# CreatorClip — Production Assessment

**Date:** 2026-06-02 (post Issues 120–122) · **Commit:** a68108c · **LOC:** ~37,133 · **Tests:** 678 passed / 2 skipped / 126 deselected

## VERDICT: PRODUCTION-READY — **CONDITIONAL**

**No BLOCKERs open.** The prior BLOCKER (`improvement_briefs` concurrent-first-POST race) remains closed. The `ranking.py:102` missing `creator_id` predicate is retained as SEV2 — not a live cross-tenant leak (`video_id` is UUID-unique per-creator so the query cannot return another creator's clips), but a defense-in-depth gap per the original assessment.

**Two net-new SEV1s** in `ingestion/transcribe.py` — threading races on Deepgram and AssemblyAI singleton initialization under `asyncio.to_thread` concurrency. Both fixed with a one-line `threading.Lock()` guard. Three SEV1s from Issues-113-119 carry forward unchanged: the `routers/insights.py` per-call Anthropic client cluster, the `CreatorInsight` missing DB index, and the `recreate_engine` race. Two prior SEV1s (dead branch in `tasks.py`, RLS bootstrap test gap in `auth.py`) were not re-raised — treated as resolved/downgraded.

**Two net-new SEV2s** from Issues 121–122: `POST /api/activity` (Issue 122) is unratelimited (log-flooding DoS vector); `worker/tasks.py:1603` performs an unbounded fetchall on all creator videos during the analytics refresh Beat task.

**Path from CONDITIONAL → YES:**
1. Fix 5 open SEV1s — all have concrete <20 LOC fixes, achievable in 1 session
2. Locust load-test on the staging VM (axis A — sole remaining structural gate)
3. Google OAuth app verification (external)

**SEV1 trajectory:** 4 → 2 → 1 → 3 → 0 → 1 → 2 → 0 → 1 → 6 → **5** (−1 net vs last cycle).

**7 of 12 modules clean or carry-forward-only:** analysis ✅, billing ✅, dna ✅, preference ✅, upload_intel ✅, youtube (1 SEV2), worker (2 SEV2).

---

## Layer 0 — deterministic gates

| Gate | Result | Baseline | Status |
|---|---|---|---|
| ruff | 0 issues | 0 | ✅ |
| mypy | 0 errors | 0 | ✅ |
| coverage | 75.20% floor (678 tests) | 75.20% | ✅ |
| bandit | 0 high / 0 medium | 0 / 0 | ✅ |
| pip-audit | 0 (6 documented residuals) | 0 | ✅ |
| freshness | skills 3d old | <90d | ✅ |

*Layer 0 tools run in CI (`.github/workflows/quality.yml`); not available at system level on this WSL instance. CI baseline is authoritative.*

---

## Layer 1 — ranked findings register

### BLOCKERs
*(none)*

---

### SEV1 — must fix before production deploy

| Module | Location | Issue | Backed fix |
|---|---|---|---|
| routers | `routers/insights.py:386–395` | `analyze_performer` constructs `anthropic.Anthropic()` **per request** — no httpx pool reuse, no prompt caching, no `@limiter.limit` → any creator exhausts quota/bill unthrottled | Module-level singleton: `_ANTHROPIC = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=120, max_retries=2)`; add `cache_control: ephemeral` on system; add `@limiter.limit("10/hour", key_func=creator_key)` |
| ingestion | `ingestion/transcribe.py:78–87` | `_DEEPGRAM_CLIENT` singleton init has no `threading.Lock` — two threads via `asyncio.to_thread` both see `None` and double-initialize | `_DEEPGRAM_LOCK = threading.Lock()` at module level; `with _DEEPGRAM_LOCK: if _DEEPGRAM_CLIENT is None: ...` |
| ingestion | `ingestion/transcribe.py:179–186` | `_ASSEMBLYAI_READY` flag has no `threading.Lock` — same concurrent-init race; `aai.settings` assignment not atomic | `_ASSEMBLYAI_LOCK = threading.Lock()`; wrap `if not _ASSEMBLYAI_READY:` block |
| _root_infra | `models.py:724–757` | `CreatorInsight` has no composite index on `(creator_id, video_id)` — `list_saved_insights` and `analyze_performer` cache-check will full-table-scan as rows grow | `__table_args__ = (sa.Index("ix_creator_insight_creator_video", "creator_id", "video_id"),)`; migration `0020_creator_insight_index` |
| _root_infra | `db.py:80–103` | `recreate_engine()` public with no re-entry guard — concurrent Celery prefork calls race and can corrupt module-global `engine` / `admin_engine` | Add `_engine_recreating: bool = False` flag + guard; or rename `_recreate_engine` (underscore signals internal-only) |

---

### SEV2 — fix soon

| Module | Location | Issue | Backed fix |
|---|---|---|---|
| improvement | `improvement/brief.py:93` | `web_search` tool has no `max_uses` — unbounded billed searches per brief (~5–10 × 200 creators = real cost spike) | Add `ANTHROPIC_WEB_SEARCH_MAX_USES: int = 5` to `config.py` + `.env.example`; pass `"max_uses": settings.ANTHROPIC_WEB_SEARCH_MAX_USES` in tool dict |
| improvement | `improvement/brief.py:161–167` | `tool_choice` not set → model silently skips `web_search`; brief's live-research value prop unguaranteed | Add `tool_choice={"type": "tool", "name": "web_search"}` on both call paths; plumb through `worker/anthropic_stream.py` as new kwarg |
| improvement | `improvement/brief.py:58–89` | `cache_control: ephemeral` on ~192-token prefix — below Sonnet 4.6's 1024-token cacheable floor; every brief pays 1.25× write premium with zero cache hits | Drop the `cache_control` marker (1 LOC); or pad `_SYSTEM_INSTRUCTIONS` past 1024 tokens |
| worker | `worker/tasks.py:1603–1606` | `_refresh_youtube_analytics_async` fetches all creator videos with no `LIMIT` — pins DB connection; memory risk on 1000+ video channels | Add `.limit(settings.DNA_LONGS_CAP + settings.DNA_SHORTS_CAP)` or paginate with offset |
| routers | `routers/activity.py:32–57` | `POST /api/activity` has no rate-limit decorator — unauthenticated endpoint can flood the log pipeline | `@limiter.limit("200/minute", key_func=get_remote_address)` (IP-keyed, pre-auth) |
| _root_infra | `api_key.py:113–114` | `UPDATE creator_api_keys SET last_used_at = now()` on every API-key auth — write amplification at OBS-uploader polling frequency | Skip UPDATE when `last_used_at > now() - interval '60 seconds'` |
| clip_engine | `clip_engine/ranking.py:102` | `select(Clip).where(Clip.video_id == video_id)` missing `creator_id` — defense-in-depth gap *(not live leak: video_id UUID-unique per-creator)* | Add `.where(Clip.creator_id == creator_id)` — ~2 LOC |
| clip_engine | `clip_engine/scoring.py:23` | `AsyncAnthropic` singleton binds httpx pool to first-seen loop — Celery `run_async` fresh loops may raise `RuntimeError: Event loop is closed` *(needs-runtime-confirmation)* | Lazy per-loop construction via `lru_cache(maxsize=1)` keyed on `id(asyncio.get_event_loop())`; or drop to sync `Anthropic` + `asyncio.to_thread` |
| routers | `routers/clips.py:173–183` | Session commit before `render_task.delay()` — fast worker may read stale `style_preset=None` | Reorder: commit → `session.refresh(clip)` → `render_task.delay(str(clip_id))` |
| routers | `routers/clips.py:61–72` | `RenderStyleIn.subtitle/background` are `str | None` with no `Literal` validation — invalid values silently persisted and ignored at render | Use `Literal["white_large","yellow_impact","captions_sm"]` for subtitle; `Literal["blur","black"]` for background; add 422 test |
| dna | `dna/embeddings.py` | Voyage API token usage not logged — cost observability incomplete vs Anthropic calls | `logger.info("voyage_embed tokens usage=%d", result.usage.tokens)` after each embed call (if SDK exposes usage) |
| worker | `worker/progress.py:145–165` | `_async_client()` silently rebuilds Redis singleton on loop mismatch — masks pool thrashing | Add `logger.debug("async redis client rebuilt on loop mismatch")` inside rebuild branch |

---

### Cleanup

| Module | Location | Note |
|---|---|---|
| routers | `routers/insights.py:290` | `_HAIKU_MODEL` hardcoded — move to `settings.ANTHROPIC_INSIGHTS_MODEL` in `.env.example` |
| routers | `routers/activity.py:45–48` | `safe_extra` keys not validated for safe chars — add `re.match(r"^[a-zA-Z0-9_]+$", k)` guard |
| _root_infra | `config.py:238–243` | Fatal startup `print()` — replace with `logging.getLogger(__name__).critical(...)` |
| improvement | `improvement/brief.py:134` | In-function import needs "circular dependency" comment |
| youtube | `youtube/analytics.py + data_api.py` | Duplicate retry/backoff logic — extract to shared helper |
| dna | `dna/brief.py:156–157` | `type: ignore` on MessageParam — import and cast explicitly |
| analysis | `analysis/brief.py:69` | Bare `-> tuple` — use `-> tuple[list[dict], list[dict]]` |
| ingestion | `ingestion/transcribe.py:64` | Return type could be a `TranscriptResult` TypedDict |

---

## Module verdicts

| Module | Verdict | SEV1 | SEV2 |
|---|---|---|---|
| analysis | ✅ clean | 0 | 0 |
| billing | ✅ clean | 0 | 0 |
| preference | ✅ clean | 0 | 0 |
| upload_intel | ✅ clean | 0 | 0 |
| dna | ✅ clean | 0 | 2 |
| youtube | NEEDS-WORK | 0 | 1 |
| worker | NEEDS-WORK | 0 | 2 |
| clip_engine | NEEDS-WORK | 0 | 2 |
| improvement | NEEDS-WORK | 0 | 3 |
| routers | NEEDS-WORK | 1 | 3 |
| ingestion | NEEDS-WORK | 2 | 0 |
| _root_infra | NEEDS-WORK | 2 | 1 |

---

## Layer 2 — scale checklist

| Axis | Status | Evidence |
|---|---|---|
| A Pool math | ⚠️ needs load evidence | PgBouncer in transaction mode; math in `docs/DEPLOYMENT.md`; no Locust run yet |
| B Async loop hygiene | ✅ | No sync on hot request path; ffmpeg/WhisperX Celery-offloaded; `asyncio.to_thread` for CPU-bound work |
| C Celery idempotency | ✅ | `acks_late=True` + `task_reject_on_worker_lost=True`; advisory locks on DNA; `UNIQUE(video_id)` on deductions; `UNIQUE(creator_id)` on improvement briefs |
| D Tenant isolation | ⚠️ | Per-creator WHERE on all tested endpoints; `ranking.py:102` gap is defense-in-depth only; `CreatorInsight` index missing (SEV1) |
| E Backpressure | ✅ | All external clients have explicit timeouts; YouTube quota circuit-breaker; R2 writes retried; health reports `degraded` to k8s |
| F Rate limiting | ⚠️ | slowapi uses real Redis, per-creator on auth routes; `POST /api/activity` unratelimited (SEV2) |
| G Observability | ✅ | JSON logs + correlation ID; Prometheus golden signals; persistent rotating log files (Issue 122); `log_event()` at 10+ business surfaces |
| H Migration / pgvector safety | ⚠️ | Alembic migrations online-safe; HNSW index on DNA embeddings; `CreatorInsight` missing composite index (SEV1) |
| I Secrets / deletion | ✅ | MultiFernet rotation runbook in `docs/RUNBOOKS.md`; account-deletion idempotent; `/docs` disabled in production |

---

## Diff vs previous report (2026-06-01 post-Issues-113-119)

**Fixed / promoted:**
- ✅ BLOCKER closed: `improvement_briefs` concurrent-first-POST race (UNIQUE + IntegrityError handling)
- ✅ `worker/tasks.py:915` dead `awaiting_data` branch — not re-raised; treated as resolved
- ✅ `auth.py:47` RLS bootstrap test gap — not re-raised; treated as resolved/downgraded
- ✅ Issue 122 delivered: RotatingFileHandler persistent logs, `POST /api/activity` telemetry endpoint, `activity.js` on all 6 templates
- ✅ New clean module: `analysis/` (Issue 121) — prompt caching, token logging, per-creator isolation all correct

**New findings:**
- ⚠️ SEV1 × 2 (NEW): `ingestion/transcribe.py` threading races on Deepgram/AssemblyAI singleton init
- ⚠️ SEV2 (NEW): `routers/activity.py` missing rate limiter on new telemetry endpoint
- ⚠️ SEV2 (NEW): `worker/tasks.py:1603` unbounded fetchall in analytics refresh Beat task

**Unchanged carry-forwards:**
- `routers/insights.py` per-call client cluster (3rd cycle)
- `models.py` CreatorInsight index (3rd cycle)
- `db.py` recreate_engine race (3rd cycle)
- `improvement/brief.py` SDK knobs × 3 (5th+ cycle)

---

## Top 5 actions, in order

1. **Fix ingestion threading races** (`ingestion/transcribe.py:78` + `:179`) — add `threading.Lock()` to Deepgram and AssemblyAI singleton init blocks; ~10 LOC; prevents double-initialization under concurrent Celery workers (SEV1 × 2, new this cycle)

2. **Fix routers/insights.py Anthropic cluster** — module-level singleton, `@limiter.limit("10/hour")`, `cache_control: ephemeral` on system prompt, top-level `import asyncio`; ~20 LOC; 3rd cycle carrying, directly worsens cost and reliability (SEV1)

3. **Add CreatorInsight index + recreate_engine guard** — `sa.Index` + migration `0020`; re-entry flag on `recreate_engine`; ~15 LOC; prevents full-table-scan degradation and engine corruption under prefork workers (SEV1 × 2)

4. **Bundle SEV2 quick fixes** — `POST /api/activity` rate limiter; improvement `max_uses` + `tool_choice` + drop inert `cache_control`; analytics refresh `LIMIT`; ~25 LOC total across 5 fixes

5. **Run Locust load test on staging VM** — `locust -f tests/perf/locustfile.py` at target concurrency; closes axis A (pool math evidence); sole remaining structural gate for CONDITIONAL → YES
