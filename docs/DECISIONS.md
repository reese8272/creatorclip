# CreatorClip — Design Decisions Log

Entries are added whenever an architectural decision is made, a library is chosen, or
implementation diverges from the PRD. Every entry must include what, why, source/evidence, and date.

---

## 2026-05-30 — Issue 87: Catalog sync wiring + 180s Shorts threshold

### What was decided

Four coupled fixes for a SEV-0 onboarding bug surfaced on `reesepludwick@gmail.com`
("backboard media": 20 Shorts + 3 long-form, data-gate reporting 0/0):

1. **New `sync_channel_catalog` Celery task** that wraps the previously-uncalled
   `youtube.analytics.sync_video_catalog` (token resolution + commit + safe-fail).
2. **OAuth callback enqueues the task asynchronously** for new creators — async
   via `.delay()` so the OAuth redirect budget is never blocked by a 10–30s
   playlistItems + per-video duration fan-out.
3. **The hourly `refresh_youtube_analytics` Beat job prepends `sync_video_catalog`**
   to each creator's iteration, so new uploads land in the DB before per-video
   analytics is attempted (otherwise newly published videos stay invisible until
   the next deploy).
4. **New `POST /creators/me/catalog/sync` endpoint** (5/min, 202+task_id) wired
   into the onboarding "Refresh data status" button — the data-gate becomes a
   true sync trigger, not just a counter.

Plus two compounding fixes in the same code path:
- **`classify_video_kind` reads `settings.SHORTS_MAX_DURATION_S` (default 180)**
  to match YouTube's 2024 spec.
- **`/videos/link` and `/videos/upload` resolve `kind` + `duration_s`** from
  `get_videos_metadata` (link) / `probe_duration_s` (upload) instead of
  hardcoding `VideoKind.long`.

### Why

The user-observed symptom was a silent failure: the onboarding step 2 data-gate
counted Video rows that never existed because the only function that pulled the
uploads playlist was dead code. The fix had to (a) populate the table on
connect, (b) keep it fresh, and (c) ensure manual link/upload paths also
classify correctly so a manually-pasted Short isn't mis-bucketed as long-form.

### Industry standard checked

- **YouTube Shorts duration**: Officially raised from 60s to **180s** in
  October 2024 — confirmed from YouTube Help Center
  ([Create a Short](https://support.google.com/youtube/answer/10059070)).
  The codebase comment + `<=60s` constant predate that change.
- **Async OAuth-post-sync pattern**: Trigger initial catalog pull async right
  after token storage; refresh on schedule. Mirrors the pattern used by every
  major YouTube-data tool (TubeBuddy, VidIQ, Streams Charts). A synchronous
  catalog fetch in the OAuth callback can exceed LB / ingress timeouts on
  large channels; standard is enqueue → redirect → background sync → poll.
- **`sync_video_catalog` itself is unchanged** — it already does the right
  thing (`UNIQUE(creator_id, youtube_video_id)` keeps it idempotent across
  redeliveries; classifier handles duration → kind). The bug was that
  nothing called it.

### Alternatives ruled out

- **Sync catalog in the OAuth callback path**: would block the redirect for
  10–30s on large channels and fail under LB timeouts. Standard is enqueue +
  redirect.
- **Lazy-sync on first `/creators/me/data-gate` GET**: hides the kickoff in
  a "read" endpoint, makes rate-limit accounting weird, and races against the
  5s onboarding poll. Explicit `POST /catalog/sync` is cleaner.
- **Keep `kind=VideoKind.long` hardcoded in link/upload and "fix later"**: the
  link/upload path is the only DB-write surface other than the catalog sync;
  shipping a known data-quality bug for no reason.
- **Block on `get_videos_metadata` failure in `/videos/link` and return 502**:
  worse user experience than registering the row as long-form and letting the
  next catalog sync repair it. The fallback is observable in logs.

### Tradeoffs accepted

- **`/videos/link` fallback may briefly mis-classify a Short as long-form**
  if YT API is unreachable at link time. The next `refresh_youtube_analytics`
  tick won't fix this (the per-video sync doesn't re-classify; only the
  catalog sync does, and the catalog sync skips existing IDs). If this turns
  out to be a real problem, the catalog sync can be extended to refresh kind
  for rows where `duration_s IS NULL` — tracked under Issue 75 follow-ups.
- **Onboarding `refreshDataGate` button now costs YouTube quota** (one
  `playlistItems` + one `videos` call per click) — rate-limited at 5/min per
  creator to bound abuse.

### Source / evidence

- YouTube Help Center: [Create a Short](https://support.google.com/youtube/answer/10059070) — confirms 180s upper bound for new Shorts uploads since Oct 2024.
- `grep -rn "sync_video_catalog" .` across the entire repo: ONE hit (the definition itself, `youtube/analytics.py:179`) before this issue; zero callers confirmed by `Bash` inspection.
- Live user evidence: `reesepludwick@gmail.com` / "backboard media" — 20 Shorts + 3 videos >10 min, sync reported 0/0.

### Files

- `config.py` (`SHORTS_MAX_DURATION_S`)
- `.env.example`
- `youtube/data_api.py::classify_video_kind`
- `worker/tasks.py` (new `sync_channel_catalog` task + `_sync_channel_catalog_async`; prepended call in `_refresh_youtube_analytics_async`)
- `routers/auth.py::callback` (enqueue on new creator)
- `routers/creators.py` (`POST /me/catalog/sync`)
- `routers/videos.py` (link + upload kind resolution)
- `static/onboarding.html::refreshDataGate`
- `tests/test_catalog_sync.py` (new), `tests/test_analytics.py` (180s boundary), `tests/test_retention_tasks.py` + `tests/test_oauth_lifecycle.py` (mock `sync_video_catalog`)

### Date

2026-05-30

---

## 2026-05-30 — Issue 86: Live progress surface (SSE + Redis Streams)

### What was decided
A reusable per-task live-progress facility. Worker tasks call
`worker.progress.sync_emit / aemit(task_id, event_type, **fields)`, which writes
to a per-task Redis Stream `task:{task_id}:events`. A new authenticated FastAPI
endpoint `GET /tasks/{task_id}/events` returns `text/event-stream`, tails the
stream with `XREAD BLOCK 5000`, and forwards each entry as an SSE event the
browser consumes via `EventSource`. Wrapping `Anthropic().messages.stream(...)`
in `worker.anthropic_stream.stream_and_emit` forwards `message_start.usage`
(cache hit/miss + input tokens) → `thinking_delta` → `text_delta` →
final usage, returning `(final_text, usage_dict)` to the caller.

The seven sub-decisions:

| Sub-decision | Choice | Why |
|---|---|---|
| Transport | SSE | One-way append-only flow; every LLM provider already uses SSE; passes Cloudflare Tunnel + corporate proxies without protocol upgrade. WebSocket overkill (no client→server channel needed), long-poll laggy, HTTP/2 server push deprecated in Chrome 106. |
| Worker→web bridge | Redis Streams `XADD`/`XREAD` | Persists + replays — the page-refresh case (today's pain) just works via `Last-Event-ID`. Pub/Sub is fire-and-forget. Postgres `LISTEN/NOTIFY` has an 8 KB payload limit + no replay. Already-existing Redis singleton, zero new infrastructure. |
| Anthropic thinking | Surfaced via `content_block_delta` generic forwarding | Wrapper forwards every delta type generically, so `thinking_delta` is supported now even though the project's `anthropic==0.40.0` may not expose first-class thinking-block params yet. The `effort:`/`adaptive` migration belongs to Issue 84. |
| Cache stat reporting | Read from `message_start.usage`, not `message_delta` | Anthropic puts `cache_read_input_tokens` / `cache_creation_input_tokens` in `message_start` — confirmable BEFORE the first token, exactly what observability needs. |
| Wire format | Plain JSON-per-event + named SSE `event:` types | `EventSource.addEventListener('thinking', …)` filters natively. Vercel AI SDK Data Stream Protocol locks the frontend into the Vercel React SDK; the project's frontend is vanilla JS. |
| Late-joiner support | `XREAD` from cursor; `MAXLEN ~ 200`; `EXPIRE 3600` on terminal | EventSource's `Last-Event-ID` header auto-sent on reconnect — free replay. 200 events covers step + token traffic with buffer. 1h post-terminal TTL handles "user comes back after the build finished". |
| Security | Session-cookie auth + ownership key + per-creator concurrent cap (3) + ~12s keepalive comment + 600s hard lifetime cap | Cookies carry on `EventSource`. Ownership prevents cross-creator subscription by guessing task ids. The concurrent cap + lifetime cap close the hold-open exhaustion vector. Keepalive cadence (12s) shorter than typical TCP/proxy idle (25s) to stay alive on mobile networks. |

### Why
Today's prod incident — `build_dna` Celery task crash-looped on a
`ModuleNotFoundError` for 4 retries while the UI sat on a generic spinner
for 3+ minutes. Even on the happy path, the LLM call takes ~30 seconds with
zero user-facing signal of progress. The pattern is generic: every Celery
task in the system today has this same failure mode. Live progress is the
single biggest "feels like a real editing tool, not a generic AI website"
upgrade we can ship and is a load-bearing prerequisite for Issue 85 (UI
redesign) and a free observability win for Issue 84 (LLM efficiency audit).

### Source / evidence
- **SSE vs WebSocket**: [MDN EventSource](https://developer.mozilla.org/en-US/docs/Web/API/EventSource), [Cloudflare Agents SSE docs](https://developers.cloudflare.com/agents/api-reference/http-sse/), [cloudflared issue #199 (buffering fix)](https://github.com/cloudflare/cloudflared/issues/199).
- **Redis Streams**: [Redis XADD docs](https://redis.io/docs/latest/commands/xadd/), [Redis XREAD docs](https://redis.io/docs/latest/commands/xread/).
- **Anthropic streaming + cache stats in `message_start`**: [Anthropic streaming docs](https://platform.claude.com/docs/en/api/messages-streaming), [prompt caching docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) ("within `usage` in the response, or `message_start` event if streaming").
- **Wire format**: Vercel AI SDK Data Stream Protocol [docs](https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol) confirm the React-SDK-only consumer assumption.
- **SSE security**: per-creator concurrent cap + idle timeout is the documented production pattern; no specific CVE class, but architectural exhaustion is real for long-lived connections.

### Alternatives ruled out
- **WebSocket** — protocol upgrade that corporate/CDN configs silently fail, and we have no client→server channel need.
- **HTTP long-poll** — latency + extra requests; UI would still feel choppy.
- **Redis Pub/Sub** — fire-and-forget; page-refresh = lost progress, exactly today's pain.
- **Postgres LISTEN/NOTIFY** — 8 KB payload cap, no persistence, no replay, requires a long-lived connection per subscriber.
- **Celery built-in events (Flower-style)** — `task_prerun`/`task_postrun` are already wired in `observability.py` for the request-id correlation, but they are coarse lifecycle only; mid-task step emission is out of scope.
- **Vercel AI SDK Data Stream Protocol** — locks frontend into Vercel React SDK; we're vanilla JS by SOT decision.
- **`sse-starlette` / `asgi-correlation-id` packages** — project convention (per `observability.py`) is hand-rolled when the pattern is ~60 lines we control, no new CVE surface.

### Scope guard
DNA build is the only LLM call site wired in this issue. `improvement/brief.py`
and `clip_engine/scoring.py` get the same `emit()` calls in follow-up PRs once
we've validated the pattern on real traffic. Broader CapCut/Descript redesign
of the surrounding pages belongs to Issue 85.

### Date
2026-05-30

---

## 2026-05-30 — Container: PYTHONPATH=/app (prod DNA-stuck hotfix)

### What was decided
Set `ENV PYTHONPATH=/app` in the runtime stage of `Dockerfile`, in addition to the
existing `WORKDIR /app`. First-party packages (`dna/`, `worker/`, `youtube/`,
`ingestion/`, `clip_engine/`, `preference/`, `improvement/`, `billing/`, `routers/`,
`upload_intel/`) are now reachable from every Python process in the image regardless
of how that process is invoked.

### Why
Production incident, 2026-05-30 19:48–19:51 UTC: a user-triggered `build_dna` Celery
task crashed 4× with `ModuleNotFoundError: No module named 'dna'` and gave up,
leaving the onboarding UI stuck at "Analysing your top & bottom performers…" past
its 2-minute poll cap. Root cause: Celery is launched via the console script at
`/root/.local/bin/celery`, so Python's `sys.path[0]` becomes the script directory
— not `/app`. Celery's master prepends CWD before importing `worker.celery_app`,
so the master boots fine, but the forked pool worker that runs the task hits
`from dna.brief import generate_brief` at `worker/tasks.py:498` and the resolver
still can't find `/app/dna/`. Other lazy first-party imports (`youtube.*`,
`ingestion.*`, `clip_engine.*`, …) silently worked only because those packages
were transitively pulled in at celery boot and lived in `sys.modules`; `dna.*` was
the first to require a fresh path resolution and exposed the gap.

Setting `PYTHONPATH=/app` closes the gap structurally — every entry point sees
`/app` regardless of whether sys.path[0] is the script dir, the CWD, or empty.
WORKDIR alone is not sufficient because `''` only goes into sys.path when Python
is invoked as `python -c`, `python -m`, or with no script argument; a console
script overrides it.

### Source / evidence
- Worker logs: `docker compose -f /opt/autoclip/docker-compose.prod.yml logs --tail 150 worker` showed 4 retries of task `c3b02e43-689d-4f71-b7c0-c25f32102f52` ending in unrecoverable failure.
- In-container repro: `docker exec autoclip-worker-1 python -c "import sys; sys.path = [p for p in sys.path if p]; from dna.brief import generate_brief"` → `ModuleNotFoundError`. Adding `/app` back → succeeds.
- Process inspection: master pid 1 cmdline was `python3.12 /root/.local/bin/celery -A worker.celery_app worker …`; sys.argv[0] points at `/root/.local/bin/celery`, so `sys.path[0]` resolves to `/root/.local/bin`.
- Python docs: [The initialization of the sys.path module search path](https://docs.python.org/3/library/sys_path_init.html) — sys.path[0] is the directory of the running script; CWD is added only for `-c`, `-m`, or interactive mode.

### Alternatives considered
- **`sys.path.insert(0, "/app")` at the top of `worker/celery_app.py`.** Works
  but local to one module; doesn't help if another script-style entry point is
  added later (e.g. an alembic console-script invocation). PYTHONPATH is the
  global lever.
- **Switch the command to `python -m celery -A worker.celery_app worker`.**
  `python -m` adds CWD to sys.path. Less invasive than touching the image, but
  requires updating every compose service and any future entry; PYTHONPATH is
  one line that covers them all.
- **Install the repo as a package (`pip install -e .`).** The right long-term
  shape but a refactor — needs a real `pyproject.toml` source layout, affects
  `pytest` and mypy paths, and is out of scope for a hotfix.

### Date
2026-05-30

---

## 2026-05-30 — Issue 83: Creator Intake Form (stated identity layer)

### What was decided
Adopt a **form-driven, append-only, strictly-separated** stated-identity layer that
fuses with the inferred `creator_dna` at LLM-call time. Specifically:

1. **Form-driven over sample-text-driven.** A multi-select niche enum (1–3 of 15
   YouTube Data API categories) + required audience-summary free text + four optional
   fields (mission, content pillars, tone tags, hard-nos) + optional ~600-char style
   sample. Not a paste-3-articles voice extractor.
2. **Strictly separate from `creator_dna`.** Two tables, fused at query time, never
   merged. The clip engine + brief generator inject the identity as a stable per-creator
   system block; conflicts surface as a non-blocking profile-page nudge instead of being
   silently resolved with engagement signals.
3. **Append-only versioned storage.** Each POST creates a new row at `version = max+1`
   and stamps `superseded_at` on the prior current row. Partial unique index
   `uq_one_current_identity_per_creator` on `(creator_id) WHERE superseded_at IS NULL`
   is the DB-level guarantee. Mirrors `creator_dna`'s versioning shape.
4. **Cache placement.** Identity goes as the LAST stable system block (after the
   global instructions, before the volatile performance corpus); `cache_control` moves
   to that block. When no identity exists, the block is OMITTED entirely (not "(no
   identity)") so the cache prefix stays canonical across no-identity creators.
5. **Onboarding UX.** Inline optional card during onboarding (3 required fields + 45-s
   target) with a skip-from-step-1 affordance. Full edit + version-summary view lives
   on `static/profile.html`. Never blocks clip generation.
6. **Conflict surfacing.** A simple keyword-based detector flags "stated niche keywords
   appear in NONE of the inferred top/bottom video titles + hooks" as a profile-page
   nudge. Non-blocking; the clip engine continues to weight stated identity at full
   strength.

### Why
Two motivating problems. (1) The user observed the inferred DNA pipeline takes ~30s
end-to-end (LLM call + analytics fetch + embeddings) and ships nothing usable until
everything finishes — bad cold-start. (2) Inference can only see what has *accidentally*
performed well; it cannot see what the creator is *trying* to build. The intake gives
us both an instant cold-start signal AND a signal the inference pipeline structurally
lacks.

The strict-separation decision is load-bearing for the North Star ("the only AI editor
that truly knows your channel"). Silently overriding stated intent with engagement
signals is the YouTube-algorithm problem recreated inside our own tool. Recommender-
system research (PReF 2025, production writeups from Userpilot/LaunchNotes/Tianpan
2026) shows user satisfaction is higher when systems surface stated-vs-revealed
conflicts than when they silently re-rank by behavior.

### Industry standard checked
Read the 2026 patterns across Jasper Brand Voice, Copy.ai Brand Voice, HubSpot Breeze,
Claude Projects, ChatGPT Custom Instructions, Beehiiv/Substack/ConvertKit onboarding,
VidIQ/TubeBuddy creator personas. The convergent field set (niche + audience + content
pillars + tone tags + hard-nos + mission + sample) is exactly what every leading tool
captures. Multi-step wizards complete at **52.9% higher rate** than single-page forms
(HubSpot A/B). Forced pre-value intake is the 70%-first-session-drop-off norm —
progressive disclosure is the 2026 winner. Hybrid columns + JSONB beat single freeform
blobs for filterability and audit. Append-only versioning is rarer (Jasper/HubSpot
overwrite) but the right call for an honesty-constrained product.

### Alternatives ruled out
- **Sample-text-driven voice extraction (Jasper-style):** Duplicates the inferred-DNA
  path's job. No signal gain; adds a second LLM pass.
- **Single freeform "about you" blob (ChatGPT Custom Instructions-style):** Loses
  filterability, breaks the "Why this clip" attribution UX, blocks auditable updates.
- **Overwrite-on-update versioning:** Loses the audit trail. Storage cost of
  append-only is negligible for small identity rows.
- **Required full questionnaire at signup:** 70% drop-off norm; worse than no intake.
- **Conversational chat intake (Notion AI-style):** Higher friction for a 45-second
  task; harder to backfill for existing creators.
- **Single fused `creator_dna` table with stated + inferred merged:** Re-creates the
  engagement-bias problem we are explicitly trying to avoid.
- **Block clip generation until intake is complete:** Inferred-only mode still works;
  non-blocking intake is the high-completion-rate pattern.
- **Confidence-score/uncertainty-interval display for the conflict nudge:** Production
  evidence (HubSpot, Claude Projects) shows users find numeric uncertainty less
  actionable than qualitative framing. We use qualitative phrasing ("your stated focus
  is X but your top clips don't reflect it yet").

### Tradeoffs accepted
- **5-minute Anthropic prompt-cache TTL (2026 change).** Identity blocks rarely engage
  the cache for a creator's single isolated DNA build. The structural placement is
  still correct, and we'll capture savings any time we pipeline multiple LLM calls in
  one session (a future-Issue-84 candidate).
- **Niche-conflict detector is keyword-based, not embedding-based.** Higher precision,
  lower recall — fine for a nudge, would be wrong for a gate. Embedding-based
  detection is a Phase-3 enhancement if false-negatives become a real complaint.
- **No write to the existing `dna_pending → active` onboarding state on POST identity.**
  Identity is independent of DNA confirmation; the state machine still hangs off
  `confirm_draft`.

### Source / evidence
- 2026 industry-standard research synthesis (Jasper Brand Voice docs, Copy.ai feature
  page, HubSpot Brand Voice setup, Claude Projects guide, Anthropic Prompt Caching
  docs incl. April-2026 TTL writeup, ChatGPT Custom Instructions help center,
  Userpilot/LaunchNotes 2025 onboarding stats, MIT PReF 2025 paper, Ivy Forms
  multi-step-vs-single-step study, Tianpan 2026 cold-start writeup).
- Read `dna/builder.py`, `dna/brief.py`, `dna/profile.py`, `models.py::CreatorDna`,
  `routers/creators.py`, `static/onboarding.html`, `static/profile.html` to ground the
  design in actually-existing patterns (versioning, partial-unique idiom, system-block
  structure, dark theme).

### Files
- `alembic/versions/0012_creator_identity.py` — new table + partial unique + history index
- `models.py::CreatorIdentity`
- `youtube/categories.py` — static 15-option NICHE_OPTIONS list
- `dna/identity.py` — CRUD + `format_for_prompt` + `validate_*` helpers
- `dna/conflict.py` — niche-keyword mismatch detector
- `dna/brief.py` — `generate_brief()` accepts `stated_identity`; cache breakpoint moved
- `worker/tasks.py::_build_dna_async` — passes identity through to brief
- `routers/creators.py` — 4 new endpoints + Pydantic schemas
- `static/onboarding.html` — optional intake step 3
- `static/profile.html` — full edit + history + conflict nudge
- `tests/test_identity_unit.py` — 22 unit tests
- `tests/test_identity_integration.py` — 5 integration tests
- `docs/SOT.md`, `docs/issues.md`, `docs/PROJECT_STATE.md` — updated

### Date
2026-05-30

---

## 2026-05-30 — Reconcile merge: local-main hardening + origin Issue 78 salvage

### What changed
Two parallel timelines that had been diverging since `d5b92df` (2026-05-29) were merged into
a single `main`. Six remote feature branches (`claude/issue-78a..78g`) had been squash-merged
into `origin/main` as PRs #9–#14; in parallel, local `main` had shipped six commits hardening
the Phase-2 carry-over (Issues 38 W1, 46, 52, 56, 57, 60-RLS).

Decisions made during the reconcile (each diverging from at least one side's prior plan):

**1. Renumber local Issues 60/58/59/61 → 79/80/81/82 to avoid collision.** Both timelines
independently used the same numbers for different work — most importantly, local "Issue 60"
(Postgres RLS implementation, shipped) collided with origin "Issue 60" (personalization
loop wiring, also shipped). Local's shipped RLS work was renumbered to Issue 79; the three
local placeholder issues (58 email, 59 notifications, 61 Wave 2) → 80/81/82. References
updated across `docs/issues.md`, `docs/PROJECT_STATE.md`, `docs/DECISIONS.md`, `LEFT_OFF.md`,
`docs/DEPLOYMENT.md`, plus inline comments in `config.py`, `db.py`, `auth.py`, `alembic/env.py`,
`tests/test_rls_isolation_integration.py`, and the renamed alembic file.

**2. Rename alembic `0005_rls_policies.py` → `0010_rls_policies.py`.** Local's RLS migration
had `revision = "e5f6a7b8c9d0"` and `down_revision = "d4e5f6a7b8c9"` — the same revision id
as origin's `0005_dna_idempotency.py`. The file was renamed and re-chained to
`down_revision = "0009_improvement_briefs"` so the merged migration chain stays linear
(0001 → 0002 → 0003 → 0004 → 0005_dna_idempotency → 0006 → 0007 → 0008 → 0009 → 0010_rls_policies).
RLS lands LAST — which is also semantically correct: the policies apply to all tenant tables
already in the chain, including the new `improvement_briefs` table from 0009.

**3. Drop local's selective-DELETE in `generate_and_rank_clips` in favor of origin's
idempotency early-return.** Local Issue 46's fix narrowed the DELETE WHERE to exclude
`done`/`running` rows; origin Issue 61's fix added a top-of-function check that returns the
existing clips unchanged if any exist. Origin's guarantee is strictly stronger — under it,
the local DELETE block is unreachable — so the local block was removed. Both files'
original intent (no late retry orphans rendered clips) is preserved.

**4. Adopt origin's 10-day + `final` poll bound; supersede local's 30-day floor.** Local
Issue 46 added a 30-day `Clip.created_at` floor to `_poll_clip_outcomes_async`. Origin
Issue 70 added a tighter 10-day cap (the measurement lifecycle is 48h + 7d) plus a
`ClipOutcome.final.is_(False)` filter. Origin's bound is strictly tighter and structurally
correct (the lifecycle is the right measure, not the preference-signal staleness). The
30-day reasoning becomes moot.

**5. Worker tasks keep `db.AdminSessionLocal()` even with origin's advisory-lock additions
in `_build_dna_async`.** Origin Issue 76 added a `pg_advisory_xact_lock` + double-checked
idempotency on `job_id` to close the DNA build double-spend race. Local Issue 79 switched
worker tasks from `AsyncSessionLocal` → `AdminSessionLocal` (RLS bypass for cross-tenant
sweeps). Both apply in the merged version: `AdminSessionLocal` for the role, advisory lock
for the race.

**6. `dna/embeddings.py` keeps local's `_aembed` wrapper.** Both sides offloaded the
sync Voyage SDK to a thread — origin did it inline at every call site, local introduced
the `_aembed` helper. The helper is DRY-er and the merged version uses it; origin's
inline timing comment was folded into the helper's docstring.

**7. `_render_clip_async` uses origin's `setup_start_s`-preferred render start, but on
locally-snapshotted values.** Origin Issue 59's `_render_start_for(clip)` helper computes
`setup_start_s if not None else start_s`; local Issue 38 W1 snapshots timing fields into
locals before closing the session to avoid implicit refresh. Merged version snapshots
`setup_start_s` AND `start_s` into locals, then inlines the same conditional.

**8. `db.py` admin engine inherits `connect_args=_CONNECT_ARGS`.** Origin Issue 58 added
`prepare_threshold=None` to the app engine (PgBouncer transaction-pooling incompatibility);
local Issue 79 added a new admin engine. Merged version passes the same `connect_args`
to the admin engine so it's safe under future PgBouncer too.

### Why
Both branches were doing real, shipped work. A "drop one timeline" resolution would have
permanently deleted either the RLS migration + worker async refactor (if local lost) or the
Issue 78a–g + AutoClip rebrand + production-assessment work (if origin lost). Preserving
both via merge + targeted renumbering keeps every commit attributable and every issue
traceable. The fact-of-the-matter for the four code conflicts (poll bound, generate_clips
delete, _aembed, render start) is that origin's later iterations were strictly stronger in
each case — the merge honors that.

### Source / evidence
- `git merge-base main origin/main` → `d5b92df` (2026-05-29)
- `git log origin/main..main` → 6 local commits about Issues 38 W1, 46, 52, 56, 57, 60-RLS
- `git log main..origin/main` → 44 origin commits about Issues 76, 77, 78a-g, beta launch, etc.
- `gh pr list --state merged` → PRs #9–#14 confirmed squash-merged
- Audited `docs/issues.md` for issue-number collisions before reconcile (only 58/59/60/61
  were ambiguous; 38/46/52/56/57 were the same issue tracked on both branches with the
  local timeline ahead on completion status).
- Safety tag preserved: `safety/pre-reconcile-2026-05-30` points at local main pre-merge.

### Files
This entry; the merge commit itself; the renumber prep commit (`7bcc224`).

### Date
2026-05-30

---

## 2026-05-30 — Issue 78c: mypy 30 → 0 + ratchet enabled

### What changed
Took the mypy gate from 30 errors to 0 and turned on `disallow_untyped_defs` +
`disallow_incomplete_defs` (the pyproject comment's promised ratchet). Baseline
`docs/assessment/baselines.json` `mypy_errors` ratcheted 30 → 0.

### How (three honest buckets)
- **Plugin (−9):** enabled `pydantic.mypy` in `[tool.mypy].plugins`. The 9 `config.py`
  `call-arg` errors were spurious — mypy doesn't understand `BaseSettings` env-var
  population without the plugin (the documented fix).
- **Real type fixes (−12):** `preference/train.py` — a loop variable `w` (a float from
  `sample_weight`) shadowed the later `w = np.array(...)`; renamed the loop var to `weight`
  and gave `X`/`y`/`w` explicit `np.ndarray` annotations. `youtube/oauth.py` — replaced
  `if is_new:` with `if creator is None:` so mypy narrows `Creator | None → Creator` in the
  else branch and the return. `worker/tasks.py` — added an explicit `if video.source_uri is
  None: continue` before `delete_file` (the query already filters non-null; the guard makes
  it type-sound). `preference/model.py` — removed two now-unused `# type: ignore[assignment]`.
- **Targeted `# type: ignore[...]` for third-party stub lag (−9):** `anthropic` 0.40's
  `TextBlockParam`/`ToolParam` stubs predate the `cache_control` field and server-tool
  (`{type, name}`) shape we send (`clip_engine/scoring.py`, `dna/brief.py`,
  `improvement/brief.py`); `redis.asyncio`'s `eval` is typed with a `str` union
  (`youtube/quota.py`, `youtube/oauth.py`); `cv2.data` and slowapi's exception-handler
  signature are unstubbed (`clip_engine/render.py`, `main.py`). All are runtime-correct,
  tested code; each ignore carries a code + an "SDK/stub typing lag" comment, and
  `warn_unused_ignores=true` keeps them honest (a stale one becomes an error).

### Why not bump the anthropic SDK instead
Upgrading `anthropic` past 0.40 would refresh the stubs but is a dependency change with its
own behavior + pip-audit/version-pin review — out of scope for a typing-only PR. Targeted
ignores are the documented mypy way to handle incomplete third-party stubs and carry zero
runtime risk. (Deferred as a possible future cleanup.)

### Correction
The earlier `OFF_COURSE_BUGS.md` entry claiming the Layer-0 mypy gate aborts on a
non-existent `knowledge/` source was a **misdiagnosis** and has been withdrawn: `gate_mypy()`
calls `_sources()` which filters non-existent paths, so the gate always reported the true
count. The bogus `mypy=1` came from a raw manual mypy run with the unfiltered candidate list.

### Evidence
Plain `mypy` over the gate sources → **0** under the committed (gradual) config; ruff 0 +
format clean; full suite **431 passed, 1 skipped**; integration **66 passed**. All 11 edited
files `py_compile`-clean. (Note: the `run_layer0.py --gates mypy` harness emits noisy/garbled
counts locally — the authoritative measure is plain `mypy` + the CI `Types` job, both 0.)

---

## 2026-05-30 — Issue 78d: Improvement-brief → 202 + poll (async Celery)

### What changed
`GET /creators/me/improvement-brief` built a creator-scoped analytics summary then ran the
~120s Claude + web_search call inline via `asyncio.to_thread` (offloaded from the loop in
Issue 66, but still on the request path). Converted to a 202 + poll flow:
- New `ImprovementBrief` model + `improvement_brief_status` enum (`pending`/`ready`/`failed`),
  one row per creator, `creator_id` indexed; migration `0009_improvement_briefs`.
- `POST /me/improvement-brief` → 202, `@limiter.limit("10/hour")`: cheap creator-scoped guards
  (channel connected; has VideoMetrics — Issue-33-safe), **debounces** an in-flight `pending`
  build, get-or-creates + resets the row, enqueues `generate_improvement_brief`, stores
  `job_id`. `GET` now returns the stored row (`status`/`brief`/`requested_at`/`completed_at`/
  `error`), HTTP 200 always (`none` when absent) — a cheap poll target at 120/min.
- Worker task `generate_improvement_brief` + `_generate_improvement_brief_async(job_id,
  creator_id)`: builds the analytics dict (moved out of the router) + DNA brief, calls the
  unchanged `improvement/brief.py` function via `asyncio.to_thread`, writes `brief_text` +
  `ready`. Idempotent (no-op on redelivery once `ready` for the same `job_id`) and safe-fail
  (`failed` + a generic message — never a stack trace / token / PII).
- `static/insights.html` `loadBrief()` rewritten to POST → poll every 3s until `ready`/`failed`.

### Why
A ~120s synchronous request can exceed a load-balancer / ingress timeout, returning a 5xx to
the user even though the work would have finished. Moving it behind a 202 + poll removes the
request-path time bound; the durable row also survives a worker restart and lets the UI show
honest progress.

### Why this design (industry standard)
Mirrors the existing **DNA-build 202 + poll precedent** (`routers/creators.py::build_dna` +
`worker/tasks.py::_build_dna_async`) — same status-row idempotency, `task.delay`, and Celery
at-least-once handling — so the codebase has one consistent long-job pattern rather than two.
202 + a poll endpoint is the standard REST shape for a long-running, non-cacheable job kicked
off by a client (vs. holding the connection open or a websocket, which the LB-timeout problem
rules out). The status enum + one-row-per-creator + `job_id` idempotency key matches CreatorDNA.

### Evidence / tests
+8 integration tests (`tests/test_improvement_brief_async.py`): 202 + pending row; debounce;
GET none→ready; safe-fail with no exception text leaked; per-creator isolation via the task;
idempotent redelivery. Three pre-existing GET-based isolation/offload tests
(`test_improvement_isolation.py`, `test_isolation.py`, `test_event_loop_offload_integration.py`)
rebased onto the task path; `test_rate_limiting.py` updated (the 10/hour LLM cap moved from GET
to POST). Default suite **425 passed, 1 skipped**; integration **66 passed**; ruff 0; mypy 30
(= baseline, none in 78d files); migration `0009` up/down/up clean.

---

---

## 2026-05-30 — Issue 78b: Clip-scorer prompt caching (1h TTL) + stable-first ordering

### What changed
`clip_engine/scoring.py` built a single system block `[intro][CREATOR DNA: {dna_brief}]
[principles]` with a default-TTL (`{"type": "ephemeral"}`, 5 min) cache breakpoint. Split it
into two system blocks — a static `[intro][principles]` block first, then a per-creator
`CREATOR DNA:\n{dna_brief}` block carrying `{"type": "ephemeral", "ttl": "1h"}`. The volatile
per-video candidates already live in the (uncached) user message and are unchanged.

### Why
The DNA brief is identical across a creator's videos but the candidates differ per video, so
the brief is the natural cached prefix. The default 5-minute TTL only helps videos scored
within 5 minutes of each other; a creator's batch (channel connect → many videos ingested and
scored over a longer span) falls outside that window. The 1h TTL widens the reuse window so
those repeat scorings read the cached prefix (~0.1× input price) instead of re-billing it.

### Why this design (industry standard, verified via `/claude-api`)
- **1h TTL syntax is `{"type": "ephemeral", "ttl": "1h"}` with no beta header** — extended
  cache TTL is GA (the `/claude-api` prompt-caching reference shows it directly on
  `messages.create`). Economics: a 1h write costs 2× vs 1.25× for 5-min, so it needs ≥3 reads
  to pay off (vs 2) — fine for a creator with several videos.
- **Stable-content-first ordering** is the documented caching best practice (any byte change
  invalidates the rest of the prefix; volatile content goes after the last breakpoint). Static
  instructions now lead; the per-creator brief carries the breakpoint; candidates stay last.
- **Honest scope note:** the minimum cacheable prefix is model-dependent — **2048 tokens on
  Sonnet 4.6** (`settings.ANTHROPIC_MODEL`). The static block alone (~400 tokens) is below the
  floor, so it can never cache cross-creator on its own; only the `[static + DNA brief]`
  per-creator prefix (DNA briefs are large) clears it. The static-first reorder is therefore
  correct structure + future-proofing (a global breakpoint becomes useful only if the static
  block ever grows past the floor), and the present, measurable win is the 1h TTL. This
  refines the Issue 69 note, which framed the reorder as a cross-creator share. The existing
  `logger.info` already logs `cache_read/cache_creation` tokens, so cache engagement is
  verifiable in production.

### Evidence / tests
Updated `test_score_candidates_dna_uses_prompt_caching` to the two-block contract: static
block leads and is not the breakpoint (no `cache_control`, holds the principles, no DNA); the
last block carries `{"type":"ephemeral","ttl":"1h"}` and holds the DNA brief. Full suite **430
passed, 1 skipped**; clip-quality eval **6 passed**; gates ruff 0 / mypy 30 (= baseline; the
lone `scoring.py` `cache_control` TypedDict error is the pre-existing SDK-stub false positive,
shared with `dna/brief.py` + `improvement/brief.py`).
## 2026-05-30 — Issue 78a: Per-(creator, version) preference-scorer cache

### What changed
`preference.train.load_latest` deserialized the joblib model blob on **every** rerank
(`clip_engine/ranking.py` calls it per clip-generation pass), each time taking the
process-global `_UNPICKLER_LOCK` in `PreferenceScorer.from_bytes` — so reranks serialized
against each other on the worker. Added `preference/_scorer_cache.py`: a per-worker bounded
LRU (`OrderedDict` + `threading.Lock`) keyed by `(creator_id, version)`. `load_latest` now
issues a cheap query for the latest `version` + `feature_schema_jsonb` only, returns the
cached scorer on a hit, and fetches the blob + `from_bytes` once on a miss. Bound via new
`PREFERENCE_SCORER_CACHE_SIZE` (default 128).

### Why
The deserialize is the only lock-contended step on the personalization hot path and it
repeated needlessly. Memoizing on `(creator_id, version)` removes both the redundant blob
fetch and the lock acquisition when the model is unchanged.

### Why this design (industry standard)
Per-process bounded cache of deserialized ML artifacts, keyed by an **immutable version**
and relying on **monotonic versioning** for invalidation, is the standard memoization
pattern. `train.py` assigns `max(version)+1` on every retrain, so a new model is a new key
and the stale entry simply ages out by LRU — no manual busting, no stale-read window. A TTL
cache was rejected (stale-read risk + redundant reloads); `functools.lru_cache` was rejected
(doesn't fit the async lookup and can't key cheaply on the live version); caching the raw
blob was rejected (skips the DB fetch but still pays the lock-contended `from_bytes`, which
is the actual cost). Hand-rolled rather than adding `cachetools`, consistent with the
zero-new-dependency choice made for the observability layer.

### Evidence / tests
5 DB-free unit tests (`tests/test_preference_scorer_cache.py`): same-version deserializes
once, new version reloads (no stale model), feature-drift returns `None` before any
fetch/deserialize, no-model returns `None`, LRU eviction bound holds. Full suite **430
passed, 1 skipped**; gates ruff 0 / mypy 30 (= baseline) / coverage ≥ floor.

---

## 2026-05-29 — Issue 75(f): Observability (correlation ids + structured logs + metrics)

### What changed
New `observability.py` wires three things, integrated in `main.py` (API) and
`worker/celery_app.py` (worker):
- **Correlation id** — a `request_id_ctx: ContextVar[str]`; a pure-ASGI
  `RequestIDMiddleware` (added last → outermost) reads an inbound
  `REQUEST_ID_HEADER` (default `X-Request-ID`) or mints a UUID4, binds it, and
  echoes it on the response. A `RequestIDLogFilter` injects `request_id` onto every
  log record.
- **Structured logs** — `JsonLogFormatter` emits one JSON object per line (incl.
  `request_id` + any `extra` fields); `configure_logging(json_logs=settings.LOG_JSON)`
  replaces `logging.basicConfig`, idempotent, text fallback for local dev.
- **Golden signals** — `prometheus-client==0.25.0`: `http_request_duration_seconds`
  (latency + traffic/errors via the `_count` by status, labelled by route template
  to bound cardinality) recorded in the same middleware; `celery_task_duration_seconds`
  + `celery_tasks_total` recorded via task signals; exposed at `/metrics`
  (gated by `METRICS_ENABLED`, `include_in_schema=False`).
- **Celery propagation** — `before_task_publish` stamps the id onto task headers;
  `task_prerun` binds it (+ starts the task timer); `task_postrun` records the task
  metrics and clears the id. Connected with `weak=False`.

### Why
Assessment axis G was the last ⚠️ blocking *operability*: no request id meant a
failed render couldn't be traced API→worker, and there were no golden signals for
p99 / error rate. This is a pre-deploy operational gate, not polish.

### Decisions / deviations
- **Hand-rolled correlation layer, not `asgi-correlation-id`.** Same documented
  pattern (ContextVar + ASGI middleware + echo header + logging filter + Celery
  signals) in ~60 lines we own, adding **zero** new dependency/CVE surface right
  after ratcheting pip-audit to 0. One new dep (prometheus-client, the canonical
  metrics client) is justified; a second for ~60 lines is not.
- **Prometheus metrics now; OpenTelemetry tracing deferred.** Full OTel needs a
  collector to operate; golden-signals-before-launch is the standard MVP and is
  k8s-native (our deploy target). Distributed tracing is a tracked follow-up.
- **`weak=False` on the Celery signal connects** — Celery connects receivers weakly
  by default, so module-level handlers held by no other ref would be GC'd and never
  fire (caught by a failing test before it shipped).
- **Pure-ASGI middleware, not `BaseHTTPMiddleware`** — avoids the known
  streaming/background-task pitfalls; reads `scope["route"]` in the `finally` (set by
  the inner router by then) so the latency label is the route template.

### Industry standard checked (live, 2026-05-29)
The de-facto pattern across `snok/asgi-correlation-id`, `django-structlog`'s Celery
integration, and current FastAPI observability guides: ContextVar + ASGI middleware
+ echo-header + logging filter, Celery signal propagation, Prometheus for metrics,
OTel for tracing as a later layer. Sources in the session research.

### Verification
Full suite **410 passed, 1 skipped, 55 deselected** (+9 DB-free observability tests:
id validation/mint, log-filter injection, JSON format, idempotent config, mint+echo
+ inbound-respect via TestClient, `/metrics` exposition, Celery publish→run→clear +
task-counter increment). Gates unchanged: ruff 0 / mypy 30 / bandit 0,0 / pip_audit 0.

---

## 2026-05-29 — Issue 75(a): pip-audit CVE remediation (14 → 0)

### What changed
Patched every CVE with a fix in our compatible range; pinned in `requirements.txt`:
- **cryptography** 43.0.3 → **46.0.7** — OpenSSL secadv (GHSA-79v4-65xg-pq4g), EC
  subgroup check (GHSA-r6ph-v2qm-q3c2), DNS name-constraint (PYSEC-2026-35), and the
  46.0.6-only PYSEC-2026-36 found after the first bump.
- **python-multipart** 0.0.20 → **0.0.27** — path-traversal + 2 DoS.
- **PyJWT** 2.9.0 → **2.12.0** — `crit`-header validation bypass (PYSEC-2026-120). The
  disputed PYSEC-2025-183 ("weak encryption") dropped off entirely: it was scoped to
  2.10.1 and 2.12.0 is outside its affected range.
- **lightgbm** 4.5.0 → **4.6.0** — RCE (PYSEC-2024-231).
- **python-dotenv** 1.0.1 → **1.2.2** — symlink-follow file overwrite.
- **starlette** 0.41.3 → **0.49.1** (the newest under FastAPI 0.120.x's `<0.50.0`
  pin) — multipart-blocks-the-loop (GHSA-2c2j-9gv5-cj73) + Range-header quadratic DoS
  (GHSA-7f5h-v6xp-fcq8). Required bumping **FastAPI** 0.115.4 → **0.120.4**, the
  smallest bump whose starlette pin admits 0.49.1.

The gate (`run_layer0.py:gate_pip_audit`) now passes a curated `--ignore-vuln`
allowlist (`PIP_AUDIT_IGNORES`); baseline `pip_audit_vulns` ratcheted **14 → 0**.

### Accepted-risk (2 residuals, in `PIP_AUDIT_IGNORES`)
- **pytest GHSA-6w46-j5rx-g56g** — local `/tmp/pytest-of-*` predictable-name
  priv/DoS. Fixed only in pytest 9, but `pytest-asyncio==0.24.0` caps `pytest<9`, so
  it's a test-stack cascade, not a runtime exposure (dev/CI only). Lift when the test
  stack is bumped as a unit.
- **starlette PYSEC-2026-161** — Host-header path injection, fixed only in starlette
  **1.0.1**, which needs FastAPI 0.136.x (the documented `on_startup/on_shutdown`
  1.x landmine). The advisory itself notes routing matches on the *actual* path; we
  also sit behind Cloudflare + locked `ALLOWED_ORIGINS`. Tracked as a starlette-1.x
  migration follow-up under Issue 75.

### Why these chosen versions / why not literal-0 without ignores
Going to starlette 1.x / FastAPI 0.136 to close the last starlette CVE is a
major-line jump with a documented breakage surface — out of scope for a CVE-patch
task. The standard posture for a `pip-audit` CI gate is patch-to-nearest-fix plus a
*justified* ignore-list for no-fix/disputed/major-line-only advisories, kept in
lockstep with this entry. Verified each fix version and the FastAPI↔starlette pin
coupling against live PyPI metadata, not memory.

### Verification
`pip check` clean; full suite **401 passed, 1 skipped, 55 deselected** on the bumped
deps (auth/crypto/upload/preference/lifespan all green); `run_layer0.py` reports
`pip_audit 0`, no other gate regression (ruff 0, mypy 30, bandit 0/0). PyJWT 2.12
emits an `InsecureKeyLengthWarning` only on a short-key test fixture — production
uses a full-length configured secret.

---

## 2026-05-29 — Batch 8 (Issues 73 + 74 + 75): input/memory/config hardening

### What changed
- **74:** `ingestion/audio.py` loads at `sr=16000` (≈3× less memory than the native
  rate); `ingestion/transcribe.py` caches the WhisperX model + align model
  (`lru_cache`) and makes the Deepgram client + AssemblyAI key module-level singletons.
- **73:** `routers/videos.py` validates `youtube_video_id` against `^[A-Za-z0-9_-]{11}$`
  (422) on `/link` and `/upload`, before it reaches a storage key.
- **75:** `config.py` fails fast in production when Stripe secrets are unset;
  `upload_intel/timing.py` skips out-of-range `day_of_week`/`hour` rows instead of
  `IndexError`→500.

### Why
Memory: the librosa full-rate decode was the dominant OOM vector under concurrency.
Security: an unvalidated `youtube_video_id` could reshape the R2 object key (`../`).
Robustness: a missing Stripe secret should fail at boot, not at first webhook; one
bad activity row shouldn't 500 the upload-intel endpoint.

### Scope decisions (honest deferrals, tracked in Issue 75)
- **Full `response_model` coverage (73)** is mechanical hygiene across ~16 endpoints
  with no security/correctness risk; rushing accurate schemas for every dict in one
  commit risks runtime 500s. Deferred to Issue 75. The *security* part (input
  validation) shipped here.
- **Deepgram file-stream (74)** deferred: the deepgram SDK isn't installed in this
  environment to verify the streaming API, and `sr=16000` already removes the main
  memory vector.
- **CVE triage, analytics-retention cadence, observability, mypy→0** are each
  research/infra efforts, not single commits — enumerated in Issue 75.

---

## 2026-05-29 — Issue 71 (Batch 7): Preference hardening

### What changed
- `preference/model.py`: the `from_bytes` joblib-global swap is now guarded by a
  module `threading.Lock`. `predict_score` validates feature count against
  `n_features_in_` and raises on mismatch (no more silent `0.5`).
- `preference/train.py`: `build_and_save` takes `pg_advisory_xact_lock(hashtext(creator_id))`
  before the `max(version)+1` read; `load_latest` returns `None` when the stored
  `feature_schema_jsonb` differs from `FEATURE_NAMES` (schema drift → DNA fallback).
- `clip_engine/ranking.py`: `rerank_with_preference` scores all clips first; if the
  scorer raises, it keeps the DNA ranking untouched (honest fallback).

### Why
The monkeypatch was not thread-safe — a concurrent load could restore the
unrestricted unpickler mid-load, defeating the RCE allowlist exactly when a
tampered blob is read. `max()+1` raced into a UNIQUE violation under concurrent
retrains. `predict_score`'s blanket `0.5` let a broken/drifted model silently move
rankings.

### Decision: lock over direct unpickler
The finding suggested instantiating `_RestrictedUnpickler` directly to avoid the
global swap. Verified empirically that joblib's `NumpyUnpickler.__init__` signature
is version-dependent (this joblib requires an `ensure_native_byte_order` arg), so
direct instantiation is brittle across upgrades. A module-level `threading.Lock`
around the existing swap is version-proof and fully thread-safe — the accepted
alternative in the finding. Serialization cost is negligible (loads are rare; a
per-(creator,version) scorer cache is the tracked optimization under Issue 75).

---

## 2026-05-29 — Issue 70 (Batch 6): Bound poll_clip_outcomes

### What changed
- Migration `0007`: `clip_outcomes.final BOOLEAN NOT NULL DEFAULT FALSE` + a partial
  index on `fetched_at WHERE final=false AND published_youtube_id IS NOT NULL`.
- `_poll_clip_outcomes_async`: query excludes `final IS TRUE` and caps candidates to
  `Clip.created_at >= now-10d`; the 7d-checkpoint poll sets `final=True`; commits per
  creator.

### Why
The `fetched_at < cutoff_7d` branch had no terminal guard, so every published clip
re-qualified for a quota-costing re-poll every 7 days forever — an unbounded drain
that would eventually starve the daily analytics refresh (axes E/F). One session was
also held across the whole N×M network loop.

### Decision
`final` terminal marker is the primary fix; the 10-day created-at cap is
defense-in-depth so the scan is bounded even before `final` propagates to legacy
rows (which self-finalize on their next 7d poll — no backfill needed). Per-creator
commit bounds the transaction/connection hold to one creator's network calls.

---

## 2026-05-29 — Issue 69 (Batch 5): Prompt-cache split + web_search extraction

### What changed
- `dna/brief.py` and `improvement/brief.py`: `system` is now two blocks — a static
  instruction block carrying `cache_control: ephemeral`, then a separate uncached
  block holding the per-creator corpus/analytics.
- `improvement/brief.py` returns `text_blocks[-1].text` (final answer after the
  last web_search `tool_use`), not `text_blocks[0]` (the preamble). `dna/brief.py`
  uses `[-1]` for consistency.
- Corrected misleading docstrings (the DNA brief does not share a cache with the
  clip scorer — separate prompts never share a cache entry).

### Why
The volatile data was interpolated into the cached block, so the prefix changed
every call (the assessment's "~0% hit"). The `improvement` extraction bug returned
the model's "let me search…" preamble instead of the synthesised brief.

### Finding: caching can't engage at this prompt size (the real correction)
Per the `/claude-api` skill, the **minimum cacheable prefix is 2048 tokens on
Sonnet 4.6** (4096 on Opus); below it the cache silently no-ops. Both static
instruction blocks are ~350-450 tokens — far below the floor — and both calls are
low-frequency (DNA build once per build; improvement 10/hour), so there is no
repeated-prefix-within-window to cache either. **The split is the correct
structure but is NOT a cost win for these two endpoints.** The acceptance
criterion "cache_read_input_tokens non-zero after warmup" was therefore replaced
with a structural assertion (volatile data is out of the cached block).

### Follow-up (Issue 75)
The genuine caching beneficiary is `clip_engine/scoring.py`: a large per-creator
prefix (DNA brief + the 11 principles) reused across all of a creator's videos in
a window. Splitting static/volatile + `cache_control` there, with a prefix above
the 2048-token floor, is where caching actually pays off.

### Standard checked
`/claude-api` prompt-caching: stable-prefix-first, breakpoint on the last stable
block, volatile after; minimum cacheable prefix 2048 (Sonnet 4.6) / 4096 (Opus);
web_search interleaves text/tool_use — take the final text block.

---

## 2026-05-29 — Issue 72 (Batch 4b): Shared YouTube HTTP client + 5xx backoff

### What changed
- New `youtube/_http.py`: lazy per-process singleton `httpx.AsyncClient`
  (`Timeout(15, connect=5)`) + `aclose()`. All three OAuth helpers, `_get_json`,
  and `_fetch_report` reuse it. `aclose()` wired into the API lifespan
  (`main.py`) and worker shutdown (`worker/celery_app.py`).
- `_get_json`/`_fetch_report` retry transient 5xx with jittered backoff.

### Why
Per-call `httpx.AsyncClient()` with no timeout on the token-refresh hot path could
hang a request/worker indefinitely if Google stalls; per-call construction also
defeats connection pooling under the analytics fan-out (axes B/E).

### Decisions / standard checked
- **Lazy singleton** (not import-time) so the connection pool binds to the loop
  that first uses it — the API app loop and the worker's post-fork singleton loop
  (Issue 39) are different; lazy avoids a cross-loop binding bug.
- httpx guidance: reuse one `AsyncClient` for pooling; always set timeouts. 5xx on
  idempotent GETs → backoff+retry (axis E).

### Test-isolation note
The existing `test_oauth_lifecycle` `_get_json`/`_fetch_report` tests mocked
`httpx.AsyncClient` directly; rebased them onto the new `youtube._http.client`
boundary. (The per-test event loop + a module singleton is the same class of
hazard as Issue 39 — but in tests every patch targets `_http.client`, and the one
test that builds the real client `aclose()`s it, so nothing leaks across loops.)

---

## 2026-05-29 — Issue 68 (Batch 4b): Worker-loop offload + transcription timeout

### What changed
- `dna/embeddings.py`: both `_embed` (Voyage) calls run via `await asyncio.to_thread`.
- `worker/tasks.py`: `generate_brief` and `extract_audio_events` offloaded via
  `asyncio.to_thread`; `transcribe_audio` via
  `asyncio.wait_for(asyncio.to_thread(...), timeout=settings.TRANSCRIPTION_TIMEOUT_S)`.
- `config.py`/`.env.example`: `TRANSCRIPTION_TIMEOUT_S` (default 300).

### Why
These sync calls ran on the worker's Issue-39 singleton event loop (bounded by
prefork concurrency today, fragile to any pool change), and transcription had no
upper bound — a hung provider stalled the worker indefinitely (axis E).

### Decision: wait_for as the job-level bound; SDK-native timeouts deferred
`wait_for(to_thread(...))` guarantees the *job* fails (→ Celery retry) after the
timeout and keeps the loop free. It cannot kill the worker thread, which lives
until the SDK call returns; the Deepgram/AssemblyAI SDKs aren't installed in this
environment to verify their native timeout params, so SDK-level timeouts are a
tracked follow-up (Issue 75). Voyage already self-bounds (`timeout=30`).

### Standard checked
FastAPI/asyncio: offload blocking/CPU work with `asyncio.to_thread`; never let a
sync SDK + retry-sleep run on the event loop. (Batch-0 load-testing research.)

---

## 2026-05-29 — Batch 4a (Issues 66 + 67): Blocking calls off the API event loop

### What changed
- `routers/improvement.py`: the 120s Anthropic+web_search brief now runs via
  `await asyncio.to_thread(generate_improvement_brief, ...)`.
- `routers/videos.py`: the R2/disk `upload_file` write now runs via
  `await asyncio.to_thread(...)`.
- `routers/auth.py`: `delete_account`'s `delete_prefix` purge now runs via
  `await asyncio.to_thread(...)`.

### Why
Each was a synchronous call inside an `async def` handler — on FastAPI's
single-threaded event loop, one in-flight call stalled every other concurrent
request on that worker (axis B; "p99 issues come from sync calls hidden in async
paths"). `asyncio.to_thread` moves the blocking work to a threadpool so the loop
stays responsive.

### Decision: to_thread now, Celery+poll later (Issue 66)
`to_thread` fully fixes the loop-blocking SEV-1. It does NOT shorten the request
(the brief can still take 120s, which may exceed a production LB/gateway timeout).
The robust UX is a Celery 202/poll job (like `build_dna`), but that needs result
storage + a poll endpoint + frontend work — tracked under Issue 75 rather than
ballooning this batch. The upload/delete offloads are unambiguously correct as
`to_thread` (the work must finish before responding; it just must not hold the loop).

### Standard checked
FastAPI guidance: never run blocking/CPU/sync-I/O directly in an `async def` path;
offload via `asyncio.to_thread` or a task queue. (Confirmed in the load-testing
research from Batch 0.)

### Testing
Integration tests (`tests/test_event_loop_offload_integration.py`) assert the
offloaded callable is recorded through an `asyncio.to_thread` shim — external
services mocked, DB real.

---

## 2026-05-29 — Batch 3 (Issue 65): pgvector HNSW + FK index

### What changed
- Migration `0006`: `ix_dna_embeddings_hnsw` (HNSW, `vector_cosine_ops`,
  `m=16, ef_construction=200`) on `dna_embeddings.embedding`, and
  `ix_clip_feedback_creator_id` on `clip_feedback.creator_id`. Both built
  `CREATE INDEX CONCURRENTLY` inside `op.get_context().autocommit_block()`.

### Why
The embedding similarity query (`<=>`, cosine) was an unindexed O(rows) scan;
`clip_feedback.creator_id` was an unindexed FK on the (now hot, post-Issue-60)
training + retrain-debounce path.

### Decisions / standard checked
- **HNSW over IVFFlat**: HNSW is the recommended default for <10M vectors with
  active writes; IVFFlat's k-means clustering is data-dependent and must NOT live
  in a migration. `m=16, ef_construction=200` is the documented better-recall
  starting point (defaults 16/64). Op class `vector_cosine_ops` matches the `<=>`
  query (voyage-3.5 vectors). Sources: pgvector index-selection guides
  (medium.com/@philmcc…), AWS pgvector indexing deep-dive.
- **CONCURRENTLY + autocommit_block**: `CREATE INDEX CONCURRENTLY` can't run in
  Alembic's default transaction; the autocommit block keeps the build online-safe.

### Scope correction (assessment was imprecise)
- `dna_embeddings.creator_id` already has a btree index (`ix_dna_embeddings_creator_id`,
  migration 0001).
- `preference_models.creator_id` is already covered by the `(creator_id, version)`
  unique-constraint index (leading column serves `WHERE creator_id ORDER BY version`).
- So no redundant `creator_id` indexes were added — only the HNSW index and the
  genuinely missing `clip_feedback.creator_id`.

---

## 2026-05-29 — Batch 2 (Issues 63 + 64): Idempotent unique-keyed writes

### What changed
- `billing/ledger.py`: `grant_minutes` is now self-idempotent — fast-path
  existence check (keyed grants) + `begin_nested()` SAVEPOINT + `flush()` +
  `IntegrityError` catch, mirroring `deduct_for_video`.
- `dna/profile.py`: `create_draft` accepts `build_job_id`; `confirm_draft` locks the
  creator's DNA rows `with_for_update()`, supersedes-before-promotes with an explicit
  `flush()`, and catches `IntegrityError`.
- `worker/tasks.py`: `build_dna` passes `self.request.id`; `_build_dna_async`
  early-returns before the paid LLM/Voyage calls when a draft for that job_id exists.
- Migration `0005`: `creator_dna.build_job_id` (nullable) + index, and partial unique
  index `uq_one_confirmed_dna_per_creator ON creator_dna(creator_id) WHERE status='confirmed'`.

### Why
At-least-once delivery + concurrent Stripe/worker delivery duplicated money records,
re-spent paid Anthropic/Voyage calls (duplicate DNA drafts), and could leave two
`confirmed` DNA rows. The idempotency key for DNA builds is the Celery `task_id`
(stable across redelivery, new per user re-request); for grants it is
`stripe_session_id` (UNIQUE).

### Standard / precedent
In-repo precedent `deduct_for_video` (UNIQUE + SAVEPOINT + IntegrityError); Celery
idempotency (Batch 1 research). Partial unique index is non-deferrable, hence the
ordered flush (supersede → flush → promote) so two 'confirmed' rows never coexist
even transiently.

### Coverage baseline moved: 69.97% → 69.54% (justified)
These three fixes are DB-mutating logic. The project rule (CLAUDE.md Testing Rules)
forbids mocking the DB, so they are covered by integration tests
(`test_dna_idempotency_integration.py`, `test_billing_grant_idempotency_integration.py`)
which run in the integration CI, not the unit-coverage gate. Their unit-invisible
lines lowered the unit-only floor. Per the README ratchet + Phase-4 rule, the floor
moves to current reality (69.54%) with this justification; it climbs back as
unit-coverable code lands in later batches.

---

## 2026-05-29 — Batch 1 (Issues 61 + 62): Worker at-least-once safety

### What changed
- `clip_engine/ranking.py`: `generate_and_rank_clips` is now idempotent — it
  early-returns the existing clips (rank order) when any exist for the video,
  instead of `delete(Clip)` + reinsert.
- `worker/celery_app.py`: added `task_reject_on_worker_lost=True`,
  `task_soft_time_limit=3000`, `task_time_limit=3300`, and
  `broker_transport_options={"visibility_timeout": 3600}`.
- `worker/tasks.py`: `_render_clip_async` early-returns when the clip is already
  `render_status==done` with a `render_uri`.

### Why
Celery delivers at-least-once. With `acks_late` and cascade-delete on
`Clip.feedback`/`Clip.outcome`, a redelivered `build_signals`→`generate_clips`
silently wiped a creator's feedback labels and outcomes (data loss; and post-Issue-60
the preference training signal). `acks_late` without `reject_on_worker_lost` also
dropped tasks whose worker was OOM-killed, and with no time limit a task exceeding
the broker visibility timeout was redelivered while still running (double execution).

### Decisions / standard checked
- Pair `task_acks_late` with `task_reject_on_worker_lost`; the **invariant
  soft < hard time_limit < visibility_timeout** ensures a task is killed before
  Redis redelivers a running copy. Assume tasks run twice → design idempotent.
  Sources: francoisvoron.com/blog/configure-celery-for-reliable-delivery;
  dev.to "Celery + Redis at Scale"; celery/celery#5935.
- **Idempotency strategy = skip-if-exists** (KISS) over "replace only pending
  zero-feedback clips" — there is no regenerate trigger today, and skip-if-exists
  can never wipe feedback. The finer-grained replacement is noted as a future
  enhancement if a re-generate feature is ever added.

### Caveat
`task_time_limit=3300` (55m) covers normal media jobs; a very long source on CPU
WhisperX could exceed it → use the hosted transcription backend or add a per-task
`time_limit` override. Documented here rather than guessed at a larger global ceiling.

---

## 2026-05-29 — Issue 60: Wire the personalization loop + maturity-gated blend

### What changed
- `clip_engine/ranking.py`: `generate_and_rank_clips` now calls
  `rerank_with_preference` after persisting (and re-commits the blended score/rank).
- `preference/model.py`: new `preference_weight(label_count)` — the rerank blend
  weight. `rerank_with_preference` uses `(1-w)*dna + w*pref` instead of fixed 50/50.
- `worker/tasks.py`: new idempotent, self-debouncing `retrain_preference(creator_id)`
  Celery task (no-op when no trainable feedback since the latest model version).
- `routers/review.py`: enqueues `retrain_preference` after each feedback write.
- `config.py`/`.env.example`: `PREFERENCE_WEIGHT_CAP` (default 0.5).
- `preference/train.py`: exposed `TRAINABLE_ACTIONS` (DRY for the debounce filter).

### Why
Two subagents independently found personalization was dead code — never trained,
never applied. This is half the North Star ("learns your style, adapts as you
evolve"). The flat 50/50 blend also gave a 2-label cold-start model equal authority
over ranking, violating the CLAUDE.md honesty rule ("below the threshold, ranking
falls back to DNA + signals").

### Decisions
- **Blend curve:** weight 0 below `PERSONALIZATION_THRESHOLD_LABELS` (honest DNA
  fallback), linear ramp to `PREFERENCE_WEIGHT_CAP` by 2× the threshold. This is the
  standard hybrid cold-start strategy — start content-based, grow personalization as
  the creator's own feedback accumulates. `label_count` already lives on
  `PreferenceScorer`, so no migration. Sources: hybrid/cold-start recommender
  practice — expressanalytics.com/blog/cold-start-problem; arxiv 1808.10664.
- **Retrain trigger:** enqueue-on-feedback (responsive, matches "adapts as you
  evolve") with an in-task new-labels guard, over a Beat-only cadence (laggy).
  Repeated clicks collapse to cheap no-ops.

### Scope boundaries (deferred, tracked)
- `build_and_save` version-race (`max()+1` → IntegrityError) and unpickler
  thread-safety → **Issue 71**; the retrain task catches `IntegrityError` as a
  minimal guard meanwhile.
- `from_bytes` runs sync on the worker loop (bounded by prefork) → **Issues 68/71**.

---

## 2026-05-29 — Issue 59: Render from setup_start_s + ffmpeg accurate-seek finding

### What changed
- `worker/tasks.py` renders from `setup_start_s` via a new pure helper
  `_render_start_for(clip)` (coalesces to `start_s` only when the nullable
  `setup_start_s` is unset), instead of the fixed peak−window `start_s`.
- `clip_engine/render.py` sets `-accurate_seek` explicitly before `-i`.
- Tests: DB-free unit guards for `_render_start_for` + the seek flag, plus an
  end-to-end integration test.

### Why
The render cut from `start_s` (fixed peak−75s) while scoring, the API response,
and the eval all key on `setup_start_s` — so the delivered Short did not actually
"clip the setup" (CLIPPING_PRINCIPLE #2), the product's core differentiator.

### Finding: the assessment's "inaccurate seek" SEV-2 was a false positive
`clip_engine.md` flagged `-ss` before `-i` as drifting up to one GOP. That is true
for **stream copy**, but this pipeline **re-encodes with libx264**, and ffmpeg
applies `accurate_seek` by default when encoding — so the existing cut was already
frame-accurate. We set `-accurate_seek` explicitly as a self-documenting guard (a
no-op today) so the cut stays accurate if anyone later switches to `-c copy`. We
did NOT restructure to output-seek (`-ss` after `-i`), which decodes from 0 and
could blow the render timeout for clips deep in a long source.
Source: ffmpeg seek semantics — `-noaccurate_seek` "only applies when encoding"
(github.com/mifi/lossless-cut/pull/13 discussion); accurate seek is the default
when transcoding.

### Note
`setup_start_s` is a nullable column; the coalesce keeps legacy/edge clips
rendering a valid range rather than passing `None` to ffmpeg.

---

## 2026-05-29 — Issue 58: psycopg3 prepared statements + pool sizing for PgBouncer

### What changed
- `db.py:_make_engine()` now passes `connect_args={"prepare_threshold": None}` to
  `create_async_engine`, disabling psycopg3 server-side prepared statements.
- Pool ceiling lowered from `pool_size=10, max_overflow=20` (30/pod) to
  `pool_size=15, max_overflow=5` (20/pod) to stay under the 25-conn PgBouncer
  sidecar; added `pool_recycle=1800`. Values are module constants
  (`_CONNECT_ARGS`, `_POOL_SIZE`, `_MAX_OVERFLOW`, `_POOL_RECYCLE_S`) for testability.
- `docs/DEPLOYMENT.md` records the connection-budget inequality.
- `tests/test_db_engine_config.py` introspects the engine to guard all three.

### Why
psycopg3 auto-prepares a statement after its 5th execution. PgBouncer in
transaction-pooling mode (the chosen production pooler, `DEPLOYMENT.md`) reuses
server connections across clients, so the prepared statement vanishes on the next
checkout → `prepared statement "_pg3_…" does not exist`. CI never catches this
because it connects to Postgres directly. The per-pod pool (30) also exceeded the
25-conn sidecar, causing checkout timeouts at p99 under load.

### Source / evidence
- psycopg3 docs: *"Unless a pooling middleware explicitly declares otherwise…
  disable prepared statements by setting `Connection.prepare_threshold` to `None`."*
  (psycopg.org/psycopg3 — prepared statements).
- SQLAlchemy issue #6467 / discussion #10246 (pooler + prepared-statement handling).

### Alternatives ruled out
- **Rely on PgBouncer ≥1.22 + psycopg ≥3.2 named-prepared support:** couples
  correctness to exact infra versions; a downgrade silently reintroduces the outage.
- **Session-mode pooling:** defeats pooling benefit at hundreds of clients.
- **Drop PgBouncer:** contradicts the documented 10k-scale K8s target.

### Verification status
Code complete + unit-tested. The green-under-load proof (no `prepared statement`
errors at target concurrency) is deferred to a `tests/perf/` Locust run behind a
real PgBouncer in staging — not reproducible in the CI/dev container.

---

## 2026-05-29 — Skill freshness convention + standards SSOT

### What changed
- Created a committed `best-practices` skill (`.claude/skills/best-practices/SKILL.md`)
  to replace the phantom `/best-practices` that CLAUDE.md mandated but did not exist
  on disk. It is process-first/evergreen: it operationalizes the One Rule
  (research current standard live, record in DECISIONS) rather than listing
  perishable "current best" facts.
- Added a freshness convention (`docs/SKILL_FRESHNESS.md`): every `SKILL.md`
  carries `last_verified: YYYY-MM-DD`; a 6th `freshness` gate in `run_layer0.py`
  flags any skill unverified for >90 days (warn-only by default, hard fail under
  `--require-fresh` for the scheduled re-verification job). Added freshness
  (warn-only) to the CI static-gates job.
- Hoisted the Anthropic model id + web_search tool version to `config.py`
  (`ANTHROPIC_MODEL`, `ANTHROPIC_WEB_SEARCH_TOOL`), referenced by all 4 call sites
  (`clip_engine/scoring.py`, `dna/brief.py`, `improvement/brief.py`) instead of
  three hardcoded duplicates; added both to `.env.example`. Closes the
  hardcoded-model-id SEV2 from the assessment.

### Why
A standards skill that bakes perishable facts (model ids, tool/lib versions,
"best library for X") goes stale silently and then gives confident wrong answers —
worse than no skill. The mitigation is to encode *process* (how to find the
current standard) as evergreen, fetch perishable facts live where possible
(pip-audit pulls current CVEs; web_search researches per decision), store the
must-store perishable facts in a single source (config/requirements), and make
staleness a visible CI signal via `last_verified` + the freshness gate. Full
rationale in `docs/SKILL_FRESHNESS.md`.

### Alternatives ruled out
- **Baking the current model id / library recommendations into the skill prose:**
  the exact rot this avoids; the improvement assessment caught `claude-sonnet-4-6`
  duplicated across three files.
- **A hard staleness gate that fails all PRs after 90 days:** would block unrelated
  work; warn-by-default + `--require-fresh` on the scheduled job is the cadence-
  correct posture.
- **Cloning the Claude API surface into a repo skill:** that surface moves fastest;
  delegate to the Anthropic-managed `/claude-api` skill that updates upstream.

---

## 2026-05-29 — Production-assessment harness + quality gates

### What changed
- Added a committed project skill `.claude/skills/production-assessment/`
  (`SKILL.md` + `rubric.md` + `scale-checklist.md` + `subagent-contract.md` +
  `report-template.md` + `scripts/run_layer0.py`) and a `/assess` slash command.
- Added four ratcheted CI gates in `.github/workflows/quality.yml`, all driven by
  the single `run_layer0.py` harness: **mypy** (types), **pytest-cov** (coverage
  floor), **bandit** (SAST), **pip-audit** (dependency CVEs).
- Added `requirements-dev.txt` (pinned), `[tool.mypy|coverage|bandit]` config in
  `pyproject.toml`, `docs/assessment/` register (baselines + per-module findings +
  report history), and a Locust load-test scaffold in `tests/perf/`.
- Un-ignored `.claude/skills/` and `.claude/commands/` in `.gitignore` (session
  state stays ignored; intentional skills/commands are now committed).
- Added one line to the CLAUDE.md Phase-4 checklist requiring the Layer-0 gates
  to be green before an issue closes.

### Why
Assessing a codebase aimed at hundreds of concurrent users needs to be (a)
exhaustive, (b) repeatable, and (c) bounded in context as the repo grows. A
single full-codebase Claude sweep satisfies none of these — it is
non-deterministic, unrepeatable, and its recall drops as the repo grows. The
governing split is **tools provide exhaustiveness; Claude provides judgment**:
deterministic gates run in CI with perfect recall at zero context cost, and the
model is reserved for per-module judgment via parallel subagents that write
findings to disk, so the orchestrator reads short summaries rather than source.
This keeps assessment context flat from 16k LOC upward.

### Tool choices (industry standard checked, 2026)
- **Type checker: mypy** over pyright/ty. `ty` only reached FastAPI in 3/2026 —
  too new for a load-bearing gate; pyright needs Node in CI. mypy is pip-native
  and mypyc-compiled builds are fast. Sources: pydevtools.com type-checker
  comparison; "Migrating from mypy to ty" (FastAPI).
- **SAST: bandit** — AST-based, Python-specific, ~88% issue recall, <5s scans.
  Semgrep (92%, semantic) noted as a future add; heavier and needs rule curation.
  Source: dev.to "Semgrep vs Bandit (2026)".
- **Dependency CVEs: pip-audit** over safety (safety now gates behind an account).
  Critical/high CVEs to be fixed within 7 days. Source: aikido.dev Python tools.
- **Coverage: pytest-cov as a self-baselining ratchet** (regression gate, not an
  absolute bar) so it doesn't red-wall 16k existing LOC. Mutation testing via
  **mutmut** (most-active tool; target score 75%→85%) is cadence-only because it
  is slow. Source: johal.in mutmut 2026; ieeexplore mutation-tool comparison.
- **Load testing: Locust** over k6 — Python-first, reuses the project's JWT/auth
  scheme and is maintained in-language. k6 documented as the alternative for
  >10k RPS/Grafana streaming. Source: dev.to "Best Load Testing Tools 2025".

### Ratchet posture
Gates are seeded permissively in `docs/assessment/baselines.json` and only fail
on regression; `run_layer0.py --update-baseline` captures current reality, then
the targets are tightened over time (bandit_high→0, pip_audit_vulns→0,
mypy_errors→0 then enable `disallow_untyped_defs`). Rationale and steps in
`docs/assessment/README.md`.

### Alternatives ruled out
- **One big Claude sweep**: non-deterministic, unrepeatable, recall degrades with
  size, context blows up — the exact failure mode this design avoids.
- **Strict gates from day one** (mypy --strict, 90% coverage): would block every
  PR against existing code; the ratchet reaches the same end state without a
  flag-day rewrite.
- **Folding the full assessment into every issue's Phase 4**: too heavy for
  day-to-day; instead only the cheap Layer-0 floor-check is per-issue, and the
  deep sweep is the milestone-cadence `/assess`.

---

## 2026-05-28 — Issue 79: Postgres RLS implementation per Issue 56 decision

### What was built
Implements the Issue 56 adopt-now decision. New alembic revision
`0010_rls_policies` creates roles, grants, and policies:

- **Roles**: `creatorclip_app` (LOGIN, no BYPASSRLS — the application
  connects as this) and `creatorclip_migrate` (LOGIN, BYPASSRLS granted out
  of band — alembic and Celery worker tasks connect as this). Both are
  created idempotently inside `DO $$ ... $$` blocks.
- **Grants**: `creatorclip_app` gets `USAGE` on `schema public` and
  `SELECT, INSERT, UPDATE, DELETE` on all tables + `USAGE, SELECT` on all
  sequences. `ALTER DEFAULT PRIVILEGES` extends the same grants to future
  tables created in `public` so we don't lose access after the next
  migration.
- **Policies** on 12 tables (every table with a direct `creator_id`
  column): `videos`, `audience_activity`, `demographics`, `youtube_tokens`,
  `creator_dna`, `dna_embeddings`, `clips`, `clip_feedback`,
  `preference_models`, `minute_packs`, `minute_deductions`, `usage`. Each
  policy is `USING (creator_id = current_setting('app.creator_id',
  true)::uuid) WITH CHECK (...)`. Both `ENABLE` and `FORCE ROW LEVEL
  SECURITY` are applied so the table owner cannot bypass.

Application wiring (Issue 79 code changes):

- `config.py`: new optional `DATABASE_MIGRATION_URL` env var (falls back to
  `DATABASE_URL` for single-role dev/CI).
- `db.py`: two engines / sessionmakers — `engine` + `AsyncSessionLocal`
  (app role, used by FastAPI request path) and `admin_engine` +
  `AdminSessionLocal` (migration role, used by Celery worker tasks).
  Registers a global `after_begin` listener on the `Session` class that
  emits `SET LOCAL app.creator_id = :cid` from `session.info["creator_id"]`
  when present.
- `auth.py:get_current_creator`: after resolving the Creator from the JWT,
  attaches `creator.id` to `session.info["creator_id"]`. The bootstrap
  Creator lookup runs cleanly because the `creators` table is exempt from
  RLS.
- `worker/tasks.py`: every `db.AsyncSessionLocal()` site switched to
  `db.AdminSessionLocal()` (16 call sites). Worker tasks are trusted
  internal code that performs cross-tenant sweeps; the admin role bypass
  is the correct shape.
- `alembic/env.py`: uses `settings.database_migration_url`.

Tests:

- `tests/test_retention_tasks.py` and `tests/test_oauth_lifecycle.py`:
  patches of `db.AsyncSessionLocal` switched to `db.AdminSessionLocal`
  (only worker-task tests were affected).
- New `tests/test_rls_isolation_integration.py` (marker: `integration`):
  seeds Creator A + Creator B with one row per tenant table each, then
  opens a transaction, issues `SET LOCAL ROLE creatorclip_app` + `SET LOCAL
  app.creator_id = :A`, and asserts that an unfiltered `SELECT creator_id
  FROM <each tenant table>` returns zero rows owned by B. A second test
  asserts the `creators` table remains visible to the app role with no GUC
  set, validating the auth-bootstrap exemption.

Operations runbook in `docs/DEPLOYMENT.md` covers the one-time prod ops:
`ALTER ROLE creatorclip_migrate BYPASSRLS`, role passwords, table ownership
transfer to `creatorclip_migrate`, and the two-URL env update.

### Why
Implements the Issue 56 decision without re-deliberating. See that
DECISIONS entry for the rationale; this entry documents the chosen
implementation shape.

### Two minor decisions surfaced during implementation

**1. JWT-to-creator bootstrap via `creators` table exemption.** The auth
dependency must look up Creator by JWT `sub` before `app.creator_id` is set.
Option B from the CHECK brief (pre-parse JWT in middleware → request.state)
was ruled out as heavier than needed. Option A (rely on the existing
`creators`-table RLS exemption) works because the `creators` table has no
policy — the bootstrap SELECT runs without a gate, then `auth.py` attaches
the resolved id to `session.info` so every subsequent transaction in the
request emits SET LOCAL via the listener.

**2. Test fixture role strategy.** Existing integration tests use
`settings.DATABASE_URL` to create their own engines for setup/teardown.
Rather than touching ~15 test files, the strategy is: dev / CI Postgres
connects as a SUPERUSER (which bypasses RLS regardless of FORCE), and the
new RLS-guarantee tests use `SET LOCAL ROLE creatorclip_app` within a
transaction to assume the non-BYPASSRLS role for the visibility assertion.
This keeps existing tests untouched and makes the RLS guarantee
independently verifiable.

### Mutation rowcount audit (AC carry-over)

Issue 56's acceptance criteria included "every UPDATE/DELETE on tenant
tables checks rowcount and raises 404 on 0". The audit found:

- Routers: only two `session.execute(update/delete)` calls outside the
  ORM session pattern (`routers/billing.py:154` updating `creators`,
  `routers/auth.py:204` deleting `creator`). Both target the `creators`
  table, which is exempt from RLS — no rowcount-zero failure mode.
- All other router mutations go through ORM `session.get(Model, id)` →
  mutate → commit. Under RLS, `session.get` returns `None` for rows the
  current creator cannot see → the existing `if not video: raise 404`
  pattern is the rowcount guard.
- Worker tasks (the one bulk UPDATE in `_purge_stale_source_media_async`)
  run via `AdminSessionLocal` and bypass RLS — no failure mode there.

The audit AC is therefore satisfied by construction. If a future change
introduces a router-side bulk UPDATE/DELETE on tenant tables, the
rowcount-zero check must be added at the call site; this is documented
in the runbook.

### Alternatives ruled out (Issue 79-specific)
- **Drop FORCE RLS to make dev/CI Just Work**: would let the table owner
  bypass policies — defeats the purpose. The chosen role-assumption test
  strategy keeps FORCE on without needing to change CI.
- **Bypass-flag policy pattern** (`OR current_setting('app.bypass_rls',
  true) = 'on'`): rejected per Issue 56 — industry-standard is BYPASSRLS
  role, not in-policy bypass logic.
- **Worker tasks with per-creator `SET LOCAL`** (instead of admin role):
  would require restructuring every Celery task to scope to one creator.
  `purge_stale_source_media` and `poll_clip_outcomes` are inherently
  cross-tenant; the admin role + BYPASSRLS is the correct shape for those.
  Per-creator scoping in workers is a possible future hardening if we
  ever need to defend against compromised worker code.

### Tradeoffs
- **First-deploy ops burden**: the runbook requires SUPERUSER access to
  prod Postgres for one-time `ALTER ROLE BYPASSRLS` + ownership transfer.
  Documented but unavoidable.
- **Child tables not yet covered**: `video_metrics`, `retention_curves`,
  `transcripts`, `signals`, `clip_outcomes` reach tenant via FK to a
  policy-protected parent. Per Issue 56, this is acceptable for now; a
  raw `SELECT * FROM signals` in a future code path would bypass the
  parent policy. Flagged for future hardening.
- **Mutation rowcount audit**: the AC is satisfied by construction today
  but the codebase pattern (`session.get → mutate → commit`) is not
  enforced — a future bulk `session.execute(update(...))` on a tenant
  table would silently 0-row under RLS without raising 404. A static check
  could be added but is overkill for current surface.

### Source / evidence
Same sources as Issue 56's DECISIONS entry (Crunchy Data, pganalyze,
Bytebase footguns, SQLAlchemy 2.0 docs + discussion #10469, Microsoft
Azure multi-tenant guidance). Re-validated against the actual codebase:

- Read `auth.py:31-47` to confirm the bootstrap query shape and apply the
  exemption-based fix.
- Read `models.py` to enumerate every direct `creator_id` column (12,
  matches Issue 56's count exactly).
- Read every router for mutation patterns; confirmed two raw mutations on
  the exempt `creators` table.

### Files
- `alembic/versions/0010_rls_policies.py` — new migration.
- `config.py` — new `DATABASE_MIGRATION_URL` + `database_migration_url`
  property with fallback.
- `db.py` — admin engine/sessionmaker; `after_begin` listener.
- `auth.py:get_current_creator` — `session.info["creator_id"]` injection.
- `worker/tasks.py` — 16 `db.AsyncSessionLocal()` → `db.AdminSessionLocal()`
  replacements.
- `alembic/env.py` — uses migration URL.
- `tests/test_retention_tasks.py` — patches updated to
  `db.AdminSessionLocal`.
- `tests/test_oauth_lifecycle.py` — patch updated to
  `db.AdminSessionLocal`.
- `tests/test_rls_isolation_integration.py` — new file: 2 tests
  (cross-tenant leak block + creators-table exemption).
- `docs/DEPLOYMENT.md` — RLS one-time setup runbook.
- `docs/SECRETS.md` — `DATABASE_MIGRATION_URL` row added.

### Date
2026-05-28

---

## 2026-05-28 — Issue 56: Postgres Row-Level Security — adopt now

### What was decided
**Adopt Postgres RLS as the defense-in-depth layer underneath the existing
application-level always-filter for every tenant-owned table.** The
implementation lands in a separate issue (filed as **Issue 79**); this entry
closes the Issue 56 "research-and-decide" deliverable.

### Why
Application-layer filtering is the foundation but is a linting problem
disguised as a security property — it depends on every developer, every PR,
every query author, forever, never forgetting `WHERE creator_id = :id`. We
already had one SEV-0 leak (Issue 33) where the filter was missed and
cross-creator analytics flowed into a Claude prompt. RLS converts the
guarantee from "every query author must remember" into a structural property
of the database: the row never leaves Postgres for the wrong tenant, even
when application code forgets the WHERE.

We are about to enter Google OAuth verification (Phase 3) where auditable
multi-tenant isolation posture is load-bearing for approval; the right
time to pay the implementation cost is before public launch, not during a
post-launch incident.

### Implementation sketch (for Issue 79)

**Tables needing CREATE POLICY** — every table with a direct `creator_id`
column, 12 in total: `videos`, `audience_activity`, `demographics`,
`creator_dna`, `dna_embeddings`, `clips`, `clip_feedback`,
`preference_models`, `minute_packs`, `minute_deductions`, `usage`,
`youtube_tokens`. Child-only tables (`video_metrics`, `retention_curves`,
`transcripts`, `signals`, `clip_outcomes`) reach tenant via FK to a parent
that already has a policy; explicit policies on them are belt-and-suspenders
and can land in a follow-up if a query path ever bypasses the parent join.
`creators` and `audit_log` are explicitly exempt (self-identifying;
append-only ops log).

**Role split** — application connects as `creatorclip_app` (no `BYPASSRLS`,
not the table owner). Alembic migrations connect as `creatorclip_migrate`
with `ALTER ROLE creatorclip_migrate BYPASSRLS`. Adds a new
`DATABASE_MIGRATION_URL` env var alongside the existing `DATABASE_URL`.
Without this split the app role would bypass policies as the owner,
defeating the entire mechanism.

**`SET LOCAL app.creator_id` injection** — register an SQLAlchemy
`after_begin` event listener on the `Session` class that calls
`connection.execute(text("SET LOCAL app.creator_id = :id"), {"id": str(creator_id)})`
inside every transaction. Source the creator UUID from the existing FastAPI
auth dependency (`current_creator`). The `after_begin` hook fires
per-transaction, matching `SET LOCAL`'s transaction scope: when the
transaction commits or rolls back, the GUC disappears and the next
transaction on a recycled pool connection starts clean.

**`FORCE ROW LEVEL SECURITY`** — apply to every policy-covered table in
the migration. By default Postgres lets the table *owner* bypass RLS
regardless of policies; `FORCE` closes that gap.

**Issue 48 isolation test extension** — for every existing isolation test,
add a "with RLS active, an unfiltered `SELECT *` returns zero rows for
non-current creator" assertion. This converts the test suite from "the
application filtered correctly" into "the database refused to leak even
without the application filter" — exactly the property RLS is purchased to
provide.

### pgbouncer-future answer (pinned)
We do not run pgbouncer today. When we add it:
- **Transaction pooling**: SAFE. `SET LOCAL` is scoped to the transaction
  and cleared on commit, so the next request on a recycled connection
  starts clean.
- **Statement pooling**: UNSAFE. pgbouncer can hand off mid-transaction
  to a different connection, leaking the GUC across tenants.
- **Session pooling**: SAFE but loses most of pgbouncer's benefit.

Decision: when we add pgbouncer, configure transaction pooling only. This
is the industry-standard pairing for RLS-enabled stacks.

### Alternatives ruled out
- **Defer to production-scale**: would tolerate Issue-33-class regressions
  until launch. The Issue 33 leak motivated this issue. Deferring is not
  defensible given that history.
- **Decline (rely on application filter only)**: leaves the bug class
  structurally open. Even with the Issue 48 isolation test suite (which is
  excellent for what it tests), nothing prevents the next missed filter from
  shipping.
- **Connection `checkout` pool event for SET LOCAL**: fires too early —
  the tenant UUID is not yet in scope at pool-checkout time. Use
  `after_begin` per Crunchy Data + SQLAlchemy 2.0 guidance.
- **Per-tenant Postgres schema**: a tenant-per-schema approach is the
  alternative defense-in-depth pattern. It scales poorly past a few
  hundred tenants (`pg_class` bloat; introspection cost) and adds heavy
  migration complexity. Not the right shape for a B2C-leaning SaaS.

### Tradeoffs
- **Open question on child tables**: child-only tables (`video_metrics`,
  etc.) are reachable through parent tables that DO have policies, so
  application JOINs naturally filter them. The Issue 56 spec says "every
  table with a `creator_id` column" — honored literally; child tables get
  RLS in a future hardening if a query ever bypasses the parent join.
- **Silent UPDATE/DELETE failures**: with RLS, a mutation touching a row
  the current tenant doesn't own returns 0 rows affected with no error.
  Mutation paths must check rowcount and raise 404 rather than silently
  succeeding. Issue 79 implementation must audit every mutation path.
- **pgvector ANN index queries on `dna_embeddings`**: RLS policies are
  evaluated post-index-scan, so cross-tenant embeddings could briefly
  appear in ANN candidates before filtering. For current scale (closed
  beta, few hundred rows per creator) this is correctness-and-performance
  neutral; revisit at scale.
- **Migration role lockdown**: requires SSH access to the prod Postgres
  to grant `BYPASSRLS` to the migration role one time. Captured in
  `docs/DEPLOYMENT.md` for Issue 79.

### Source / evidence (RLS pattern + pgbouncer compatibility)
- Crunchy Data — Row Level Security for Tenants in Postgres:
  https://www.crunchydata.com/blog/row-level-security-for-tenants-in-postgres
- pganalyze — Using Postgres Row-Level Security in Ruby on Rails (pgbouncer
  transaction-mode compatibility):
  https://pganalyze.com/blog/postgres-row-level-security-ruby-rails
- Daniel Imfeld — PostgreSQL Row Level Security notes (pgbouncer
  statement-vs-transaction pooling):
  https://imfeld.dev/notes/postgresql_row_level_security
- Bytebase — Postgres RLS Footguns (FORCE RLS, owner bypass, silent
  failures): https://www.bytebase.com/blog/postgres-row-level-security-footguns/
- SQLAlchemy 2.0 Async I/O docs (sync_engine event listener pattern):
  https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
- SQLAlchemy Discussion #10469 (after_begin requires `connection.execute`,
  not `session.execute`, since 2.0.17):
  https://github.com/sqlalchemy/sqlalchemy/discussions/10469
- techbuddies.io — PostgreSQL RLS for Multi-Tenant SaaS:
  https://www.techbuddies.io/2026/02/04/how-to-implement-postgresql-row-level-security-for-multi-tenant-saas-2/
- Microsoft Azure Architecture — Postgres in Multi-Tenant Solutions:
  https://learn.microsoft.com/en-us/azure/architecture/guide/multitenant/service/postgresql
- Thenile — Shipping multi-tenant SaaS using Postgres RLS:
  https://www.thenile.dev/blog/multi-tenant-rls

### Files (this issue, decision-only)
- `docs/DECISIONS.md` — this entry.
- `docs/issues.md` — Issue 56 closed; new Issue 79 filed for the
  implementation.

### Date
2026-05-28

---

## 2026-05-28 — Issue 57: Automatic refund on terminal ingest failure

### What changed
- New module `billing/refund.py` with `refund_for_video(video_id)`. Looks up
  the `MinuteDeduction` for the video; if a refund `MinutePack`
  (`pack_id=f"refund:{video_id}"`) already exists, no-op; otherwise grant
  the same minute count back via `grant_minutes(reason="refund",
  pack_id=f"refund:{video_id}", price_cents=0)`.
- New Celery base class `RefundOnFailureTask` in `worker/tasks.py`. Its
  `on_failure` hook fires only when retries are exhausted; it extracts
  `video_id` from `args[0]`, refuses to crash the failure path on any
  internal exception, and dispatches `refund_for_video` via `run_async`.
- The three ingest-chain tasks — `ingest_video`, `transcribe_video`,
  `build_signals` — now use `base=RefundOnFailureTask`. `generate_clips`
  and `render_clip` do NOT — neither path deducts minutes, so refund is
  not applicable.
- `docs/COMPLIANCE.md` now includes a "Billing & Refund Policy" section
  with the disclosure language; this is the canonical user-facing
  disclosure until pricing / ToS pages land.

### Why
The product needed a policy. The choice between "automatic", "support-only",
and "hybrid (auto for our errors, manual for user-source errors)" was open;
the user delegated the call. The peer SaaS pay-per-use refund pattern is
unambiguous:

- **Stripe metered billing** auto-credits usage-record errors and surfaces
  them only in the customer portal billing history.
- **AWS service credits** auto-issue on SLA breach; visible in console,
  email is opt-in.
- **OpenAI compute charges** auto-refund on server-side API failures; usage
  dashboard surfaces them; per-call emails would create alert fatigue.
- **Twilio failed-message refunds** auto-credit, usage log only.

Convergent pattern: **automatic, immutable ledger entry, per-event email
only when material**. Honesty-constraint friendly ("you pay for what we
deliver"), low support burden, no abuse vector that isn't already bounded
by `max_retries=3` + per-video idempotency.

"All terminal failures" over "system errors only" because the classification
carve-out creates real edge cases (corrupt-but-decodable codec? DRM stripped
halfway?), demands a failure-reason taxonomy we don't have, and erodes trust
on the failure event itself. The abuse model — a user deliberately uploading
broken files to game the trial — costs us minutes that we'd refund anyway
(zero additional loss) plus compute we'd incur on retries (small dollar
amount; bounded by `max_retries`); the right knob for that is rate limiting
or per-creator quotas, not the refund policy.

### Alternatives ruled out
- **Support-initiated refunds**: high friction, doesn't match peer SaaS,
  creates a support queue we don't staff. Failure-mode UX would be: video
  shows "failed", balance reflects the deduction, creator has to find a
  support contact and email. Bad.
- **Hybrid policy (auto for system errors only)**: requires a
  `failure_reason` taxonomy plumbed through the three ingest tasks; demands
  a confidence call ("is this codec failure 'our fault' because we should
  support it, or 'their fault' because it's exotic?") that we can't make
  cleanly today. Revisit if/when we have meaningful corpus on real
  failures.
- **Refund minus a "we tried" overhead**: hard to communicate; erodes
  trust on the failure event; saves a trivial amount per failure relative
  to the support cost of explaining it.
- **`MinuteDeduction.refunded_at` column instead of compensating `MinutePack`
  row**: row mutation breaks the existing "immutable ledger" invariant.
  Both `MinuteDeduction` and `MinutePack` carry inline docstrings calling
  out immutability; the compensating-grant pattern preserves the
  event-sourcing audit trail; the schema already supports it (the `reason`
  column is a free-text label, and `pack_id` accepts arbitrary keys).
- **Per-video email + in-app banner notification (originally requested by
  the user)**: we have ZERO email infrastructure and ZERO notification
  surface. Bundling both into Issue 57 would explode a one-day refund-ledger
  PR into three separate systems. **Split out into Issues 58 (transactional
  email infrastructure) and 59 (in-app notifications surface)**, filed in
  `docs/issues.md`. Issue 57 ships with the immutable billing-history row
  as the only user-visible surface; the refund email and banner follow once
  the underlying infrastructure lands.

### Tradeoffs
- **Idempotency is read-then-write, not enforced by a UNIQUE constraint**.
  `MinutePack.pack_id` is not unique by itself. Two concurrent `on_failure`
  invocations for the same `video_id` could in principle race past the
  pre-check and both INSERT a refund row. This is not reachable in the
  current pipeline (the ingest chain is single-runner per video; Celery
  doesn't double-fire `on_failure` for one task instance), but if real
  concurrency emerges (e.g. a manual reprocessing endpoint) we should add
  a partial unique index `UNIQUE (pack_id) WHERE reason = 'refund'`
  via a future migration. Flagged in `billing/refund.py` module docstring.
- **`on_failure` swallows exceptions raised by the refund itself**. The
  worker's terminal failure must stand even if the refund crashes (e.g.
  transient DB outage at the precise moment the refund tries to write).
  Manual remediation via direct call to `refund_for_video(video_id)` is
  supported. A future hardening could add Celery retry semantics to the
  refund itself, but that adds complexity for a path that should already
  be rare.
- **Refund triggers on `failed` ingest only, not on Stripe purchase
  failures**: out of scope. Failed purchases never deduct minutes in the
  first place (the deduct happens on ingest, not on purchase).

### Source / evidence
- Read `MinutePack` / `MinuteDeduction` definitions at `models.py:434–480`
  — confirmed immutability docstrings, `reason` field shape, `pack_id` not
  unique, `stripe_session_id` unique-but-nullable.
- Read `billing/ledger.py:39–66` `grant_minutes` — confirmed it accepts
  arbitrary `reason` + `pack_id` kwargs and writes a `MinutePack` row +
  balance update in one session.
- Read the existing ingest chain at `worker/tasks.py:49–87` to confirm
  the failure path: `_set_status(failed)` + `self.retry(exc)`. The retry
  raises `MaxRetriesExceededError` on the final attempt; Celery's
  `on_failure` then fires exactly once.
- Celery `Task.on_failure` semantics: https://docs.celeryq.dev/en/stable/userguide/tasks.html#handlers
  ("Run by the worker when the task fails", fires only on final failure).
- Industry pattern confirmed against Stripe Billing credit balance docs,
  AWS Cost Anomaly Detection notification surfaces, OpenAI usage dashboard,
  Twilio Programmable Messaging usage logs.

### Files
- `billing/refund.py` — new (refund helper).
- `worker/tasks.py` — `RefundOnFailureTask` base; applied to `ingest_video`,
  `transcribe_video`, `build_signals`.
- `tests/test_billing_refund.py` — unit tests for `_refund_pack_id` and
  `RefundOnFailureTask.on_failure` dispatch/safety.
- `tests/test_billing_refund_integration.py` — three real-Postgres scenarios
  (deduct → refund net zero; idempotent on duplicate; pre-deduct failure is
  clean no-op).
- `docs/COMPLIANCE.md` — new "Billing & Refund Policy" section with
  user-facing disclosure language.
- `docs/issues.md` — Issue 57 closed; new Issues 58 + 59 filed as stubs.

### Date
2026-05-28

---

## 2026-05-28 — Issue 46: Generate-clips retry safety + outcomes 30-day floor

### What changed
- `clip_engine/ranking.py:generate_and_rank_clips` — the `DELETE FROM clips
  WHERE video_id = :vid` before reinsert is now narrowed to exclude `done` and
  `running` rows: `Clip.render_status.notin_([RenderStatus.done,
  RenderStatus.running])`. Pending and failed rows are still cleared.
- `worker/tasks.py:_generate_clips_async` — early-return idempotency guard:
  `select(Clip.id).where(Clip.video_id == video_uuid, Clip.render_status ==
  RenderStatus.done).limit(1)`; if a row is returned, log and return without
  invoking `generate_and_rank_clips`. The guard runs before the Signals lookup,
  so a retry on an already-rendered video no-ops even if Signals were never
  persisted.
- `worker/tasks.py:_poll_clip_outcomes_async` — added a 30-day floor on the
  Clip side of the join: `Clip.created_at > now - timedelta(days=30)`. Clips
  older than 30 days drop out of the polling set even when their `fetched_at`
  is past the 7-day arm.

### Why
Two distinct production hazards in one Celery task family:

1. **Late retry wipes rendered work**. `generate_clips` is configured with
   `max_retries=2, default_retry_delay=60`. If a retry fires after
   `render_clip` has already moved one or more rows to `done`, the previous
   unconditional `DELETE` would drop those rows, orphaning the rendered
   R2 objects and breaking the `ClipOutcome` FK chain (cascade delete on
   `clip_id`). The selective DELETE preserves anything in a terminal-success or
   in-flight render state; the idempotency guard short-circuits the whole task
   so the retry doesn't even re-extract candidates and re-rank them. Together
   they make `generate_clips` safe to retry at-least-once.
2. **Unbounded 7-day re-poll arm**. The WHERE was
   `or_(and_(performed_well.is_(None), fetched_at < cutoff_48h), fetched_at <
   cutoff_7d)`. The second arm has no upper bound on the clip's age — once a
   clip is past its 7-day checkpoint, every hourly run of
   `poll_clip_outcomes` would re-fetch its stats forever, burning YouTube Data
   API quota for a label flip that doesn't matter at that age. A 30-day floor
   matches the preference model's recency-decay horizon: a flip from
   `performed_well=False` to `True` for a 60-day-old clip would have a
   vanishing sample weight anyway.

### Alternatives ruled out
- **Make `generate_and_rank_clips` upsert-based on `(video_id, peak_s)`**:
  would eliminate the DELETE entirely but requires a new unique index +
  alembic migration, plus a way to delete stale candidates that no longer
  appear in the new ranking. Heavier than the acceptance criteria demand;
  the selective DELETE + idempotency guard hits the same correctness target
  with one-line changes and no schema work.
- **Bound the poll window by `ClipOutcome.published_at`** instead of
  `Clip.created_at`: `published_at` is nullable until the YouTube upload
  completes, so it would silently skip clips during the publish race window.
  `Clip.created_at` has a tz-aware default at row insert and is monotone.
- **30 vs 60 vs 90 days for the floor**: 30 days matches the recency-decay
  half-life used by `preference/decay.py:sample_weight`. A flip past one
  half-life contributes negligible weight to the next retrain.

### Tradeoffs
- **Selective DELETE keeps `running` rows around forever if render gets
  stuck**: acceptable. A separate Celery retry+timeout in `render_clip`
  (`max_retries=3, default_retry_delay=60`) drives `running` → `failed` on
  timeout/exception; the next `generate_clips` retry then sweeps the failed
  row out cleanly.
- **Idempotency guard is binary** (any `done` clip → skip entirely). For a
  video where rendering partially succeeded (some `done`, some `failed`),
  the retry will preserve all `done`/`running` rows but skip re-extracting
  candidates for the failed ones. Acceptable: the failed rows are still
  retried by `render_clip` itself; we don't re-rank a partially-rendered
  video.
- **30-day floor is not configurable**: hardcoded. If the recency-decay
  horizon changes (`preference/decay.py`) the two should stay aligned —
  flagged for future cleanup if either ever moves.

### Source / evidence
- Read `generate_and_rank_clips` at `clip_engine/ranking.py:65–119` —
  confirmed the unconditional DELETE on line 89 and the `session.commit()`
  follow-up on line 114.
- Read `generate_clips` Celery task at `worker/tasks.py:80–87` — confirmed
  `max_retries=2`, no idempotency check before `run_async`.
- Read `_poll_clip_outcomes_async` at `worker/tasks.py:376–460` — confirmed
  `cutoff_48h` is used in the `performed_well IS NULL` arm and is therefore
  self-bounding; the 7d arm is the unbounded one. (LEFT_OFF's framing of
  the 48h cutoff being the bug was slightly off; the actual bug is in the
  7d arm.)
- Celery retry-safety guidance: tasks must be safe under at-least-once
  redelivery, terminal-success rows must never be touched by a retry
  (https://docs.celeryq.dev/en/stable/userguide/tasks.html#avoid-launching-synchronous-subtasks).
- Standard sliding-window outcome polling pattern: bounded by both edges
  (Stripe webhook retry scheduler; Shopify Fulfillment polling docs).

### Files
- `clip_engine/ranking.py` — narrowed the DELETE WHERE (3 lines).
- `worker/tasks.py:_generate_clips_async` — early-return guard (12 lines).
- `worker/tasks.py:_poll_clip_outcomes_async` — 30-day floor added to the
  WHERE (3 lines including the `poll_floor` binding).
- `tests/test_outcomes.py` — two new predicate-level unit tests pinning
  the 30-day floor.
- `tests/test_generate_clips_retry_integration.py` — new `integration`-marked
  file with three scenarios: selective-DELETE preserves done+running and
  clears pending+failed; `_generate_clips_async` short-circuits when a done
  clip exists (even without Signals); `_poll_clip_outcomes_async` excludes
  clips >30 days old while polling fresh ones.

### Date
2026-05-28

---

## 2026-05-28 — Issue 47: Beat-job fairness via `last_analytics_refreshed_at`

### What changed
- Added `creators.last_analytics_refreshed_at: timestamptz NULL` (bundled with
  Issue 43 into alembic revision `d4e5f6a7b8c9`, file renamed to
  `0004_video_done_creator_refreshed.py`).
- Added B-tree index `ix_creators_refresh_order ON creators(last_analytics_refreshed_at, id)`
  to make the daily sweep cheap.
- `_refresh_youtube_analytics_async` now orders creators by
  `Creator.last_analytics_refreshed_at.asc().nulls_first(), Creator.id`.
- On successful per-creator refresh (after `sync_audience_data` returns,
  inside the same transaction as the analytics writes), set
  `creator.last_analytics_refreshed_at = datetime.now(UTC)` before
  `session.commit()`. On `QuotaExhaustedError` the existing
  `await session.rollback()` un-stamps the timestamp by design, so the
  starved creator stays at the front of the queue next cycle.

### Why
The previous loop iterated `select(Creator)` with no `ORDER BY`. On
`QuotaExhaustedError` the loop broke. Quota resets daily; next beat run
started the same scan in the same heap order. For e.g. 50 creators with
quota for ~30 per day, creators 31–50 starved forever — they would never
even have analytics fetched once. Classic FIFO-fairness bug.

The fix is a single nullable timestamp + an `ORDER BY` clause. NULLS FIRST
means newly-connected creators (never refreshed) jump the queue, which
matches user expectation: "I just connected my channel, I expect to see
data fast." Once they're refreshed they stamp and drop to the back; the
oldest stamp goes next.

### Alternatives ruled out
- **`ORDER BY RANDOM()`**: non-deterministic, hard to debug. Probabilistically
  still starves unlucky creators across consecutive runs (any randomized
  scan with a cutoff has a non-zero starvation tail).
- **Round-robin pointer in Redis**: extra distributed state; doesn't survive
  worker restart cleanly; loses the "newly connected creator jumps first"
  property.
- **Process all creators in parallel via Celery groups**: multiplexes the
  quota faster but does nothing for fairness — same starvation curve,
  compressed in time.
- **Per-creator quota allocation (1/N of total)**: punishes power users
  with many videos who legitimately need more quota; doesn't solve the
  "new creator never appears in the scan" failure mode.

### Tradeoffs
- **Partial-refresh starvation (acknowledged)**: if a creator's refresh
  partially succeeds (e.g. 5 of 12 videos processed) and then
  `sync_video_analytics` raises `QuotaExhaustedError`, we rollback the
  whole creator and don't stamp the timestamp. They retry first next run.
  A creator who *always* trips quota mid-refresh would never advance —
  but that's actually correct behavior (no partial credit). Out of scope
  for Issue 47.
- **Migration coupling**: bundled with Issue 43's `videos.ingest_done_at`
  into one alembic revision (`0004_video_done_creator_refreshed.py`) per
  LEFT_OFF's explicit suggestion. Pro: one alembic step at deploy. Con:
  reverting one change reverts both. Both are nullable-additive,
  low-blast-radius, so the coupling is acceptable.
- **No backfill**: existing creators have `last_analytics_refreshed_at IS
  NULL`, which by `NULLS FIRST` puts them at the front on day 1 (tied
  break by `id` — same as today's order). Self-bootstrapping fairness
  after the first daily sweep.
- **Index cost**: tiny B-tree on `(last_analytics_refreshed_at, id)`.
  Bounded by creator count.

### Source / evidence
- Read `_refresh_youtube_analytics_async` at `worker/tasks.py:532–572` and
  confirmed: `select(Creator)` with no `ORDER BY`; `break` on
  `QuotaExhaustedError`; per-creator commit inside the inner try.
- SQLAlchemy `.nulls_first()` documented at
  https://docs.sqlalchemy.org/en/20/core/sqlelement.html#sqlalchemy.sql.expression.nulls_first
- Canonical time-based fairness pattern: Crunchy Data's `SKIP LOCKED`
  job-queue writeups, Stripe's webhook re-delivery scheduler design, every
  CRM batch-syncer paginator.

### Files
- `alembic/versions/0004_video_done_creator_refreshed.py` — added
  `creators.last_analytics_refreshed_at` + `ix_creators_refresh_order`;
  broadened docstring + filename to reflect the bundle.
- `models.py` — `Creator.last_analytics_refreshed_at` Mapped column.
- `worker/tasks.py` — `ORDER BY` clause on the creator SELECT; stamp +
  commit on successful refresh.
- `tests/test_retention_tasks.py` — three new mock-level tests pinning
  the load-bearing contracts: ORDER BY whereclause inspection,
  stamp-on-success, no-stamp-on-quota-exhaustion.
- `tests/test_analytics_fairness_integration.py` — new `integration`-marked
  scenario: 5 creators × 2-budget × 3 cycles → no starvation; verifies
  both attempt sequence and DB timestamp stamping.

---

## 2026-05-28 — Issue 43: Source-media retention clock = ingest completion, not upload

### What changed
- Added `videos.ingest_done_at: timestamptz NULL` (alembic revision `d4e5f6a7b8c9`)
  + partial index `ix_videos_purge_candidates ON videos(ingest_done_at) WHERE
  ingest_done_at IS NOT NULL AND source_uri IS NOT NULL` to keep the hourly purge
  sweep cheap.
- Set `Video.ingest_done_at = datetime.now(UTC)` in `_signals_async` at the same
  point we flip `ingest_status` to `done`. Guarded by `if video.ingest_done_at
  is None:` so a retry of an already-completed task preserves the original
  completion stamp (Celery is at-least-once; without the guard, retries would
  silently extend the retention window).
- Changed `_purge_stale_source_media_async` filter from `Video.created_at <
  cutoff` to `Video.ingest_done_at.is_not(None) AND Video.ingest_done_at <
  cutoff`. Kept the `source_uri IS NOT NULL` predicate.
- Backfill (one-shot in the migration): every existing row with `ingest_status
  = 'done'` AND `ingest_done_at IS NULL` gets `ingest_done_at = created_at`. This
  preserves the pre-migration retention semantics for already-completed videos.

### Why
The previous filter `Video.created_at < cutoff` started the retention clock at
upload time. A video uploaded 30h ago but still mid-ingest (slow Whisper, retry
backoff, beat-cycle race) would have its `source_uri` nulled out from under the
pipeline; the next stage would crash trying to read the file. This is SEV-1
because under any concurrency / queue depth it shows up as flapping ingests
that "just sometimes fail" — exactly the kind of bug that's expensive to
diagnose post-launch.

The new filter gates on a soft-completion timestamp: ingest is "done with
the source" precisely when the signals-build commits successfully. That's the
right moment to start the YouTube ToS retention clock.

### Alternatives ruled out
- **Gate on `ingest_status = IngestStatus.done`**: works, but couples retention
  to a status enum that's also used for failure states. With the timestamp we
  can later say "retain failed videos longer for debugging" without a schema
  change.
- **Bigger retention window (e.g. 72h → 168h)**: pushes the problem out but
  doesn't fix it; a stuck pipeline still races on day 4.
- **Skip purge while a task is in-flight (Redis lock check)**: orthogonal
  mechanism, much more complex, doesn't help the case where a task crashed and
  left `source_uri` set without `ingest_done_at`.
- **Use a `Video.updated_at`**: don't have one, and `updated_at` would tick on
  retries/status flips/score writes — fuzzy semantics for a retention cutoff.

### Tradeoffs
- **Backfill semantics**: existing already-completed videos use `created_at` as
  a stand-in for `ingest_done_at`. Slightly off (the original completion was
  later than upload), but bounded by the ingest pipeline runtime (~minutes)
  and only matters at the edges of the cutoff. Net effect: a handful of
  already-completed videos get a few minutes of extra retention. Acceptable.
- **Failed-ingest rows**: `ingest_done_at` stays NULL for rows with
  `ingest_status = failed`. Those rows are NEVER purged by this sweep. Their
  source media is small (failed ingests = nothing rendered) and they're useful
  for debugging. If they pile up they can be cleaned via a separate retention
  job; out of scope for Issue 43.
- **Idempotency**: the `if video.ingest_done_at is None` guard is load-bearing.
  Without it, Celery's at-least-once redelivery could refresh the timestamp on
  retry, silently pushing the cutoff forward by hours/days.
- **Partial index cost**: adds one B-tree of (`ingest_done_at`) filtered to
  source-still-on-disk rows. Roughly O(videos with source_uri set). At our
  scale this is a few thousand rows max — negligible storage; meaningful
  speedup for the hourly Beat sweep.

### Source / evidence
- Read `_purge_stale_source_media_async` at `worker/tasks.py:491–525` and
  confirmed the bug: filter is `Video.created_at < cutoff`, not gated on
  status. Confirmed `IngestStatus.done` is set exactly once at line 254 inside
  `_signals_async`.
- SQLAlchemy partial index pattern:
  https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#partial-indexes
- Standard pattern across event/job systems: gate retention on a
  "soft-completion" timestamp (Stripe `processed_at`, S3 lifecycle
  `LastModified`, DLQ `last_completed_at`).

### Files
- `alembic/versions/0004_video_ingest_done_at.py` — schema + backfill +
  partial index.
- `models.py` — `ingest_done_at` Mapped column on `Video`.
- `worker/tasks.py` — `datetime` added to top-level import; `_signals_async`
  stamps `ingest_done_at` under the NULL guard; `_purge_stale_source_media_async`
  filter swapped.
- `tests/test_retention_tasks.py` — semantic-aligned existing tests
  (`created_at` → `ingest_done_at` on mocks); new `test_purge_filter_gates_on_ingest_done_at`
  inspects the SQL `whereclause` to pin the new predicate; new
  `test_signals_async_stamps_ingest_done_at_when_null` +
  `test_signals_async_preserves_ingest_done_at_on_retry` pin the idempotent
  write contract.
- `tests/test_purge_integration.py` — `@pytest.mark.integration` real-DB
  scenario: done-100h purged, in-progress-100h preserved, done-1h preserved.
- `docs/COMPLIANCE.md` — retention-clock row updated to reflect the new
  semantic for the YouTube ToS posture.

---

## 2026-05-28 — Issue 39: Celery event-loop strategy

### What changed
- Replaced per-task `asyncio.run(...)` with a per-worker-process singleton event loop
  installed by the `worker_process_init` Celery signal.
- Added `db.recreate_engine()` and `db.dispose_engine()` so the SQLAlchemy async engine
  + asyncpg pool can be rebound to the worker child's loop after fork, and cleanly
  disposed on `worker_process_shutdown`.
- Added `worker.celery_app.run_async(coro)` — used by every task in `worker/tasks.py`
  (11 sites) instead of `asyncio.run`. Falls back to `asyncio.run` when no worker loop
  is installed (unit-test invocation path).
- `worker/tasks.py` now does `import db` and uses `db.AsyncSessionLocal(...)` so that
  rebinding the module-global sessionmaker in `db.recreate_engine()` is visible to
  task bodies at call time (`from db import AsyncSessionLocal` would capture the
  stale reference).

### Why
Every Celery task used to call `asyncio.run(_some_async(...))`, which creates a fresh
event loop per task. The first task in a worker process would bind the engine's
asyncpg pool to its loop; subsequent tasks would receive a *different* loop and hit
the classic `Future attached to a different loop` errors plus pool churn (each loop
discarded, connections re-handshaked). Under concurrent load this was a SEV-1 because
it manifests as intermittent worker failures rather than a single reproducible bug.

The fix pins one loop per worker process for the worker's lifetime and binds the
engine to it once. This is the canonical FastAPI + Celery + async-SQLAlchemy pattern;
SQLAlchemy's own docs spell out that async engines must be created *after* fork
because the asyncpg connection pool cannot survive across processes.

### Alternatives ruled out
- **`celery-pool-asyncio` / `celery-aio-pool`**: third-party pool replacements. Smaller
  community, replace the entire pool model, and unnecessary — our concurrency model is
  per-process prefork and we don't need cooperative I/O multiplexing inside a task.
- **`asgiref.async_to_sync`**: caches a loop per thread but does not address the
  engine-binding-on-fork problem. Same bug class would resurface.
- **Lazy `get_engine()` inside every coroutine**: scatters the fix across every task
  body and makes the contract implicit; one init signal is far easier to audit.
- **`gevent` / `eventlet` worker pool**: would require monkey-patching the entire
  stack; out of scope.

### Tradeoffs
- Each worker child holds a long-lived loop + pool. Trivial memory cost vs. eliminating
  the pool-rebind cost on every task.
- Engine pool sizing budget is unchanged: `concurrency × (pool_size + max_overflow)`,
  currently `concurrency × 30`. If we raise Celery concurrency, we must size the
  Postgres `max_connections` accordingly. Not a regression — the pre-fix code had the
  same upper bound; it just churned the pool more.
- `worker_process_init` calls `db.recreate_engine()` after fork. We use
  `engine.sync_engine.dispose(close=False)` to abandon (not close) any inherited
  parent connections so we don't yank file descriptors out from under the parent.
  In practice the parent has no open connections at fork time (it only imports the
  modules), but this is the SQLAlchemy-blessed safe default.

### Source / evidence
- SQLAlchemy 2.0 docs — "Using asyncio with multiprocessing":
  https://docs.sqlalchemy.org/en/20/core/pooling.html#using-connection-pools-with-multiprocessing-or-os-fork
- Celery worker signals reference:
  https://docs.celeryq.dev/en/stable/userguide/signals.html#worker-process-init
- Prior incident pattern: `Future attached to a different loop` is the symptom called
  out in Issue 39's spec; verified the cause by reading `worker/tasks.py:49–135` and
  `db.py:8` before the fix.

### Files
- `db.py` — added `_make_engine`, `recreate_engine`, `dispose_engine`.
- `worker/celery_app.py` — singleton `_LOOP`, `run_async`, init/shutdown signal hooks.
- `worker/tasks.py` — 11 × `asyncio.run` → `run_async`, 16 × `AsyncSessionLocal` →
  `db.AsyncSessionLocal`.
- `tests/test_celery_event_loop.py` — pins loop-reuse, fallback, init/shutdown,
  engine-rebind invariants (5 tests).
- `tests/test_retention_tasks.py`, `tests/test_pipeline_trigger.py`,
  `tests/test_oauth_lifecycle.py` — updated patch targets from `worker.tasks.*` to
  `db.AsyncSessionLocal` / `worker.tasks.run_async` to match the new import surface.

---

## 2026-05-28 — Issue 37: External SDK Timeouts + Retry-with-Backoff

### Anthropic SDK (`anthropic==0.40.0`)

**What**: Replaced per-call `Anthropic(...)` / `AsyncAnthropic(...)` construction in `dna/brief.py`, `improvement/brief.py`, and `clip_engine/scoring.py` with module-level singletons (`_ANTHROPIC`) constructed once from `config.settings`. Configured `timeout=httpx.Timeout(60.0, connect=10.0)` and `max_retries=2`. For `improvement/brief.py`, the web_search call uses `_ANTHROPIC.with_options(timeout=120.0)` per-call because web_search tool agentic loops routinely exceed 60s.

**Why**: The Anthropic Python SDK docs (sdk.anthropic.com/python) recommend constructing the client once and reusing it. Per-call construction wastes connection pool setup on every invocation. The 60s read timeout covers standard Claude calls; 120s override on the web_search path is needed because the tool loop typically takes 30–90s per the Anthropic docs on `web_search_20250305`. connect_timeout of 10s is an industry-standard value for TLS handshakes. `max_retries=2` uses the SDK's built-in exponential backoff on transient 529/500 errors.

**Source**: Anthropic SDK docs — `httpx.Timeout`, `max_retries`, `with_options`; Anthropic web_search tool docs noting agentic loop latency.

### Stripe SDK (`stripe==11.4.0`)

**What**: Added `stripe.max_network_retries = 3` at module level in `billing/stripe_client.py` and promoted `StripeClient` to a module-level singleton `_STRIPE`.

**Why**: Stripe's official Python library docs state that `max_network_retries` enables automatic retry with exponential backoff on 429 and 5xx errors. The default is 0 (no retries). Setting 3 is the Stripe-recommended value for production. The default 80s socket timeout is appropriate for Checkout session creation and is not overridden.

**Source**: Stripe Python library docs — `stripe.max_network_retries`; Stripe best practices guide.

### Voyage AI (`voyageai==0.3.2`)

**What**: Added lazy-initialized module-level singleton `_VOYAGE` (via `_voyage()` accessor) in `dna/embeddings.py` with `timeout=30`. Wrapped embedding calls in a `_embed()` function decorated with `@tenacity.retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))`. Added `tenacity==9.1.4` to `requirements.txt`.

**Why**: The voyageai SDK does not support built-in retries. Tenacity is the Python community standard for retry-with-backoff (used by Google, LangChain, etc.). Exponential backoff with min=1s/max=10s is the standard pattern for rate-limit-friendly API retries. The singleton is lazy (not eager at import time) because voyageai.Client validates the API key at construction, which would fail in test environments without `VOYAGE_API_KEY` set.

**Source**: Tenacity docs (tenacity.readthedocs.io); voyageai Python client source (`voyageai/_base.py`).

### boto3 / Cloudflare R2 (`boto3==1.35.54`)

**What**: Replaced per-call `boto3.client(...)` with a lazy module-level singleton `_R2` (via `_r2()` accessor) in `worker/storage.py`. Configured `botocore.config.Config(retries={"mode": "adaptive", "max_attempts": 5}, connect_timeout=10, read_timeout=60)`.

**Why**: boto3 docs recommend reusing the client to share the connection pool. Adaptive retry mode (botocore docs) uses a token bucket to avoid retry storms on throttling; `max_attempts=5` is the botocore recommended value for production S3 workloads. `connect_timeout=10` / `read_timeout=60` match AWS SDK best practices. The singleton is lazy because boto3 validates the endpoint URL at construction, which fails if `R2_ACCOUNT_ID` is empty (test environment).

**Source**: botocore Config docs; AWS SDK best practices guide for S3 retry configuration.

### Deepgram / WhisperX (`ingestion/transcribe.py`)

**What**: No change made. WhisperX is local-only (no network timeout relevant). The Deepgram fallback path uses `deepgram-sdk` which is commented out of `requirements.txt` and unreachable in all environments. There is no httpx-based fallback path.

**Why**: Implementing a timeout on an unreachable code path would be dead code. Noted here to close the loop on the Issue 37 audit.

**Date**: 2026-05-28

---

## 2026-05-25 — Project Kickoff Decisions

### North Star Sentence

**What**: Settled on the north star: *"The only AI editor that truly knows your channel —
it learns your style from your own analytics, adapts as you evolve, and keeps you ahead of
the algorithm."*

**Why**: The product is broader than clipping — it's a full analyzer + advisor that adapts to
the creator's evolving style and keeps them informed about algorithm changes. The sentence must
communicate the personalization flywheel, not just the clip output.

**Source**: Creator (owner) input, 2026-05-25.

---

### Review UI: Single Player + Next

**What**: The review interface is a single-player + Next button, not a swipe-stack.

**Why**: Single-player makes precision trim handle interaction easier and more reliable.
Swipe-stack UX is faster for bulk review but sacrifices the trim-delta signal, which is the
strongest *timing* feedback. Trim handles are the visual centerpiece.

**Source**: Creator input, 2026-05-25.

---

### Pricing Model: Usage-Based Tiers (Research Pending)

**What**: Pricing is usage-based with tiered subscription floors, similar to Anthropic's own
model. A flat "low cap" monthly plan would frustrate prolific creators. A pure per-video model
adds friction.

**Why**: Creators' output volume varies enormously. A tiered usage model (e.g., base plan
includes N tokens/videos, then pay-as-you-go overage) aligns cost with value and doesn't
block high-output creators.

**Research needed**: Best practices for usage-based SaaS pricing + Stripe metered billing
implementation. Must be decided before public launch. Stripe + usage metering is the
industry-standard path.

**Source**: Creator input, 2026-05-25. Research not yet completed — see `docs/SOT.md` Known
Production Gaps.

---

### Production Deployment: GKE Autopilot + Helm + KEDA

**What**: GKE Autopilot is the production K8s platform. Helm charts in
`deploy/charts/creatorclip/`. KEDA ScaledObject autoscales Celery workers on Redis
queue depth. PgBouncer sidecar handles connection pooling. Cloud SQL for PostgreSQL 16
(pgvector enabled). GCP Secret Manager + External Secrets Operator for secrets.

**Why GKE Autopilot over EKS/DO**:
- No node management — Google provisions and upgrades nodes automatically
- Cloud SQL for PostgreSQL 16 has first-class pgvector support (vs. RDS which requires
  custom parameter groups and is slower to enable extensions)
- GCP Secret Manager + Workload Identity = cleanest managed-secrets story without extra agents
- Spot node pools for transcription workers available when we add WhisperX
- Familiarity: same provider as Cloudflare Tunnel integration already in dev

**KEDA vs HPA-only**: HPA on CPU is insufficient for Celery — a backlogged queue does
not spike CPU until workers are already overwhelmed. KEDA's `redis-listLength` trigger
scales on actual work queued, providing proactive scaling.

**PgBouncer sidecar vs RDS Proxy**: Sidecar eliminates the network hop to a separate
pooler, is free, and transaction mode allows up to 25 upstream connections per pod
(→ 750 at 30 pods, well within Cloud SQL's 1,000 limit).

**Source**: Compared providers on pgvector support, managed node overhead, secrets
integration, and community KEDA+Celery patterns. 2026-05-26.

---

---

### OAuth HTTP Calls: httpx Instead of google-auth-oauthlib

**What**: The OAuth token exchange, token refresh, userinfo, and YouTube Channels calls are
implemented directly with `httpx.AsyncClient` rather than using `google-auth-oauthlib` /
`google-api-python-client`.

**Why**: `google-auth-oauthlib` is synchronous — using it in an async FastAPI handler requires
`asyncio.run_in_executor()` boilerplate. The OAuth endpoints are simple POST/GET calls that
`httpx` handles natively in 3–4 lines each. Fewer dependencies, fully async, and easier to
test (patch the `_call_*` helpers rather than monkey-patching Google internals).

**Source**: httpx docs; FastAPI async best practices. Confirmed: no Google library provides
a first-party async implementation as of 2026-05.

---

### Numeric Thresholds Set as Defaults

**What**: The following defaults were set based on the kickstart document's suggested values:

| Variable | Default | Rationale |
|----------|---------|-----------|
| `CLIPS_PER_VIDEO_DEFAULT` | 8 | Enough candidates to cover diverse moments without overwhelming review |
| `MIN_VIDEOS_FOR_DNA` | 10 | Minimum for meaningful top/bottom performer analysis |
| `MIN_SHORTS_FOR_DNA` | 5 | Minimum for Shorts-specific pattern extraction |
| `PERSONALIZATION_THRESHOLD_LABELS` | 20 | Minimum feedback volume for reranker to produce meaningful signal |

All are environment-configurable and can be tuned once real usage data exists.

**Source**: Kickstart document defaults; no external research needed (tunable post-launch).

---

### Postgres Docker Image: pgvector/pgvector:pg16

**What**: Using `pgvector/pgvector:pg16` in docker-compose instead of `postgres:16` + manual
extension install.

**Why**: The official pgvector Docker image pre-installs the extension, eliminating the
`CREATE EXTENSION` step that frequently trips up fresh setups. Same underlying Postgres 16;
no functional difference.

**Source**: pgvector GitHub README recommendation, standard practice.

---

### Transcription Backend: Deepgram as MVP Default

**What**: `TRANSCRIPTION_BACKEND` defaults to `"deepgram"` (hosted API). WhisperX remains
available via `TRANSCRIPTION_BACKEND=whisperx` for self-hosted GPU deployments. The
`DEEPGRAM_API_KEY` field is already in Settings (optional, empty default).

**Why**: No GPU infrastructure exists for the MVP. Deepgram's Nova-3 model provides
word-level timestamps, speaker diarization, and competitive accuracy without the operational
overhead of managing a GPU box or container. WhisperX is preserved as a config-selectable
path for production cost optimisation once volume justifies the GPU spend.

**Source**: Resolves the "Transcription compute" open research item. Decision: hosted API
for MVP, self-hosted as a future cost lever. 2026-05-25.

---

### asyncio.run() in Celery Tasks

**What**: Celery task functions (`ingest_video`, `transcribe_video`, `build_signals`) use
`asyncio.run()` to call async SQLAlchemy helpers. Each task creates a fresh event loop
per invocation.

**Why**: Celery workers are process-based and synchronous by default. The project's
SQLAlchemy setup is async-only (`create_async_engine`). The alternatives — a parallel sync
engine or `nest_asyncio` — add more complexity. `asyncio.run()` is the documented SQLAlchemy
approach for non-async call sites, and Celery workers run in their own processes so there is
no event-loop conflict.

**Source**: SQLAlchemy async docs "Using Asyncio" section; Celery docs recommend keeping
task functions synchronous. 2026-05-25.

---

## 2026-05-26 — Billing: Minute Packs (replaces subscription tiers)

**What**: Billing model is pre-paid minute packs, not subscriptions. `Creator.plan_tier` and
`Creator.subscription_status` replaced with `Creator.minutes_balance` (int) and a
`minute_packs` ledger table. Stripe Checkout in one-time payment mode — no subscriptions,
no Billing Meters. Five purchasable packs (Starter 200 min → Studio 5,000 min) with
programmatically-verified volume discounts. 60-minute free trial granted on first login.
Minutes deducted atomically at ingest via `UPDATE … WHERE minutes_balance >= X RETURNING`.

**Why**: Subscriptions require monthly commitment — a poor fit for creators who post
episodically. Minute packs let creators pay for exactly what they use and never expire,
which is a better conversion funnel ("try 60 free minutes, buy more when you need them").
One-time Stripe Checkout is also significantly simpler to implement than subscriptions
(no Customer Portal, no dunning, no invoice lifecycle).

**Source**: Product decision, 2026-05-26. Feature branch `claude/zealous-wozniak-5KVb7`
merged into main.

---

## 2026-05-26 — Beta deployment: VM + Docker Compose, not Kubernetes

**What**: BETA_DEPLOYMENT phase (Issues 23–28) runs on a single cloud VM (DigitalOcean
Droplet, 4 vCPU / 8 GB RAM) with Docker Compose + Cloudflare Tunnel, not Kubernetes.
This is a scoped exception to the "Docker Compose = dev only" stance in `docs/SOT.md`.

**Why**: Kubernetes is right for 10k+ scale but adds unnecessary operational complexity
for a close-friends beta with < 10 users. The existing CI/CD pipeline (`deploy.yml`)
already handles image build, SSH deploy, and DB migration — no K8s tooling needed for
beta. `docs/SOT.md` still targets GKE Autopilot for production (Issue 22 Helm charts
are ready); this is a scoped beta exception only.

**Source**: Practical deployment gap analysis, 2026-05-26. Production deployment phase
(Issues 29–30) retains the Kubernetes target.

---

## 2026-05-26 — Clip engine: extend end_s for early-peak candidates

**What changed**: `clip_engine/candidates.py` — `end_s` now computed as
`min(duration_s, max(peak_s + POST_PEAK_S, setup_start_s + MIN_CLIP_S))` instead of
`min(duration_s, peak_s + POST_PEAK_S)`.

**Why**: Adversarial eval fixture `peak_very_early` surfaced a bug: when a retention spike
occurs near t=0 (e.g. 12s), the setup-to-post-peak window is only ~27s, below `MIN_CLIP_S`
(30s). The candidate was silently discarded. The fix extends `end_s` just enough to meet the
minimum, so early-video hooks are never dropped.

**Source**: `tests/eval/scenarios/peak_very_early.yaml` — engine returned 0 candidates.
Debug confirmed `end_s - setup_start_s = 27.5 < 30.0`. 2026-05-26.

---

## 2026-05-27 — Issue 31: Operability kit (secrets registry, preflight doctor, deploy hardening, auto-heal)

### Secrets storage: plain gitignored `.env` + registry (not SOPS+age)

**What**: Secrets are kept in gitignored `.env` files (local + VM `/opt/autoclip/.env`, chmod 600),
documented in a single registry at `docs/SECRETS.md`. SOPS+age (encrypted-in-git) was considered
and deferred.

**Why**: For a <10-user close-friends beta on a single VM, plain `.env` with strict file
permissions is the industry-accepted baseline and matches the existing setup with zero new
tooling. SOPS+age adds a keypair to manage and deploy-step changes — robustness we don't need
until multi-operator or compliance requirements appear. Logged as the explicit upgrade path.

**Source**: Web research on single-VM Docker Compose secret management (GitGuardian; Docker docs;
cmmx.de SOPS/age guide), 2026-05-27. Owner chose plain `.env` + registry.

### Pre-existing bug fixed: `routers/clips.py` imported deleted `billing.tiers`

**What changed**: `routers/clips.py` imported `require_render` from `billing.tiers`, a module the
minute-packs rewrite (commit `41016e6`) deleted. The render endpoint now uses
`Depends(get_current_creator)` + `await check_positive_balance(...)`, matching the minute-packs
guard already used in `routers/videos.py`.

**Why**: The stale import meant `import main` raised `ModuleNotFoundError` — the app could not
start at all, the full test suite could not collect, and any container built from `main` would
crash on boot (a likely real cause of "deploy fails / times out"). Minutes are deducted at ingest
(`worker/tasks.py`), so a render needs only a positive-balance guard, not a second deduction.

**Source**: Discovered while running `pytest` during Issue 31 Phase 3. The breaking commit was the
unpushed local `main` commit; this fix lands on top before any push. 2026-05-27.

### Image build: amd64 only

**What**: `docker-publish.yml` builds `linux/amd64` only (was `linux/amd64,linux/arm64`).

**Why**: The DigitalOcean droplet is x86_64. The arm64 build was pure wasted CI time — roughly
doubling image build duration for an architecture nothing runs. Contributed to slow deploys.

**Source**: Deploy-time analysis, 2026-05-27. If an arm64 host is ever added, restore the matrix.

### Cloudflared in Compose + no host port + auto-heal (beta VM)

**What**: `docker-compose.prod.yml` now (a) runs `cloudflared` as a service, (b) removes the app's
`ports: 80:8000` host mapping, (c) drops the dev `--reload` from the app command, (d) adds
liveness `healthcheck`s to `app` and `worker`, and (e) adds a `willfarrell/autoheal` sidecar that
restarts containers labelled `autoheal=true` when their healthcheck goes unhealthy. The tunnel's
public-hostname ingress must target `app:8000` (Compose DNS), documented in `docs/ACCESS.md`.

**Why**: Docker has no native restart-on-unhealthy (confirmed 2026); `autoheal` + per-service
healthchecks is the standard Compose pattern. Routing inbound traffic only through the tunnel
satisfies Issue 23's "no open inbound ports" acceptance and removes the `localhost:80` vs
`app:8000` ambiguity that breaks tunnels. App healthcheck is liveness-only so a transient Postgres
blip doesn't trigger an app restart loop.

**Source**: Web research on Docker Compose auto-healing (willfarrell/autoheal; oneuptime 2026
guides), 2026-05-27.

## 2026-05-28 — Issue 44: Auth boundary hardening

### `get_current_creator`: catch ValueError/KeyError alongside PyJWTError

**What changed**: `auth.py` — `uuid.UUID(payload["sub"])` moved inside the existing
`try/except`, with `(ValueError, KeyError)` added to the caught exception types. A malformed
`sub` (non-UUID string, missing key) now returns 401 "Invalid or expired session" instead of
propagating as a 500.

**Why**: The call was outside the `try` block, so any `ValueError` from `uuid.UUID()` or
`KeyError` from a missing `sub` key fell through to the global exception handler and surfaced
as a 500 with a stack trace in development mode. Per defence-in-depth, any invalid token
payload should yield 401 — not leak error details.

**Source**: Code review of `auth.py:43`; Python `uuid.UUID` docs confirm `ValueError` on
malformed input. 2026-05-28.

---

### `DELETE /me`: add 5/hour rate limit

**What changed**: `routers/auth.py` — `@limiter.limit("5/hour")` added to the
`delete_account` handler. `request: Request` added to handler signature (required by
slowapi for key extraction).

**Why**: The right-to-erasure endpoint had no rate limit. An attacker with a stolen session
could spam it; even accidental repeated clicks should be bounded. 5/hour is generous for
legitimate use (account deletion is a one-time action) and tight enough to prevent abuse.
The existing `limiter` from Issue 18 already uses `_creator_key` (JWT sub → creator UUID),
which gives correct per-creator isolation.

**Source**: slowapi docs on `@limiter.limit`; Issue 18 pattern in `routers/videos.py`.
2026-05-28.

---

### `crypto.py`: MultiFernet + typed TokenDecryptError

**What changed**: `crypto.py` — `_fernet()` now returns `MultiFernet([primary])` when no
previous key is configured, and `MultiFernet([primary, previous])` when
`TOKEN_ENCRYPTION_KEY_PREVIOUS` is set. `decrypt()` catches `cryptography.fernet.InvalidToken`
and re-raises as the new typed `TokenDecryptError`. `config.py` adds
`TOKEN_ENCRYPTION_KEY_PREVIOUS: str | None = None`. `.env.example` documents the rotation
workflow.

**Why MultiFernet over Fernet**: `MultiFernet.encrypt()` always uses the first (primary) key;
`MultiFernet.decrypt()` tries keys in order. This enables zero-downtime key rotation: set
`TOKEN_ENCRYPTION_KEY_PREVIOUS = old key`, run `scripts/rotate_token_key.py` to re-encrypt
all rows under the new primary, then clear `TOKEN_ENCRYPTION_KEY_PREVIOUS`. During the window
between setting the new primary and completing re-encryption, both old and new tokens are
readable. A single-key `MultiFernet([primary])` is functionally identical to `Fernet(primary)`
so there is no behaviour change when no previous key is configured.

**Why TokenDecryptError**: callers (`routers/auth.py`, `youtube/oauth.py`) were inconsistently
handling raw `cryptography.fernet.InvalidToken` — some caught it, some didn't. A project-level
typed exception makes the contract explicit and prevents internal cryptography exceptions from
leaking through unhandled.

**Source**: `cryptography` library docs on `MultiFernet`; Python exception-hierarchy best
practices. Confirmed: `MultiFernet` ships in the same `cryptography` package already pinned
in `requirements.txt`. 2026-05-28.

---

### Preflight doctor as the deploy gate

**What**: New `scripts/doctor.py` validates presence + format + live reachability of every secret
and prints a **redacted** status table (length + last-4 only). `config.py` keeps its fail-fast on
*missing* required vars; the doctor adds *validity* and *connectivity*. `deploy.yml` runs
`python scripts/doctor.py` after image pull and **before** migrations/cutover, so a bad secret
fails the deploy early with safe, visible output rather than a silent crash.

**Why**: The owner's core pain was being unable to see *why* a deploy failed without exposing
secrets. A redacted doctor is the standard "preflight/doctor" answer; pydantic-settings only
covers presence.

**Source**: Web research on pydantic-settings validation patterns, 2026-05-27.

---

## 2026-05-28 — Issue 32: Pin `starlette` explicitly to defend against transitive shadowing

### What changed
`requirements.txt` now pins `starlette==0.41.3` directly, in addition to the existing
`fastapi==0.115.4` pin. Previously starlette was an unpinned transitive dep.

### Why
On 2026-05-28 the test suite failed to collect with
`TypeError: Router.__init__() got an unexpected keyword argument 'on_startup'`.
Root cause: the installed environment had drifted to `starlette==1.1.0`, the published
upstream **on the same day** (starlette 1.2.0 was released earlier in the day; 1.1.0 was
2026-05-23). `starlette` graduated from ZeroVer to 1.0 on 2026-03-22, with the package
moving from `encode/starlette` to `Kludex/starlette` on PyPI (Marcelo Trylesinski now
primary maintainer; Tom Christie co-maintainer). The 1.x line **removed**
`on_startup`/`on_shutdown` from `Router.__init__`, which FastAPI 0.115.x still forwards.

FastAPI 0.115.4 declares `starlette>=0.40.0,<0.42.0` in its `Requires-Dist`, so the broken
install can only happen on an env where pip ran without that constraint applied (drift via
an unrelated `pip install` that didn't reference the requirements file). The explicit pin
on starlette closes that drift path.

### Why not pip-tools / uv lockfile right now
The 2026 industry-standard answer for production Python dep management is `uv` with
`uv.lock` (cross-platform, auto-maintained, 10–100× faster than pip-tools), or `pip-tools`
(`requirements.in` → compiled `requirements.txt`) as the lower-friction alternative. Both
would prevent this category of bug structurally. We're deferring the tooling migration:
a hotfix for an SEV-0 collection failure shouldn't carry a CI/Dockerfile/dev-workflow
overhaul with it. **Re-evaluate when production K8s deployment lands (Issue 30)** — at
that point the operational case for a lockfile is unambiguous.

Until then, the rule is **explicit `==` pinning of every runtime-affecting transitive dep
in `requirements.txt`** as the minimum bar.

### Source / evidence
- `python3.12 -m pip show fastapi` reports `Requires-Dist: starlette<0.42.0,>=0.40.0`
- FastAPI 0.115.4 `pyproject.toml` on GitHub confirms the same constraint
- PyPI `starlette` project page (2026-05-28): latest 1.2.0, source repo
  `https://github.com/Kludex/starlette`, maintainers Marcelo Trylesinski + Tom Christie
- Industry references on 2026 dependency-management practice: Astral `uv` docs;
  Real Python "uv vs pip"; Cuttlesoft "Python Dependency Management in 2026";
  pydevtools handbook on pip-tools

### Verification
With `starlette==0.41.3` pinned and `pip install -r requirements.txt` re-run in a clean
venv, `pytest -q` runs the full suite to **313 passed, 7 deselected** (the 7 are
integration-marked tests excluded by `pytest.ini`'s `-m "not integration"`).

---

## 2026-05-28 — Issue 34: Per-video idempotency for minute deduction (SAVEPOINT + UNIQUE)

### What changed
A new `minute_deductions` ledger table (migration `0003_minute_deductions.py`,
model `MinuteDeduction`) is added with **`UNIQUE(video_id)`** as the idempotency key.
`billing.ledger.deduct_minutes(creator_id, duration_s, session)` is replaced by
`deduct_for_video(video_id, creator_id, duration_s, session)`, and `worker/tasks._ingest_async`
calls the new function with `video.id` + `video.creator_id`.

The new function:
1. Fast-checks for an existing deduction row (skip without opening a savepoint if found).
2. Opens `session.begin_nested()` (SAVEPOINT) wrapping two writes:
   - INSERT into `minute_deductions` + `session.flush()` to surface UNIQUE conflicts now.
   - `UPDATE creators SET minutes_balance = minutes_balance - n WHERE id = :cid AND minutes_balance >= n RETURNING`.
3. On `IntegrityError` (concurrent retry won the race) → roll back savepoint, return 0.
4. On insufficient balance → raise `HTTPException(402)` inside the savepoint, which auto-rolls back the INSERT.

### Why
Celery is configured with `task_acks_late=True` in `worker/celery_app.py`, which makes
delivery at-least-once: if a worker crashes after the deduction commits but before
acking the message, the broker redelivers and the task runs again. The previous
`deduct_minutes` had no per-video key — each retry just re-decremented the balance,
charging the creator 2–4× for a single video. The `UNIQUE(video_id)` constraint moves
the idempotency guarantee from "the application remembers" to "the database refuses",
which is the only durable place for a money primitive.

### Why a ledger table instead of `Video.minutes_charged_at`
`MinutePack` (existing) ledgers **grants in**. `MinuteDeduction` (new) ledgers **costs
out**. `Creator.minutes_balance` is the running total of both. This is the symmetric
design used by every customer-facing billing system (Stripe usage records, AWS billing,
Adyen). It also lets us answer "show my usage history for the last 30 days" with one
indexed query — `Video.minutes_charged_at` would have lost that audit trail.

### Why SAVEPOINT (`session.begin_nested`)
Two writes (deduction record + balance decrement) must succeed atomically. SAVEPOINT
makes them an undo unit *inside* the caller's larger transaction — the caller can
continue doing other work in the same transaction even when our two writes roll back.
This is the SQLAlchemy-2.0-async idiomatic pattern for "atomic sub-operation within
a larger flow."

### Industry standard checked
- **Stripe Idempotency-Key pattern** — store key + result on first call; replay returns
  stored result. The `MinuteDeduction.video_id UNIQUE` is the same pattern with
  `video_id` as the natural opaque key.
- **AWS "Designing Idempotent APIs"** — same model: client supplies an idempotency token,
  server uses a unique constraint to short-circuit duplicates.
- **Celery docs** explicitly state task idempotency is the caller's responsibility;
  `task_acks_late=True` + worker crashes make duplicates a *normal* occurrence, not an
  edge case.
- **Postgres UNIQUE + SAVEPOINT** vs. application-level locking — UNIQUE is the
  database's natural primitive when a key exists. We use both: UNIQUE for the
  idempotency guarantee, SAVEPOINT for atomicity between the two writes.

### Refund-on-permanent-failure deferred
If `_ingest_async` eventually exhausts all Celery retries after the deduction lands,
the creator paid for a permanently-failed ingest. That refund policy is a product
decision (refund threshold? automatic vs. support-initiated?) and is filed as
**Issue 57** in `docs/issues.md`. Today's exposure is small — ingest failures are
observable in logs and support can manually refund via `grant_minutes`.

### Verification
- `pytest -q`: **311 passed, 13 deselected** (was 313/9 — net -2 mocked deduct_minutes
  unit tests, +4 real-DB integration tests in `tests/test_billing_idempotency.py`).
- Integration tests assert: (a) sequential retry is idempotent, (b) two concurrent
  coroutines for the same video_id charge exactly once, (c) insufficient balance leaves
  zero ledger rows, (d) deduction record carries minutes + duration + timestamp.

### Source / evidence
- Stripe Idempotency docs; AWS Best Practices "Designing Idempotent APIs"
- SQLAlchemy 2.0 async docs: "Using SAVEPOINT with begin_nested"
- Celery docs: at-least-once delivery + `task_acks_late`
- Existing project precedent: `MinutePack` grants ledger (Issue 21)

---

## 2026-05-28 — Issue 42: ffmpeg/subprocess timeout formula

### What changed
Every `subprocess.run` call in `clip_engine/render.py` now has an explicit `timeout=`:

- `_run(cmd, label, timeout_s=120.0)` — optional float arg, passed directly to
  `subprocess.run(timeout=timeout_s)`; catches `subprocess.TimeoutExpired` and re-raises
  as `RuntimeError(f"ffmpeg {label} timed out after {timeout_s}s")`.
- `_frame_dimensions` — direct `subprocess.run(..., timeout=30)` hardcoded; ffprobe
  reads only container headers and should return in milliseconds on a healthy file.
- `_extract_keyframe` — threads `timeout_s: float = 120.0` through to `_run` so callers
  can pass the same budget as the render.
- `render_clip_file` — computes `render_timeout_s = max(120.0, duration * 4)` and passes
  it to both `_extract_keyframe` and the final render `_run` call.

### Timeout formula: `max(120, clip_duration_s * 4)`

**Why 4×**: libx264 `fast` preset on 1080p encodes at approximately real-time speed on
modern consumer hardware (i7/Ryzen with AVX2). 4× gives 3 full "real-time equivalents" of
headroom above the encode itself, covering disk I/O, container muxing, startup overhead,
and moderate system load. A 30s clip → 120s ceiling (floor kicks in). A 60s clip → 240s.
A 90s clip → 360s.

**Why floor at 120s**: Very short clips (< 30s) would get absurdly tight budgets with 4×
alone (e.g. a 10s clip would get only 40s). 120s is ample for any short ffmpeg invocation
regardless of clip length and matches the existing `LLM_TIMEOUT_SECONDS` default, making
it the project's "standard slow-operation timeout".

**Why ffprobe = 30s hardcoded**: ffprobe reads only the container header — it finishes in
milliseconds on any non-corrupt file. 30s is already 2–3 orders of magnitude more generous
than needed; threading the render timeout through would be misleading (the ffprobe call is
not proportional to clip length).

### What the error surfaces to
`_run` raises `RuntimeError` on timeout. The Celery render task's existing error handler
catches `RuntimeError` and sets `clip.render_status = failed`. No new error handling path
was needed.

### Source / evidence
- Python docs: `subprocess.run(..., timeout=N)` raises `subprocess.TimeoutExpired` after N
  seconds, which also sends `SIGKILL` to the child process.
- ffmpeg wiki on encode speed: "fast" preset encodes near 1× real-time for 1080p H.264 on
  modern x86 CPUs.
- Project precedent: `LLM_TIMEOUT_SECONDS` defaults to 120s in `config.py`.

---

## 2026-05-28 — Issue 41: Replace pickle with joblib + restricted unpickler allowlist

### What changed
`preference/model.py` — `to_bytes` / `from_bytes` now use **joblib** for serialisation
instead of raw `pickle`.  A new `_RestrictedUnpickler` class (subclass of
`joblib.numpy_pickle.NumpyUnpickler`) overrides `find_class` to enforce an explicit
allowlist of permitted `(module, name)` pairs.  `from_bytes` temporarily patches
`joblib.numpy_pickle.NumpyUnpickler` with `_RestrictedUnpickler` for the duration of
the `joblib.load` call, then restores the original.

No schema change — `preference_models.weights_blob` remains `bytes`.

### Why joblib over raw pickle
joblib is sklearn's officially documented serialisation format:
> "joblib.dump / joblib.load — use this for sklearn estimators as it handles
> large numpy arrays more efficiently than pickle" — scikit-learn User Guide §Model
> persistence.

It is already a transitive dependency (`scikit-learn → joblib`), so no new package
is needed.  Blobs written by `joblib.dump` are forward-compatible across
minor sklearn/joblib versions; raw pickle blobs are not.

### Why the allowlist is the load-bearing defence
joblib uses pickle internally — `joblib.load` without the restricted unpickler is
functionally identical to `pickle.loads` from a security standpoint.  The allowlist
closes the RCE surface by ensuring that `find_class` rejects any module or class
that is not in the pre-approved set, **before** any `__reduce__` / `__setstate__`
output is invoked.

### Allowlist derivation
The full `(module, name)` set was determined empirically by running a subclass of
`pickle.Unpickler` against real `joblib.dump` outputs for both `LogisticRegression`
and `LGBMClassifier`:

| Entry | Reason |
|-------|--------|
| `preference.model.PreferenceScorer` | The wrapper class itself |
| `sklearn.linear_model._logistic.LogisticRegression` | Cold-start model |
| `lightgbm.sklearn.LGBMClassifier` | Warm-start model |
| `lightgbm.basic.Booster` | LightGBM's internal tree model |
| `joblib.numpy_pickle.NumpyArrayWrapper` | joblib emits this for every ndarray |
| `numpy.ndarray` | Model weight arrays |
| `numpy.dtype` | Array dtypes |
| `numpy._core.multiarray.scalar` | Scalar numpy values |
| `collections.defaultdict` | LightGBM's internal param dict |
| `collections.OrderedDict` | LightGBM's internal param dict |

### Alternatives ruled out
- **HMAC envelope around raw pickle**: defers the attack surface instead of closing it.
  The blob still becomes RCE if the HMAC key leaks.  HMAC-only is the "if pickle truly
  cannot be removed" fallback the issue specified — joblib + allowlist is strictly
  stronger.
- **LightGBM native `.txt` format + sklearn JSON**: requires separate serialisation
  paths per model type, custom re-assembly of the `PreferenceScorer` wrapper, and
  additional validation of the sklearn JSON format.  More code surface for the same
  security property.

### Thread-safety note
The temporary `_jnp.NumpyUnpickler` patch is not thread-safe if two `from_bytes`
calls execute concurrently in the same process.  Celery workers are single-threaded
per-task (one task per process with the `prefork` pool), so this is safe in the
current architecture.  If the project ever switches to a threaded Celery pool or
calls `from_bytes` from async code, replace the patch with a thread lock.

### Verification
- `tests/test_preference.py` — 4 new tests:
  - `test_scorer_round_trips_joblib`: legitimate scorer survives to_bytes → from_bytes
    with identical `predict_score` output
  - `test_scorer_round_trips_preserves_label_count`: `label_count` attribute preserved
  - `test_tampered_blob_is_rejected`: joblib blob with `os.system` `__reduce__` raises
    `pickle.UnpicklingError("class not allowed: posix.system")`
  - `test_tampered_blob_arbitrary_global_rejected`: joblib blob with `subprocess.Popen`
    gadget raises `pickle.UnpicklingError("class not allowed: subprocess.Popen")`

### Source / evidence
- scikit-learn User Guide "Model persistence": https://scikit-learn.org/stable/model_persistence.html
- Python docs `pickle.Unpickler.find_class`: https://docs.python.org/3/library/pickle.html#pickle.Unpickler.find_class
- Python HOWTO "Restricting globals" pattern for safe unpickling
- joblib source: `joblib.numpy_pickle.NumpyUnpickler`, `_unpickle` (joblib 1.5.3)
## 2026-05-28 — Issue 35: Idempotent DNA build (SEV-0)

### Single-transaction commit for draft + embeddings + onboarding state

**What changed**: `dna/profile.create_draft`, `dna/embeddings.embed_patterns`, and
`dna/embeddings.embed_brief` each gained a keyword-only `commit: bool = True` parameter.
`worker/tasks._build_dna_async` now calls all three helpers with `commit=False` and issues
a single `await session.commit()` at the end of the function, after all three `session.add()`
chains are staged.

**Why**: The original code committed inside `create_draft` before calling the Voyage API for
embeddings. If the Voyage call raised (network error, quota exhaustion, etc.), Celery retried
the whole task. On retry, `create_draft` queried `max(version)` — which now returned the orphan
draft row — and inserted a new row at version+1. The root cause is a partial commit that left a
permanent row before the unit of work was complete.

The fix makes the database write atomic: if the Voyage call or any subsequent write fails, the
`AsyncSessionLocal` context manager's `__aexit__` calls `session.rollback()`, and no draft row
exists for the next retry to bump the version against.

**Alternatives ruled out**: Deleting the orphan on retry detection (fragile — requires detecting
partial state; race-prone). Using a SAVEPOINT to wrap the embeddings (overkill — the entire
`_build_dna_async` function is one logical unit of work; a single outer transaction is the
idiomatic choice).

**Backward compatibility**: `commit=True` is the default on all three helpers, so all existing
callers (`confirm_draft`, `routers/creators.py`, any future standalone call) continue to commit
immediately without code changes.

**Source**: Standard SQLAlchemy async unit-of-work pattern (defer commit to the outermost
caller that owns the transaction boundary). 2026-05-28.
## 2026-05-28 — Issue 40: Streaming upload — chunk size and RSS assertion bound

### Chunk size: 1 MB

**What**: `upload_video` reads `UploadFile` in 1 MB chunks into a `NamedTemporaryFile`, keeping
only the current chunk in memory at any one time.

**Why 1 MB**: Standard FastAPI / ASGI streaming guidance (Starlette issue #1746; python-multipart
docs) recommends chunk sizes between 512 KB and 4 MB. 1 MB is the midpoint — syscall overhead
is negligible (≤ 500 iterations for a 500 MB file), while the per-request heap ceiling is 1 MB
of upload data regardless of file size. Smaller chunks add syscall noise; larger chunks make the
heap ceiling proportionally higher. No project-specific tuning data exists at this stage, so the
industry midpoint was chosen.

**Source**: Starlette streaming docs; python-multipart FAQ; ASGI file-upload best practices.
2026-05-28.

### RSS delta assertion bound: 20 MB for a 100 MB rejected upload

**What**: `test_rss_delta_bounded_for_rejected_upload` asserts that `ru_maxrss` grows by no more
than 20 MB when a 100 MB upload is rejected.

**Why 20 MB**: With 1 MB chunks, only the current chunk (≤ 1 MB) should be live at any moment.
However, the Python runtime, test framework, OS buffer cache, and Starlette request internals
introduce measurement noise. The 20 MB ceiling is 20× the chunk size — tight enough to catch a
regression to bulk-read (which would show a ~100 MB delta) while loose enough to absorb normal
runtime overhead. This is a conservative bound; in practice the delta observed is 1–3 MB.

**Source**: `resource.getrusage` documentation (Linux: kilobytes, macOS: bytes); empirical
observation during implementation. 2026-05-28.

---

## 2026-05-28 — Issue 36: OAuth token lifecycle hardening (SEV-1)

### Revoke the refresh token, not the access token

**What**: `DELETE /auth/me` now POSTs the decrypted **refresh_token** to
`https://oauth2.googleapis.com/revoke`. A `400` with body `{"error": "invalid_token"}` or
`{"error": "token_revoked"}` is treated as success; other 4xx is logged but does not abort
account deletion.

**Why**: Revoking only the access token leaves the refresh token usable until the user
manually visits `myaccount.google.com/permissions` — an incomplete right-to-erasure and a
YouTube ToS gap. Google's OAuth 2.0 docs explicitly state revoking a refresh token
invalidates every access token derived from it, so one call suffices.

**Source**: Google OAuth 2.0 — Revoking a Token
(`developers.google.com/identity/protocols/oauth2/web-server#tokenrevoke`); OAuth 2.0
RFC 6749 §2.3.1.

### Discard the token row on `invalid_grant`

**What**: `youtube/oauth.py::get_valid_access_token` now deletes the `YoutubeToken` row +
commits when `refresh_access_token` returns `400 {"error": "invalid_grant"}`. Other 4xx
during refresh leaves the row in place (could be transient client misconfig).

**Why**: Per RFC 6749 §5.2, `invalid_grant` is a permanent error — the user has revoked
consent, the grant expired (6 mo unused), or a password reset with reauth invalidated it.
Re-attempting the refresh hourly was wasted quota and noisy logs. Deleting the row makes
the next call surface the existing "No OAuth tokens found — please reconnect" 401.

**Source**: OAuth 2.0 RFC 6749 §5.2; Google identity docs on refresh-token expiration.

### Classify 403 errors by `error.errors[].reason`

**What**: New `youtube/errors.py` defines `YouTubeAuthError(reason, status_code)` plus
`PERMANENT_403_REASONS` (authError, forbidden, accountClosed, accountSuspended,
accountDelegationForbidden, channelClosed, channelSuspended) and `TRANSIENT_403_REASONS`
(quotaExceeded, rateLimitExceeded, userRateLimitExceeded). `_get_json` in
`youtube/data_api.py` and `_fetch_report` in `youtube/analytics.py` now share a
`_classify_error()` helper: transient reasons + 429 still retry with exponential backoff;
permanent reasons + 401 raise `YouTubeAuthError` immediately, no retries.
`worker/tasks.py::_refresh_youtube_analytics_async` catches `YouTubeAuthError`, deletes
the creator's `YoutubeToken` row, commits, and continues to the next creator.

**Why**: Previously every 403 triggered four backoff retries — 7+ seconds of blocking and
four wasted quota hits per beat tick per revoked creator. Over time the daily beat loop
would consume a meaningful slice of the channel quota on creators who had revoked access.
The reason-based branching mirrors how `google-api-python-client` exposes
`HttpError.error_details` and how official YouTube samples branch on `reason`.

**"Mark creator disconnected" via token-row absence**: Rather than add a new
`OnboardingState.disconnected` enum value (which would require an Alembic migration), we
delete the `YoutubeToken` row. The existing `get_valid_access_token` already raises
`HTTPException(401, "No OAuth tokens found — please reconnect")`, and the beat loop's
prefix `try: get_valid_access_token ... except: continue` block then silently skips that
creator. A future issue can add a UI-visible `disconnected` state if the product needs it.

**Source**: YouTube Data API v3 — Errors reference
(`developers.google.com/youtube/v3/docs/errors`); Google APIs error model
(`developers.google.com/identity/protocols/oauth2/openid-connect#errors`); existing
worker skip-on-exception pattern in `worker/tasks.py:_refresh_youtube_analytics_async`.

---

## 2026-05-28 — Issue 45: Concurrent token refresh lock + Redis pool singleton (SEV-2)

### Per-creator Redis advisory lock in `get_valid_access_token`

**What changed**: `youtube/oauth.py::get_valid_access_token` now wraps the Google refresh
call with a per-creator Redis advisory lock (`SET refresh-lock:{creator_id} <uuid> NX EX 10`).

- **Lock acquired**: proceed with the existing refresh + DB commit, then release via a Lua
  compare-and-delete script that only deletes the key if the value still matches our token.
  This prevents a worker whose TTL expired mid-flight from deleting another worker's lock.
- **Lock not acquired**: poll up to 3 times with 200 ms sleeps, re-reading the
  `YoutubeToken` row each time. If the row's `expires_at` is now in the future by > 5 min,
  return its decrypted access token. If still expired after all retries, raise
  `HTTPException(503, "Token refresh in progress; please retry")`.

**Why SET NX EX over Redlock**: SET NX + a reasonable TTL (10s) is the canonical
single-node Redis distributed-lock pattern, documented in the official Redis SETNX page and
in "The Redlock algorithm" article. Redlock (multi-node quorum) is appropriate when Redis
itself is clustered; this project runs a single Redis instance so SET NX is correct and
significantly simpler. The Lua compare-and-delete (KEYS[1] == ARGV[1] → DEL) is the
canonical safe-release idiom from the Redis docs to prevent accidental release of another
client's lock if our TTL expires.

**Why 10s TTL**: One Google token-refresh round-trip completes in < 1s under normal
conditions. 10s gives 10× headroom before the lock auto-expires, covering network hiccups
and slow Google responses while still protecting against a worker crash leaving the lock
indefinitely. A shorter TTL risks expiring mid-refresh; a longer TTL extends the worst-case
stall for waiting workers.

**Why 200ms / 3-retry poll**: Total worst-case wait is 600ms — acceptable for an interactive
`/clips` request. Three retries avoids an infinite loop while giving the lock holder enough
time to complete the Google round-trip and DB commit.

**Source**: Redis SETNX docs (`redis.io/commands/setnx`); Redis "Distributed Locks with
Redis" article (`redis.io/docs/manual/patterns/distributed-locks`). 2026-05-28.

---

### Module-level Redis singleton in `youtube/_redis.py`

**What changed**: `youtube/quota.py` previously called `aioredis.from_url(...)` on every
`consume()` and `remaining()` call, creating a new connection-pool per call. A new helper
module `youtube/_redis.py` exposes `get_redis_client()` which initialises a single
`redis.asyncio.Redis` instance at first call and reuses it on all subsequent calls.
Both `youtube/quota.py` and `youtube/oauth.py` import from this module.

**Why singleton over per-call `from_url`**: `redis-py` 4.2+ creates an internal
`ConnectionPool` per `Redis` instance. Per-call `from_url` creates a new pool every time,
leaking connections and adding latency. The singleton pattern ensures one pool is shared
across the process — the standard recommendation in the redis-py docs and the pattern used
by every production redis-py deployment.

**Why a separate `_redis.py` module**: `oauth.py` and `quota.py` are separate concerns but
both need Redis. Putting the singleton in either one and importing from the other creates a
circular dependency risk. A dedicated `_redis.py` (underscore = package-internal) is the
clean DRY solution.

**Source**: redis-py docs "Connection Pools" section; PEP 8 on module naming conventions
for package-internal helpers. 2026-05-28.
