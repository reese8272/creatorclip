# routers — assessed 2026-06-24

Slice: `routers/` (21 files). Scope: FastAPI HTTP surface only. `auth.py`, `db.py`,
`api_key.py`, `event_log.py`, `observability.py`, `limiter.py`, `worker/*`, storage
and the clip/dna/preference engines are owned by other modules and were read ONLY to
verify load-bearing claims (RLS injection, token decrypt, blocking-I/O, `log_event`
internals, limiter config) — not assessed. Line numbers re-verified against the
current tree; the prior 2026-06-16 record had drifted.

## Findings

- [SEV2] routers/activity.py:55-62 — unauthenticated log-injection / 500 surface.
  `POST /api/activity` (IP-rate-limited, no auth) splats client-controlled
  `**safe_extra` straight into `log_event(...)`, which forwards them as the
  `logging` `extra=` dict (observability.py:230-255). No key prefixing, no
  key-name filtering. (a) a client `extra` key equal to an explicit kwarg
  (`page`/`event_type`/`target`/`creator_id`) → `TypeError: multiple values for
  keyword` → unhandled 500; (b) a key equal to a reserved `LogRecord` attribute
  (`message`, `args`, `name`, `module`, `levelname`, …) → `KeyError: Attempt to
  overwrite ... in LogRecord` inside `logging` → 500; (c) arbitrary keys become
  top-level structured-log fields = log-injection; (d) only `str` values are
  length-capped, a nested dict/list value bypasses `_MAX_STR_LEN`. | fix: prefix
  every client key (`f"ui_{k}"`), allowlist scalar value types
  (`str|int|float|bool`, drop the rest), and cap non-str via `repr()[:N]`. Add a
  test posting `extra={"message":"x","page":"y"}` asserting 204, not 500.

- [SEV2] routers/auth.py:366-369 — the decrypted Google refresh token is sent to
  the revoke endpoint as a **URL query parameter** (`params={"token":
  refresh_token}`). Secrets in query strings are routinely captured by proxies and
  egress/access logs; an httpx error whose message embeds the request URL would
  also surface it at the `logger.warning` on :388
  (needs-runtime-confirmation for the httpx-message vector; the query-string
  exposure itself is structural). Token correctly read via `decrypt()` at :364.
  Google documents revocation as a form-encoded POST body. | fix:
  `await client.post("https://oauth2.googleapis.com/revoke",
  data={"token": refresh_token},
  headers={"Content-Type": "application/x-www-form-urlencoded"})`.

- [SEV2] routers/videos.py:177-238 (`link_video`) & :316-360 (`upload_video`) —
  check-then-insert with no `IntegrityError` guard. Both SELECT for an existing
  `(creator_id, youtube_video_id)` then INSERT; two concurrent same-id submits
  (double-click) both pass the SELECT and the loser violates
  `UNIQUE(creator_id, youtube_video_id)` at `commit()` (videos.py:237 / :360) →
  unhandled `IntegrityError` → raw **500** instead of the intended 409. Verified:
  `videos.py` imports no `IntegrityError` and no `try/except` wraps either commit.
  The repo's own correct pattern is at improvement.py:115-129 and export.py:61-71.
  | fix: wrap each commit in `try/except IntegrityError: await session.rollback();
  raise HTTPException(409, "Video already registered")`; add a two-concurrent-POST
  regression test.

- [SEV2] routers/clips.py:208-247 — `POST /videos/{id}/clips/generate` awaits
  `generate_and_rank_clips` (which runs the clip-scoring LLM pass) **inside the
  request/response cycle**. Every other LLM surface in the slice (analysis, titles,
  thumbnail-concepts, improvement-brief, performer-analysis) is 202 + Celery + SSE
  precisely because LLM latency can exceed the load-balancer timeout; this endpoint
  alone holds the HTTP connection open for the full scoring pass
  (needs-runtime-confirmation on p95 duration vs the LB idle timeout). | fix:
  convert to the established 202 + `TaskQueuedOut` + `aset_owner` pattern; the
  ranking pipeline's idempotent re-entry already makes a worker retry safe.

- [SEV2] auth.py:182 + chat.py:195,224,259 — endpoints with no `@limiter.limit`.
  The `Limiter` is built with no `default_limits` and `SlowAPIMiddleware` enforces
  only per-route decorators (verified limiter.py:80, main.py:121-123), so an
  un-decorated route is genuinely unthrottled. `/auth/callback` is unauthenticated
  and does Google token-exchange + identity fetch + DB writes +
  `sync_channel_catalog.delay` on first login; the CSRF state-cookie check (:195)
  fast-rejects blind floods, but a client holding a valid `cc_oauth_state` cookie
  can drive unbounded exchanges → Google-quota + Celery burn. `chat.py`
  list/get/delete are authenticated + isolated but the only unthrottled DB-query
  endpoints in the whole slice (every other read is 120/min). | fix:
  `@limiter.limit("20/minute", key_func=get_remote_address)` on `/auth/callback`
  (+ looser on `/login`, `/connect-publishing`); `@limiter.limit("120/minute",
  key_func=creator_key)` + a `request: Request` first param on the three chat
  endpoints (slowapi needs the Request to resolve the key).

- [SEV2] auth.py:69,265; creators.py:496,547,617,722; review.py:121 —
  `asyncio.ensure_future(record_event(...))` fire-and-forget: the returned Task is
  never stored, so the loop keeps only a weak reference and CPython may
  garbage-collect it before it completes (documented asyncio footgun → silently
  dropped telemetry under load). `record_event` is otherwise correct — it opens its
  OWN sessionmaker session (event_log.py:107-126), commits, and swallows all
  exceptions, so it never touches the request's session. The loss is exactly the
  activation/funnel events it exists to write (`clip_kept`, `oauth_completed`,
  `identity_saved`). | fix: `await` it inline (it's cheap + best-effort), or use
  `asyncio.create_task` with the handle held in a module-level `set()` +
  `.add_done_callback(s.discard)` so it can't be GC'd.

- [SEV2] review.py:141 — `await asyncio.to_thread(retrain_preference.delay, ...)`
  enqueues a Celery retrain on EVERY feedback write (rate-limited 120/min). The
  task self-debounces, but dedup happens only after each message is dequeued, so a
  creator clicking through feedback can enqueue up to 120 broker messages/min.
  | fix: debounce at enqueue with a per-creator Redis `SET NX EX 60` "retrain
  pending" key, skipping `.delay()` when present. (needs-runtime-confirmation that
  broker churn is material at target scale.)

- [cleanup] clips.py:247 — `generate_clips` returns bare `{"clips": [...]}`, so
  `ClipListOut`'s default `state="populated"` is emitted even when the engine
  produced zero candidates — contradicts the empty-state envelope the same model
  implements on the list path (clips.py:344-352). | fix: return
  `state=build_envelope_state(len(items))` + an honest "no candidates met the
  threshold" message on empty.

- [cleanup] chat.py:195,224,259 — these three endpoints also lack a
  `response_model=` (return bare `dict`), so the wire shape is undocumented in
  OpenAPI and unvalidated outbound (rubric 7). | fix: add
  `ConversationListOut`/`ConversationMessagesOut` Pydantic models (shapes already
  stable).

- [cleanup] insights.py:458 — `_HAIKU_MODEL = "claude-haiku-4-5-20251001"`
  hardcoded in the router while config.py owns the model ids. | fix: add
  `ANTHROPIC_HAIKU_MODEL` to Settings + `.env.example` and read it here.

- [cleanup] insights.py:120-216 — internal symbol `_compute_virality_score` + the
  "Virality score" comment. The wire field is correctly `performance_score` and no
  response string promises virality, but the internal name dirties the no-virality
  structural grep and invites future leakage. | fix: rename to
  `_compute_performance_score`.

- [cleanup] tasks.py:117-123 / :83 — `task_events` has no return annotation; :83
  uses the deprecated `asyncio.get_event_loop()` inside a coroutine. | fix:
  annotate `-> StreamingResponse`; use `asyncio.get_running_loop()`.

- [cleanup] DRY — the
  `task = await asyncio.to_thread(x.delay, ...)` + `aset_owner` try/except
  `RedisError` → `stream_url=None` block is copied ~12× (analysis.py:119-136,
  217-224, 290-297; clips.py:414-430, 591-601, 792-802; creators.py:471-486,
  529-537; auth.py:233-246; thumbnails.py:275-288; titles.py:77-90; chat.py:103-110).
  | fix: extract `async def enqueue_with_stream(task_sig, owner_key, creator_id)
  -> tuple[str, str | None]` into routers/_schemas.py (or a new _tasks_util.py).

## Notes verified clean (not findings)

- **Per-creator isolation** — re-traced every SELECT/UPDATE: all creator-scoped
  queries carry `WHERE creator_id == creator.id`, or a `session.get` + `creator_id
  !=` ownership check before any child-table access (Transcript/Signals/
  VideoMetrics/RetentionCurve/Clip keyed by an already-verified parent id), AND are
  backstopped by RLS — `get_current_creator` (auth.py:136) /
  `get_current_creator_via_api_key` (api_key.py:116) set `session.info["creator_id"]`
  and the `after_begin` listener (db.py:139-168) emits `set_config('app.creator_id'
  ...)`. `chat_messages` has no own RLS policy by design (model note models.py:1159;
  RLS on `chat_conversations` confirmed in 0026_chat.py); every chat endpoint gates
  through `_owned_conversation` first (verified). SSE streams gated by the Redis
  owner key (tasks.py:131-138). Stripe webhook stamps `session.info["creator_id"]`
  before its idempotency query (billing.py:231) and is idempotent on
  `stripe_session_id`. **No missing-WHERE cross-tenant leak found.**
- **Blocking-in-async** — all sync I/O offloaded: `asyncio.to_thread` wraps every
  `.delay()`/`apply_async`, `start_pipeline`, `upload_file`/`delete_prefix` (boto3),
  `probe_duration_s` (ffprobe), the Stripe checkout (billing.py:146) and the
  Anthropic call (insights.py:586). `presigned_download_url` signs locally with no
  network call (verified worker/storage.py docstring) so direct async use is safe.
  No `requests.`/`time.sleep`/`subprocess.run` in any async def; no `print()`.
- **Anthropic (insights.py `analyze_performer`)** — module-level singleton
  (`_ANTHROPIC` :462), `max_tokens=256`, token usage logged (`record_llm_tokens` +
  billing `record_llm_usage`), untrusted YouTube title wrapped (`wrap_untrusted`
  :485). The prior "inert cache_control marker" finding is RESOLVED — the marker was
  removed; absence is now justified inline + DECISIONS 2026-06-16 (≈30-token system
  prefix is below Haiku's 4096-token cacheable floor).
- **Error surface** — `detail=str(exc)` at creators.py:604/705 and clips.py:783
  carry only controlled domain `ValueError`/`CutValidationError` messages, not stack
  traces or DB errors. Webhook/checkout/analysis broad-excepts log detail and return
  generic 4xx/5xx. The OAuth callback catch-all (auth.py:214-221) logs
  `type(exc).__name__` only (deliberate post-outage PII guard). Status codes correct
  (202 enqueue, 402 balance, 409 conflict, 413 oversize, 422 validation).
- **Resource lifecycle** — upload paths wrap the post-tempfile block in
  `try/finally: tmp_path.unlink(missing_ok=True)` (clips.py:879, videos.py:301); SSE
  slot released in `finally` (tasks.py:113-114); sessions via DI context manager.
- **activity.py attribution** — the prior "always anonymous" SEV2 is FIXED: now uses
  `creator_id_from_cookie(request)` (activity.py:43-46) with a narrow except,
  resolving the creator from the signed JWT without a DB round-trip.
- **Honesty/compliance** — no virality promise in any response string or prompt;
  publications enforce the private-upload `privacy_note`; `/auth/me` + `/creators/me`
  reaffirm "no virality predictions."

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — temp files unlinked in finally; clients module-level singletons; SSE slot released in finally; sessions via DI |
| 2 Concurrency & scale | 5 findings — link/upload insert race → 500 (SEV2); /clips/generate in-request LLM (SEV2, needs-runtime-confirmation); ensure_future GC of telemetry (SEV2); per-feedback retrain enqueue (SEV2); /callback + chat reads unthrottled (SEV2) |
| 3 Security & compliance | 2 findings — unauthenticated log-injection/500 on /api/activity (SEV2); refresh token in revoke query string (SEV2). Per-creator isolation + RLS verified on every query; decrypt() confirmed; no token/PII in any logger line; no virality on the wire |
| 4 Clip-quality | n/a (router layer; clip logic lives in clip_engine) |
| 5 Anthropic SDK | ok — singleton, tokens logged, limits set, caching-absence justified (insights.py only direct call) |
| 6 Cleanliness & typing | cleanups — generate empty-state, chat response models, hardcoded Haiku id, _compute_virality_score naming, task_events untyped + get_event_loop deprecated, ~12× aset_owner DRY |
| 7 Error handling / API | ok — Pydantic on requests, safe error messages, correct codes. Residual 500 paths (IntegrityError race, activity **extra) counted under SEV2 above; 3 chat endpoints lack response_model (cleanup) |
| 8 Config & paths | ok — paths absolute; settings via pydantic-settings; no new config in this slice. cleanup: hardcoded Haiku model id belongs in Settings |

## Module verdict
NEEDS-WORK — no BLOCKER and no cross-tenant leak (isolation + RLS verified on every
query), but a cluster of seven SEV2s persists: an unauthenticated log-injection/500
on `/api/activity`, the Google refresh token transported in a query string, the
link/upload double-submit `IntegrityError`→500 race, the last in-request LLM call on
`/clips/generate`, fire-and-forget `record_event` that can drop activation telemetry,
a per-feedback retrain enqueue, and unthrottled `/auth/callback` + chat read/delete
endpoints. Harden these before launch.
