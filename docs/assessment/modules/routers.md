# routers — assessed 2026-07-01

Slice: `routers/` (21 files). Scope: FastAPI HTTP surface only. `auth` helpers,
`db.py`, `api_key.py`, `event_log.py`, `observability.py`, `limiter.py`,
`worker/*`, storage and the clip/dna/preference engines are owned by other
modules and were read ONLY to verify load-bearing claims. This is a re-run of the
2026-06-24 record: every prior finding was re-traced against the current tree
(line numbers re-verified); status changes are noted inline.

## Findings

- [SEV2] routers/activity.py:57-64 — unauthenticated log-injection / 500 surface
  (STILL PRESENT). `POST /api/activity` (IP-rate-limited, no auth) splats
  client-controlled `**safe_extra` into `log_event(..., page=…, event_type=…,
  target=…, creator_id=…, **safe_extra)`. `safe_extra` caps value length + key
  COUNT but does NOT filter key NAMES, so: (a) a client `extra` key equal to an
  explicit kwarg (`page`/`event_type`/`target`/`creator_id`) → `TypeError:
  multiple values for keyword` → unhandled 500; (b) a key equal to a reserved
  `LogRecord` attribute (`message`/`args`/`name`/`module`/`levelname`/…) →
  `KeyError` inside `logging` → 500; (c) arbitrary keys become top-level
  structured-log fields (log-injection); (d) a nested dict/list value bypasses the
  `str`-only length cap. | fix: prefix every client key (`f"ui_{k}"`), allowlist
  scalar value types (`str|int|float|bool`, drop the rest), cap non-str via
  `repr()[:N]`. Add a test posting `extra={"message":"x","page":"y"}` asserting
  204, not 500.

- [SEV2] routers/activity.py:68-74 — the DB sink is passed the **unsanitized**
  full `event.extra` (`extra=event.extra`) while the file sink at :63 uses the
  capped `safe_extra`; `ActivityEvent.extra: dict[str, Any]` has no size bound and
  the route is only IP-limited (200/min) → large per-row telemetry writes / table
  bloat | fix: pass `safe_extra` here too, or add a validator capping
  `ActivityEvent.extra` key-count + serialized size.
  (needs-runtime-confirmation: whether `event_log.record_event` truncates — it is
  outside this slice; the router-level file-vs-DB inconsistency is the concrete
  defect.)

- [SEV2] routers/auth.py:400-405 — the decrypted Google refresh token is sent to
  the revoke endpoint as a **URL query parameter** (`params={"token":
  refresh_token}`), STILL PRESENT. Secrets in query strings are routinely captured
  by proxies and egress/access logs; an httpx error message embedding the request
  URL could also surface it at the `logger.warning` on :424
  (needs-runtime-confirmation for the httpx-message vector; the query-string
  exposure is structural). Token correctly read via `decrypt()` at :400. | fix:
  `data={"token": refresh_token}` +
  `headers={"Content-Type": "application/x-www-form-urlencoded"}` (form body).

- [SEV2] routers/videos.py `link_video` (commit :308) & `upload_video` (dedupe
  :398-405, commit :447) — check-then-insert with no `IntegrityError` guard (STILL
  PRESENT; `videos.py` imports no `IntegrityError`). Two concurrent same-id submits
  (double-click) both pass the SELECT; the loser violates
  `UNIQUE(creator_id, youtube_video_id)` at `commit()` → unhandled `IntegrityError`
  → raw **500** instead of the intended 409. The repo's own correct pattern is at
  improvement.py:120-134 and export.py:61-71. | fix: wrap each commit in
  `try/except IntegrityError: await session.rollback(); raise HTTPException(409,
  "Video already registered")`; add a two-concurrent-POST regression test.

- [SEV2] routers/clips.py:215-257 — `POST /videos/{id}/clips/generate` awaits
  `generate_and_rank_clips` (the clip-scoring LLM pass) **inside the
  request/response cycle** (STILL PRESENT). Every other LLM surface in the slice
  (analysis, titles, thumbnail-concepts, improvement-brief, performer-analysis) is
  202 + Celery + SSE precisely because LLM latency can exceed the LB idle timeout;
  this endpoint alone holds the HTTP connection open for the full scoring pass
  (needs-runtime-confirmation on p95 vs LB timeout). | fix: convert to the 202 +
  `TaskQueuedOut` + `aset_owner` pattern; the ranking pipeline's idempotent
  re-entry already makes a worker retry safe.

- [SEV2] routers with no `@limiter.limit` — the `Limiter` has no `default_limits`
  and `SlowAPIMiddleware` enforces only per-route decorators, so an un-decorated
  route is genuinely unthrottled (verified limiter config). Affected:
  auth.py `/callback` (:198), `/login` (:53), `/connect-publishing` (:73) —
  `/callback` is unauthenticated and does Google token-exchange + identity fetch +
  DB writes + `sync_channel_catalog.delay` on first login; the CSRF state-cookie
  check (:210-212) fast-rejects blind floods but a client holding a valid
  `cc_oauth_state` cookie can drive unbounded exchanges → Google-quota + Celery
  burn. chat.py `list_conversations` (:195), `get_messages` (:224),
  `delete_conversation` (:259), and logs.py `my_events` (:38) are authed + isolated
  but the only unthrottled DB-query endpoints in the slice (every other read is
  60–120/min). | fix: `@limiter.limit("20/minute", key_func=get_remote_address)` on
  `/callback` (+ looser on `/login`,`/connect-publishing`);
  `@limiter.limit("120/minute", key_func=creator_key)` + a `request: Request` first
  param on the four chat/logs reads (slowapi needs the Request to resolve the key).

- [SEV2] auth.py:69,301; creators.py:496,547,617,722; review.py:162 —
  `asyncio.ensure_future(record_event(...))` fire-and-forget (STILL PRESENT). The
  returned Task is never stored, so the loop keeps only a weak reference and
  CPython may garbage-collect it before completion (documented asyncio footgun →
  silently dropped telemetry under load). The lost events are exactly the
  activation/funnel signals it exists to write (`clip_kept`, `oauth_completed`,
  `identity_saved`). `record_event` itself is correct (own session, commits,
  swallows errors). | fix: `await` inline (cheap + best-effort), or
  `asyncio.create_task` with the handle held in a module-level `set()` +
  `.add_done_callback(s.discard)`.

- [SEV2] review.py:182 — `await asyncio.to_thread(retrain_preference.delay, ...)`
  enqueues a Celery retrain on EVERY feedback write (rate-limited 120/min). The
  task self-debounces, but dedup happens only after each message is dequeued, so a
  creator clicking feedback can enqueue up to 120 broker messages/min | fix:
  debounce at enqueue with a per-creator Redis `SET NX EX 60` "retrain pending"
  key. (needs-runtime-confirmation that broker churn is material at target scale.)

- [cleanup] clips.py:257 — `generate_clips` returns bare `{"clips": [...]}`, so
  `ClipListOut`'s default `state="populated"` is emitted even when the engine
  produced zero candidates — contradicts the empty-state envelope the same model
  implements on the list path (:348-352). | fix: return
  `state=build_envelope_state(len(items))` + an honest "no candidates met the
  threshold" message on empty.

- [cleanup] chat.py:195,224 — `list_conversations` / `get_messages` return bare
  `dict` with no `response_model=`, so the wire shape is undocumented in OpenAPI
  and unvalidated outbound (rubric 7). | fix: add
  `ConversationListOut`/`ConversationMessagesOut` Pydantic models (shapes stable).

- [cleanup] insights.py:169 — internal `_compute_virality_score` + the "Virality
  score" comment (:122). The wire field is correctly `performance_score` and no
  response promises virality, but the internal name dirties the no-virality
  structural grep. | fix: rename to `_compute_performance_score`.
  (RESOLVED since 2026-06-24: the hardcoded Haiku model id is gone — the endpoint
  now reads `settings.ANTHROPIC_MODEL_PERFORMER` at :593.)

- [cleanup] tasks.py:83 / :119 — `_event_stream` uses `asyncio.get_event_loop()`
  inside a running coroutine (idiomatic is `get_running_loop()`; today it returns
  the running loop with no warning, but the deprecation path is real per
  https://docs.python.org/3/library/asyncio-eventloop.html, retrieved 2026-07-01);
  `task_events` has no return annotation. | fix: `asyncio.get_running_loop()`;
  annotate `-> StreamingResponse`.

- [cleanup] notifications.py:85,131,162,181,200 — `session=Depends(get_session)`
  and helper `_get_or_create_prefs(session, …)` lack the `AsyncSession` annotation
  (rubric 6: every signature typed). Also creators.py:150
  `_upsert_style_field(creator_id: object, value: object)` and `_identity_to_dict`
  (:388) are loosely/un-typed. | fix: annotate
  `session: AsyncSession`; tighten `creator_id: uuid.UUID` and the style value type.

- [cleanup] DRY — the `task = await asyncio.to_thread(x.delay, ...)` + `aset_owner`
  try/except `RedisError` → `stream_url=None` block is copied ~13× (analysis.py
  118-140, 219-231, 295-307; clips.py 449-472, 629-644, 830-846; creators.py
  465-486, 520-537; videos.py 461-474, 552-563; auth.py 244-262; thumbnails.py
  279-297; titles.py 77-95; chat.py 98-110). | fix: extract
  `async def enqueue_with_stream(task_sig, owner_key, creator_id) -> tuple[str,
  str | None]` into a shared helper.

## Notes verified clean (not findings)

- **Per-creator isolation — NO cross-tenant leak found.** Re-traced every
  SELECT/UPDATE across all 21 files: creator-scoped queries carry
  `WHERE creator_id == creator.id`, or a `session.get` + `row.creator_id != …` 404
  ownership check before any child-table access (Transcript/Signals/VideoMetrics/
  RetentionCurve/Clip keyed by an already-verified parent id), backstopped by RLS.
  `creator_id` is always derived from the JWT-injected `Creator`
  (`get_current_creator`) or API-key-resolved `Creator`
  (`get_current_creator_via_api_key` for `/clips/ingest`) — never from the request
  body/path. Publications isolate via `_get_owned_clip` parentage + a
  `pub.creator_id != creator.id` check. Stripe webhook stamps
  `session.info["creator_id"]` (from server-set Stripe metadata) before its
  idempotency query (billing.py:231) and is idempotent on `stripe_session_id`.
  Chat gates through `_owned_conversation` before any message read. SSE streams are
  gated by the Redis owner key (tasks.py:131-138, 403 on mismatch).
- **Queue-path SSE ownership (2026-06-24 regression) FIXED** — the mission-flagged
  `videos.py` queue path (`queue_video_for_analysis`) now stamps `aset_owner` at
  :556-557 (fail-open on `RedisError`) BEFORE `start_pipeline`, matching the upload
  path; the "Queue for analysis" CTA's live progress no longer 404s.
- **Blocking-in-async** — all sync I/O offloaded via `asyncio.to_thread`: every
  `.delay()`/`start_pipeline`, `upload_file`/`delete_prefix` (boto3),
  `probe_duration_s` (ffprobe), Stripe checkout (billing.py:146), and the Anthropic
  call (insights.py:591). No `requests.`/`time.sleep`/`subprocess.run` in an async
  def; no `print()`.
- **Anthropic** — module-level `_ANTHROPIC` singleton (insights.py:462),
  `max_tokens` set, usage logged (`record_llm_tokens` + billing `record_llm_usage`)
  on every LLM path, untrusted YouTube titles wrapped (`wrap_untrusted`). The
  performer prefix's cache_control omission is documented + justified (below Haiku's
  4096-token cacheable floor).
- **Error surface** — `detail=str(exc)` sites carry only controlled domain
  `ValueError`/`CutValidationError` messages, not stack traces or DB errors. Broad
  excepts log detail/type and return generic 4xx/5xx; the OAuth callback catch-all
  logs `type(exc).__name__` only (deliberate PII guard). Status codes correct (202
  enqueue, 402 balance, 409 conflict, 413 oversize, 422 validation, 502/503 LLM).
- **Resource lifecycle** — upload paths wrap the post-tempfile block in
  `try/finally: tmp_path.unlink(missing_ok=True)` (videos.py:434, clips.py:947);
  SSE slot released in `finally` (tasks.py:113-114); sessions via DI context dep.
- **Honesty/compliance** — no virality promise in any response string or prompt;
  publications enforce the private-upload `privacy_note`; transactional email is
  structurally un-disableable (omitted from `PreferencesPatch`).

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — temp files unlinked in finally; clients singletons; SSE slot released in finally; sessions via DI |
| 2 Concurrency & scale | 6 findings — link/upload insert race→500; /clips/generate in-request LLM; ensure_future GC of telemetry; per-feedback retrain enqueue; /callback + chat/logs reads unthrottled; activity `extra` unbounded DB write |
| 3 Security & compliance | 2 findings — unauth log-injection/500 on /api/activity; refresh token in revoke query string. Per-creator isolation + RLS verified on every query; decrypt() confirmed; no token/PII in any logger line; no virality on the wire |
| 4 Clip-quality | n/a (router layer) |
| 5 Anthropic SDK | ok — singleton, tokens logged, limits set, caching-absence justified (insights.py only direct call) |
| 6 Cleanliness & typing | cleanups — generate empty-state, chat response models, virality-name grep, get_event_loop + untyped task_events, untyped session/helper params, ~13× aset_owner DRY |
| 7 Error handling / API | Pydantic on requests, safe messages, correct codes; residual 500 paths (IntegrityError race, activity `**extra`) counted under SEV2; 2 chat reads lack response_model (cleanup) |
| 8 Config & paths | ok — paths absolute; settings via pydantic-settings; hardcoded Haiku id now sourced from Settings (prior cleanup resolved) |

## Module verdict
NEEDS-WORK — no BLOCKER and no cross-tenant leak (isolation + RLS verified on every
query; the prior queue-path SSE-404 regression is fixed). A cluster of eight SEV2s
persists: unauth log-injection/500 on `/api/activity`, the Google refresh token in a
query string, the link/upload double-submit `IntegrityError`→500 race, the last
in-request LLM call on `/clips/generate`, fire-and-forget `record_event` that can
drop activation telemetry, a per-feedback retrain enqueue, unthrottled
`/auth/callback` + chat/logs reads, and an unbounded activity-`extra` DB write.
Harden these before launch.
