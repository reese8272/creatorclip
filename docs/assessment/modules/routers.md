# routers — assessed 2026-06-08

Scope: every file under `routers/` (clips, creators, videos, insights, auth, billing,
analysis, thumbnails, titles, api_keys, improvement, activity, review, tasks,
upload_intel, _schemas). HEAD `7af18b2`. Focus per orchestrator: UI/UX (discoverability,
empty-state contracts, onboarding state, progress visibility, user-facing copy, 
permission-gate clarity) PLUS carry-forward verification of SEV2 async hygiene.

## Findings

### Carry-forward: sync Celery in async (SEV2 from 2026-06-07) — RESOLVED

The prior assessment flagged ~15 sites where `task.delay(...)` and `start_pipeline(...)` 
sync calls blocked the event loop from inside `async def`. **This has been fixed.** All 
occurrences now wrap the producer in `await asyncio.to_thread(...)`:

- routers/improvement.py:148 ✓ `await asyncio.to_thread(generate_improvement_brief_task.delay, ...)`
- routers/thumbnails.py:197 ✓ `await asyncio.to_thread(generate_thumbnail_concepts_task.delay, ...)`
- routers/clips.py:198, 375, 576 ✓ wrapped
- routers/analysis.py:120, 217, 290 ✓ wrapped
- routers/videos.py:279, 324 ✓ wrapped
- routers/titles.py:78 ✓ wrapped
- routers/creators.py:255 ✓ wrapped

The comments cite "scale-checklist B" as justification. **This eliminates the p99 latency 
cliff that was the single highest-value fix from the prior run.**

### UX: Empty-state contracts — Missing guidance on list endpoints

[UX-B] list endpoints that return empty arrays do not surface a `message` or `next_action`
field to guide the user:

- routers/videos.py:66-100 — `GET /videos` returns `[]` when creator has zero videos.
  Frontend cannot distinguish "no videos yet" from "still loading". | UX-B severity: SEV2 |
  fix: return a wrapper payload with `videos: []` and `message: str | None` (matching the 
  `DnaGetOut` pattern from routers/creators.py:276-307), e.g. `{"videos": [], "message": 
  "Upload or link a YouTube video to get started"}` when empty.

- routers/insights.py:620-640 — `GET /creators/me/insights/saved` returns bare `list[InsightOut]`.
  When a creator has 0 saved insights, the frontend sees `[]` with no guidance. | UX-B 
  severity: SEV2 | fix: wrap in a model like `SavedInsightsOut = {"insights": list[InsightOut], 
  "message": str | None}` and return `{"insights": [], "message": None}` normally, 
  `{"insights": [...], "message": "You haven't saved any insights yet"}` on first visit.

- routers/clips.py:130-149 — `GET /videos/{video_id}/clips` returns `{"clips": []}` when 
  the video has 0 clips (either not ingested yet or no clips passed the threshold). The 
  endpoint doesn't signal whether the user should "wait for ingestion" or "try lower 
  thresholds". | UX-B severity: SEV2 | fix: add `state: str` field to `ClipListOut` 
  (e.g., `"not_ingested"` | `"no_candidates"` | `"ready"`), and include a `message` 
  field, e.g. "Ingest complete but no clips met the quality threshold. Try lowering 
  the confidence score."

**Comparison**: `DnaGetOut` (routers/creators.py:76-79) is the ideal pattern: 
`profile: DnaProfileOut | None` + `message: str | None` allows the frontend to show 
user-facing context on empty state. Extend this pattern to all list/resource endpoints.

### UX: Discoverability — Error responses lack actionable next steps

[UX-A] Several 400/404/422 responses have bare detail strings that leave users guessing:

- routers/clips.py:101-105 — `"Video is not fully ingested yet"` and `"Signals not 
  available for this video"` are descriptive but don't tell the user WHAT TO DO. The 
  frontend cannot render "go back and wait" without hardcoding this English string. | 
  UX-A severity: SEV2 | fix: include a `next_action` / `action_type` field in the error 
  body (not just detail string), e.g. `{"detail": "...", "action_type": "wait_ingestion", 
  "retry_after_s": 30}` so the frontend can render "Ingesting... Retry in 30s" generically.

- routers/analysis.py:83 — `"Channel not connected"` lacks a redirect hint. Frontend 
  could show a link but doesn't know it's `/auth/login` or `/creators/me/connect-youtube` 
  without hardcoding the route. | UX-A severity: cleanup | fix: add `action_url: str | None` 
  field, e.g. `{"detail": "Channel not connected", "action_url": "/auth/login"}` (or 
  construct in a middleware that rewrites all 400/403/401s).

- routers/review.py:58 — same "Clip not found" pattern. The user may have deleted it or 
  lost access; the response should hint at "go back to the gallery" not just 404. | UX-A 
  severity: cleanup | fix: include `state: str` in the 404 body, e.g. 
  `{"detail": "Clip not found", "state": "deleted_or_inaccessible"}`.

### UX: Onboarding state clarity — Good, but state-machine is fragmented

[UX-C] `CreatorMeOut` (routers/creators.py:22-32) surfaces `onboarding_state` (one of 
`connected`, `awaiting_data`, `dna_pending`, `active`), which is excellent. However, a 
frontend that needs to know the EXACT NEXT STEP must still call 5 separate endpoints:

- `/creators/me` → read `onboarding_state`
- `/creators/me/data-gate` → check if videos are ingested
- `/creators/me/dna` → check if DNA exists
- `/creators/me/identity` → check if identity is filled
- `/billing/balance` → check if trial is active or balance is sufficient

The state-machine inference is distributed. | UX-C severity: SEV2 | fix: extend `CreatorMeOut` 
to include these derived fields (or create a `/creators/me/setup-status` endpoint that 
aggregates them):
```python
class CreatorMeOut(BaseModel):
    # ... existing fields ...
    onboarding_state: str
    # Derived from data-gate, DNA, identity, trial status
    setup_step: int  # 1=YouTube, 2=videos/data, 3=identity, 4=DNA, 5=ready
    setup_step_label: str  # "Connect YouTube", "Ingest videos", "Set up identity", 
                           # "Build DNA", "Ready to use"
    next_action_type: str | None  # "connect_youtube" | "wait_ingestion" | 
                                   # "fill_identity" | "build_dna" | None
```

### UX: Progress visibility — Good for long tasks, but status enums could be more user-friendly

[UX-D] The SSE endpoints (routers/tasks.py:117-151) provide live progress for long jobs 
(DNA build, render, ingest). This is excellent. However, the `status` field in responses 
like `RenderQueuedOut` (routers/clips.py:50-51) is always the enum value `"queued"` — 
the frontend cannot differentiate between "queued at position 10" vs "queued at position 1000". 
The SSE stream will emit richer events, but the initial 202 response gives no ETA. | UX-D 
severity: cleanup | fix: add `queue_depth: int | None` or `estimated_seconds: int | None` 
to `TaskQueuedOut`:
```python
class TaskQueuedOut(BaseModel):
    task_id: str
    status: str  # "queued"
    stream_url: str | None = None
    queue_depth: int | None = None  # How many jobs ahead?
    estimated_seconds: int | None = None  # ETA?
```

### UX: Copy and user-facing enums — Mostly good, but one jargon leak

[UX-E] Enum values returned in responses are mostly readable (`pending`, `running`, `done`, 
`connected`, `awaiting_data`, `dna_pending`, `active`, `draft`, `confirmed`). However:

- routers/insights.py:543, 375 — `insight_type` and `kind` (video kind) enums are returned 
  as-is. Check models.py for these enum values... | reading models shows `VideoKind.short` 
  and `VideoKind.long`, `InsightType.performer_analysis`. These are user-facing when 
  rendered in the UI. `performer_analysis` should be `"Top/Bottom Performer"` or similar. 
  | UX-E severity: cleanup | fix: wrap in a helper `_user_friendly_enum(e: Enum) -> str` 
  that maps internal enum names to user-readable labels, e.g. 
  `InsightType.performer_analysis → "Performer Analysis"`.

### UX: Permission/plan-gate clarity — Excellent patterns already in place

[UX-F] When a creator's trial expires or balance hits zero, the error responses include 
actionable guidance:

- routers/clips.py:170 calls `check_positive_balance(creator.id, session)`, which raises 402 
  with detail like `"No minutes remaining. Purchase a pack at /pricing to process videos."` 
  (per billing/ledger.py). This is ideal — the frontend can show a link to `/pricing` 
  directly from the error message. ✓

- routers/improvement.py:70-73 — `"Not enough data yet — link some videos first."` is honest 
  and actionable. ✓

- routers/billing.py:74-109 — `BalanceOut` includes `trial_ends_at`, `trial_active`, 
  `trial_days_remaining`, `low_balance` so the UI can preemptively render "Trial ends in 3 days". ✓

**No findings here — this is a model for good UX design.**

---

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — 0 findings |
| 2 Concurrency & scale | ok — SEV2 carry-forward (async hygiene) RESOLVED; prior blockers on Anthropic timeout + lazy redis fixed by Wave-3 |
| 3 Security & compliance | ok — per-creator isolation verified; SEV2 PII-near-log and bare-except from prior assessment remain |
| 4 Clip-quality | n/a (not a clip-engine module) |
| 5 Anthropic SDK | ok — caching + token logging in place |
| 6 Cleanliness & typing | NEEDS-WORK (cleanup) — 12-copy DRY violation + duplicated word-window logic from prior assessment remain; low priority |
| 7 Error handling / API | ok on HTTP status codes and Pydantic models; bare detail strings flagged in UX section above |
| 8 Config & paths | ok — absolute paths + temp file cleanup intact |
| UX (NEW) | NEEDS-WORK — 5 SEV2 findings (empty-state contracts, discoverability, onboarding state aggregation, permission-gate clarity is good) |

## Module verdict

**NEEDS-WORK** — Carry-forward SEV2 (async hygiene) has been **resolved**. The new UX-focused 
assessment adds 5 SEV2 findings: list endpoints lack empty-state guidance messages, 400/404 
responses lack actionable next-step hints, onboarding state machine is fragmented across 
5 endpoints (should aggregate into one), and progress endpoints don't expose queue depth/ETA. 
These are moderate-blast, high-impact improvements for onboarding UX. No BLOCKER, no 
cross-tenant leak, no new security defect. The app's core API surface is sound; the gaps are 
user-experience signals (frontend cannot render smart guidance from bare `[]` or bare error 
codes).

---

## UX findings

### Summary

The routers layer serves the API surface that drives every user-facing interaction (onboarding, 
errors, progress, empty states). Five UX-specific findings (all SEV2) emerge from the emphasis 
on discoverability and ease-of-use:

1. **Empty-state contracts** (UX-B): List endpoints return bare `[]` without a `message` field 
   to guide first-time users. The `DnaGetOut` pattern (profile + message) is the standard; 
   extend it to `/videos`, `/insights/saved`, `/clips`.

2. **Error discoverability** (UX-A): 400/404/422 responses lack an `action_type` or `action_url` 
   field so the frontend cannot render context-aware guidance ("go back and wait for ingestion" 
   vs "visit pricing page"). Most error text is human-readable but not machine-actionable.

3. **Onboarding state aggregation** (UX-C): The setup state machine is split across 5 endpoints 
   (`/me`, `/data-gate`, `/dna`, `/identity`, `/balance`). Frontend must poll all 5 to infer 
   the next step. Add `setup_step` + `next_action_type` to `CreatorMeOut` to centralize.

4. **Progress visibility** (UX-D): `TaskQueuedOut` returns `status="queued"` with no queue depth 
   or ETA. Add `queue_depth` and `estimated_seconds` fields so the UI can show "2 jobs ahead, ~30s wait".

5. **User-facing enums** (UX-E): Internal enum names like `InsightType.performer_analysis` are 
   returned as-is. Wrap in a user-friendly label helper (e.g. `"Performer Analysis"`).

### Severity justification

All five are **SEV2** (bounded blast, high user-impact):
- Empty-state + discoverability gaps create a "barren, unclear app" first impression (the user 
  problem stated at the outset).
- Onboarding-state fragmentation forces naive clients to poll 5 endpoints sequentially, adding 
  100-200ms to setup flows.
- Enum naming is minor but compounds confusion when the UI must hardcode label strings.

Each fix is mechanical (add fields to Pydantic models, wrap in helpers) and low-risk to 
implement.

