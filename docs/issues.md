# CreatorClip — Work Queue

**Reset 2026-08-03.** The previous tracker reached 7,096 lines / 212 briefs and stopped working as a
queue. Archived verbatim at `docs/issues-archive-2026-08-03.md`; rationale in `docs/DECISIONS.md`
(2026-08-03). This file is the live queue.

**Active lane: L26 — A→B Auto-Clipping MVP (Issues 414–426).** Declared 2026-08-04 (user decision;
`docs/DECISIONS.md` 2026-08-04): the MVP viability criterion is "the best auto-clipping short creation
on the planet," and three verified gaps block it — the LLM never reads the whole video, the crop is a
static single-keyframe frame, and the 9:16 short occupies ~9% of the Review workspace. **L25 Batch C is
PAUSED, not cancelled** — #394 re-targets the new ShortStage overlay slot, #396 becomes the manual
escape hatch *after* auto-reframe ships, #399 is unaffected. Full design in the approved plan
(session 2026-08-04) and the DECISIONS entry.

Prior lane state: **L25 Batches A & B are 100% CLOSED** (384–392, 400, 406–413 — merged & deployed;
see `docs/PROJECT_STATE.md`).

**Also closed, outside the lane:** **#406** ✅ — the 6 `pip-audit` advisories (aiohttp,
cryptography), merged as PR #72. See § Hygiene below.

> **Batch B order was changed at build time to 389 → 392 → 390 → 391** (user decision, 2026-08-03).
> #392 is the batch's only live honesty-constraint violation, it is pure backend (zero overlap with
> the frontend files the others rewrite), and it hands #390 the final waveform data contract *before*
> #390 designs its zoom/LOD renderer — landing it last would force a second pass over code #390 had
> just written. #389 also turned out to be a soft prerequisite for #390: "zoom-to-fit" and "playhead
> stays in view" are not demonstrable in a scrolling document where the timeline itself scrolls away.

Filed from the 2026-08-03 review of the
editor and presentation layer against the 2026 field. Every issue below carries: what we're doing,
the analysis behind it, in-repo evidence (file:line or a committed screenshot), and the external
sources that set the bar. Research links are listed inline per issue and collected in
**§ Source index** at the end.

---

## The finding in one paragraph

The AI engine is beta-ready and genuinely differentiated — 25 LLM-touching modules, DNA-relative
scoring that refuses to fake a virality number, and a face-tracked time-varying reframe
(`clip_engine/reframe.py`) better than most shipped auto-reframe. The presentation layer is
undermining it. The "Editor" supports exactly one edit operation (delete a time range); every
control is a raw unstyled HTML element; there is no icon library in the dependency tree; and the
primary input path caps at 500 MB with no resume, which rejects most real creator footage. None of
this is architecturally wrong — it is unfinished in a specific, fixable way, and Batch A alone
changes the gut reaction to the product.

## Batches

| Batch | Theme | Issues | Size |
|-------|-------|--------|------|
| **A** ✅ | Visual credibility — stop reading as a prototype | 384–388, **400** — **ALL DONE 2026-08-03** | days |
| **B** ✅ | Make it an application, not a webpage | **389 ✅ · 392 ✅ · 390 ✅ · 391 ✅** — **BATCH COMPLETE 2026-08-04** | 1–2 weeks |
| **C** | Close the capability gap | 393–397, **401** | multi-week |
| **D** | Asset management | 398–399, **402** | ~1 week |
| **E** | Breadth — scope-call cluster, do not start before D closes | **403–405** | multi-week |

Batches run in order. **Batch E is filed but explicitly not funded** — see the scope note above it.

---

# Lane L26 — A→B Auto-Clipping MVP (Issues 414–426)

**The finding in one paragraph.** The pipeline works mechanically but the "intelligence" is thinner
than it looks: clip candidates come from scipy audio-energy peak-picking and the only LLM call sees
~600 chars per candidate — it can score, never propose, so a great story delivered flat is invisible.
Titles are on-demand clicks, descriptions have no generator at all (`publish` falls back to
`"#Shorts"`), the production crop is one Haar face at the clip midpoint frozen for the whole clip
(the dynamic reframe in `clip_engine/reframe.py` is flag-off, largest-face-only, never verified), and
the 9:16 short is ≈260×470px in a 1440×900 workspace. L26 closes all three gaps in three parallel
tracks. Design: approved plan 2026-08-04 + `docs/DECISIONS.md` (2026-08-04, L26 entry).

**Tracks & merge order** (A → B rebase → C; A/B share `worker/tasks.py`, `routers/clips.py`,
`config.py`, `models.py`):

| Track | Theme | Issues | Branch |
|-------|-------|--------|--------|
| **A** | Intelligence: whole-video context → hybrid candidates → batched metadata | 414–417 | `lane/l26-intel` |
| **B** | Speaker-aware dynamic crop: diarization → shots → speaker map → cut/pan | 418–422 | `lane/l26-crop` |
| **C** | Short-first unified UI: ShortStage, Review+Editor flip, crop overlay | 423–426 | `lane/l26-stage` |

Binding cross-track contracts (crop-track wire shape, migration numbering 0053–0055, additive
transcript `speaker` field, SSE stage-label tolerance) are in the DECISIONS entry.

---

### Issue 414: Transcript-window helper + clip-titles window bug fix
- [x] **Status:** DONE 2026-08-04 · **Track:** A · **Size:** S · **Depends:** —

**What.** New `knowledge/util.py::extract_transcript_window(segments_jsonb, start_s, end_s,
max_chars=1200)` (midpoint-assignment, the `scoring.py` rule), then fix `routers/clips.py:1775`:
title-suggestions currently ground in the WHOLE video transcript truncated to 1500 chars
(`knowledge/clip_titles.py:49`), not the clip's own window — a real fidelity bug for any late clip.

**Acceptance**
- [x] `extract_transcript_window` unit-tested (missing transcript, empty window, cap behavior)
- [x] `/clips/{id}/title-suggestions` grounds in the clip's `[setup_start_s ?? start_s, end_s]` window;
      test asserts the LLM payload contains window text (not minute-0 text) for a late clip
- [x] Existing clip-titles tests stay green

### Issue 415: Whole-video context pass (VideoContext table + task + chain)
- [x] **Status:** DONE 2026-08-04 · **Track:** A · **Size:** M · **Depends:** —

**What.** New chain member: `ingest | transcribe | analyze_video_context | build_signals`. The task
reads the FULL transcript (rendered as ~30s paragraphs with `[512s]` markers — one call, no chunking;
120 min ≈ 26K tok) + creator identity (`dna/identity.py::format_for_prompt`) + DNA brief, and stores a
validated `context_jsonb` (`summary`, `structure`, `narrative_arcs`, `tone`, `audience_relevance`,
`moments[]` with principle citations) in a new 1:1 `VideoContext` table (migration **0053**, 0044 RLS
pattern verbatim). Structured output forced; prompt-cache blocks per Issue-315 discipline; model
`ANTHROPIC_MODEL_VIDEO_CONTEXT=claude-sonnet-4-6`. **The task can never fail the chain**: any
LLM/parse failure logs, emits `context_skipped`, returns — clips generate signal-only as today.

**Acceptance**
- [x] Chain shape updated; `VIDEO_CONTEXT_ENABLED=false` short-circuits to today's pipeline exactly
- [x] Row persisted with schema-valid `context_jsonb`; ≤ `LLM_CANDIDATES_MAX=4` validated moments
      (bounds-clamped, principle ∈ the 12, invalid dropped)
- [x] Mocked LLM 5xx/parse failure → chain completes, clips still generate
- [x] Redelivery is a no-op (PK check-then-insert; no double spend); spend-guard skip emits `context_skipped`
- [x] Usage billed via ledger after the round-trip (scoring.py pattern); cache marker floor-gated
      (byte-identity test per `test_brief_caching.py` style)
- [x] RLS policy present; migration up/down smoke; new config keys in `.env.example`

### Issue 416: Hybrid candidate merge — LLM moments ∪ signal peaks (the engine change)
- [x] **Status:** DONE 2026-08-04 · **Track:** A · **Size:** L · **Depends:** 415

**What.** `extract_candidates` stays byte-untouched (eval harness green by construction). New pure
layer `clip_engine/merge.py`: `llm_moments_to_candidates()` (sentence-snap via existing
`snap_to_sentence_boundary`, `peak_s` = signal argmax in window else midpoint, candidates.py
invariants + `MIN_CLIP_S` enforced, tagged `origin:"llm"`) + `merge_candidates()` (signal-priority
NMS, IoU>0.5 suppression, chronological). `ranking.py::score_and_rank` gains optional
`video_context`; merged pool (≤8 signal + ≤4 LLM) scored in the ONE existing Sonnet call;
post-ranking trim to `CLIPS_PER_VIDEO_DEFAULT=8` — weak candidates displaced on merit. Scoring:
payload gains `origin`/wrapped `llm_reason`; `_SYSTEM_STATIC` gains one origin line; `_PRINCIPLES`
gains missing #12 "Clean Context Boundary"; `max_tokens` 1200→1800; cold-start rule
`max(_signal_score, 0.8·llm_confidence)` for llm-origin. Provenance in `signals_jsonb["origin"]`.

**Acceptance**
- [x] All existing eval scenarios pass unmodified; `extract_candidates` diff empty
- [x] Flat-energy fixture story → persisted `origin:"llm"` clip through score→rank→persist (mocked LLM)
- [x] Overlapping LLM candidate (IoU>0.5) suppressed; trim caps rows at 8
- [x] Cold-start rule + 12-citable-principles pinned by tests
- [x] 3 new `kind: merge` eval scenarios (flat-energy admitted / overlap dedupe / invalid dropped);
      `SCENARIO_FLOOR` 15 → **18**
- [x] Absent context row ⇒ byte-identical behavior to today

### Issue 417: Batched auto-metadata — suggested title/description/hook for every clip
- [x] **Status:** DONE 2026-08-04 · **Track:** A · **Size:** M · **Depends:** 414, 415

**What.** New `knowledge/clip_metadata.py::generate_clip_metadata_batch()` — ONE structured-output
Sonnet call for all ranked clips (static honesty rubric + cached DNA block + uncached video-context
summary + per-clip payloads each grounded in its OWN transcript window). Python clamps: title ≤100
chars, description ≤5000 UTF-8 bytes + `#Shorts` + no angle brackets, hook ≤200. Migration **0054**:
`clips.suggested_title/description/hook` + `suggestions_generated_at`. `applied_*` stays
creator-typed; publish fallback becomes `applied_* or suggested_* or (video.title | "#Shorts")`. New
sibling task `generate_clip_metadata` enqueued after `persist_ranked_clips` commits, parallel with
render; idempotency filter `suggested_title IS NULL`. On-demand endpoints unchanged (= Regenerate).
`ClipOut` gains the three `suggested_*` fields.

**Acceptance**
- [x] Post-pipeline, every ranked clip has non-NULL suggested_* (mocked-LLM test through
      `_generate_clips_async`); exactly ONE LLM call per video
- [x] Each clip's payload contains only its own window text; clamps pinned by tests
- [x] Publish fallback order pinned (worker test); redelivery fills only NULL rows
- [x] LLM-down ⇒ render completes, columns NULL, clips usable; billed + spend-guard-gated
- [x] No-virality structural scan green on new prompt strings; `AUTO_CLIP_METADATA=false` disables
- [x] SSE emits non-terminal `metadata_ready`; OpenAPI/router-surface snapshots updated

### Issue 418: Speaker diarization in transcription
- [x] **Status:** DONE 2026-08-04 (branch `lane/l26-crop`, pending L26 merge) · **Track:** B · **Size:** S · **Depends:** —

**What.** `diarize=settings.TRANSCRIPTION_DIARIZE_ENABLED` (new kill-switch, default true) on the
Deepgram request — SDK 3.7.7 verified to have the field (no repeat of the `words=True` outage).
`_normalize_deepgram()` gains **additive** `speaker`/`speaker_confidence` on words, `speaker` on
segments (keys omitted when absent). AssemblyAI parity via `speaker_labels=True` + letter→int map;
WhisperX degrades gracefully. No migration (schemaless JSONB).

**Acceptance**
- [x] Diarized-fixture normalization tests (Deepgram + AssemblyAI); missing-speaker tolerance
- [x] Consumers-ignore regressions: captions + filler byte-identical on speaker-bearing transcripts
- [x] Real-SDK kwargs-validation test extended to `diarize`
- [x] Deepgram diarization surcharge checked: +$0.0020/min add-on (deepgram.com/pricing 2026-08-04)
      → `COST_PER_MIN_DEEPGRAM` 0.0077→0.0097, `PRICE_BOOK_VERSION` bumped, DECISIONS entry

### Issue 419: Shot-change detection module
- [x] **Status:** DONE 2026-08-04 (branch `lane/l26-crop`, pending L26 merge) · **Track:** B · **Size:** S · **Depends:** —

**What.** New `clip_engine/shots.py`: `detect_shot_changes()` via one downscaled ffmpeg `scdet` pass
over the clip window (`scale=320:-2,scdet=threshold=10`, parse `lavfi.scd.time` from stderr — zero
new Python deps; amends the 2026-06-23 PySceneDetect pencil-in); histogram-diff fallback over the
existing 5fps samples; total failure → `[]` = one shot (safe).

**Acceptance**
- [x] `_parse_scdet_output` unit-tested on captured stderr fixture (REAL ffmpeg 8.1.2 capture,
      `tests/fixtures/scdet_stderr.txt`); histogram fallback tested
- [x] Contract documented: tracks never span shot boundaries; EMA resets; never pan across a source cut

### Issue 420: Face tracks + speaker→face mapping + cut/pan planner
- [x] **Status:** DONE 2026-08-04 (branch `lane/l26-crop`, pending L26 merge) · **Track:** B · **Size:** L · **Depends:** 418, 419

**What.** New `clip_engine/speaker_map.py` (pure, synthetic-testable without mediapipe): greedy
nearest-neighbor face tracks per shot; speaker turns (gap-merge <0.4s, backchannel absorb <0.8s);
mouth-motion energy from BlazeFace's mouth keypoint on already-decoded frames; weighted vote
speaker→track assignment with margin-ratio confidence. **Fallback ladder** `speaker_cut → face_pan →
static` — off-screen speaker holds framing; bad mapping degrades, never worse than today. In
`reframe.py`: `_detect_face_obs()` (+keypoints), **single `cv2.VideoCapture` sequential-grab
refactor** (mandatory — per-sample open/seek is the latency killer), `plan_crop_directives()` (cut on
speaker change ≥0.8s turn + |Δcx| ≥0.25·frame_w + ≥1.2s spacing; J-cut 150ms lead; snap to shot
boundary ±300ms; always cut at source cuts; suppress inside punch-in pulse), segmented EMA smoothing,
`compute_dynamic_crop() -> ReframeResult`. Cuts are unsmoothed sendcmd value jumps — no new filters.
Cleaned/summary render paths explicitly keep static crops (follow-up filed).

**Acceptance**
- [x] All synthetic unit tests green **without mediapipe installed** (`test_speaker_map.py` 41,
      `test_reframe_planner.py` 25 — planner thresholds, ladder rungs, off-screen hold,
      segmented smoothing, sendcmd jumps)
- [x] Config: `REFRAME_CUT_ENABLED`, `REFRAME_MIN_SHOT_S=1.2`, `REFRAME_CUT_MIN_TURN_S=0.8`,
      `REFRAME_CUT_MIN_DISTANCE_FRAC=0.25`, `SHOT_DETECT_SCDET_THRESHOLD=10.0` in `.env.example`
- [x] Flag-off behavior byte-identical to today (render.py untouched this issue; pinned in 421's
      flag-off test — vf chain identical + `None` return)

### Issue 421: Render integration + crop-track persistence + API
- [x] **Status:** DONE 2026-08-04 (branch `lane/l26-crop`; 0055 re-parented onto 0054 at L26
  merge) · **Track:** B · **Size:** M · **Depends:** 420

**What.** Migration **0055**: nullable `clips.reframe_track_jsonb` (NOT in ClipEditDocument — CAS
conflict; #396 layers on top later). Track recomputed every render, persisted in the done-marking
transaction; `_load_clip_render_plan` loads transcript whenever the reframe flag is on. **Unified
wire contract** at `GET /clips/{clip_id}/crop-track` (404 `no_crop_track`): `{version, mode,
source:{width,height}, crop:{width,height}, origin_s, duration_s, keyframes:[{t,x}] (x = clamped
LEFT edge, exact sendcmd values), cuts:[{t,from_x,to_x,speaker?}], shots, speakers, meta}`.
`has_crop_track` on ClipOut. Re-render replaces or deletes the track — never stale.

**Acceptance**
- [x] Track returned by `render_clip_file`, persisted, served; 404 for pre-pipeline clips
- [x] Geometry shared: endpoint x-values are the sendcmd values (one definition; pinned by
      `test_keyframe_x_are_the_exact_sendcmd_values`)
- [x] Flag-off renders byte-identical; multi-segment paths unchanged (cleaned/summary keep
      static crops); migration up/down smoke run against a throwaway local PG16 DB
      (0052→0055→0052→0055, column jsonb NULLABLE verified)

### Issue 422: Worker image (mediapipe) + staging rollout + flag flip
- [x] **Status:** DONE 2026-08-05 — step-0 resolved (mediapipe 1.0.0), all four unlock
  criteria closed with live evidence on the 6c221f12 two-speaker fixture, and the reframe
  is LIVE in prod (`ACTIVE_SPEAKER_REFRAME_ENABLED=true`, floor
  `REFRAME_MIN_MAPPING_CONFIDENCE=0.2`; DECISIONS 2026-08-05 evening reversal entry) ·
  **Track:** B · **Size:** M · **Depends:** 421

**What.** `requirements-image.txt` (`-r requirements.txt` + `mediapipe==1.0.0` +
`opencv-contrib-python==4.13.0.92`), Dockerfile installs it by default; local dev untouched
(lazy imports). Flag sequencing: neutral deploy → `TRANSCRIPTION_DIARIZE_ENABLED=true` →
staging reframe-on/cuts-off (verify pan rung) → staging cuts on vs a real 2-speaker fixture →
prod flip. Closes **Issue 189's four unlock criteria with recorded evidence**; per-stage vlog
timings (budget est. +12–24s per 60s clip vs 240s timeout).

**Acceptance**
- [x] Image builds with mediapipe + model asset verified (unlock #1 — 2026-08-05, mediapipe 1.0.0 + live FaceDetector in prod worker)
- [x] Real 2-speaker clip visually follows the speaker with cuts on turn changes (unlock #2 — speaker_cut, cut at t=5.2 on the diarized turn, frame-verified; floor tuned to 0.2 via REFRAME_MIN_MAPPING_CONFIDENCE)
- [x] Timing budget measured and recorded (unlock #3 — 39s clip: 19.1s/13.5s/0.02s); sendcmd tmp cleanup verified (#4 — kill drill, file gone <2s, retry recovered)
- [x] DECISIONS flag-flip entry landed with the evidence (2026-08-05 evening); ACTIVE_SPEAKER_REFRAME_ENABLED=true live in prod (render-worker)

### Issue 423: Stage foundation — contracts + StagePlaceholder + useClipRender
- [x] **Status:** DONE 2026-08-04 (branch `lane/l26-stage`, merged in `lane/l26`) · **Track:** C · **Size:** S · **Depends:** —

**What.** Step 0: `types.ts` gains optional `suggested_*` on ReviewClip + `CropTrack` types;
`e2e/fixtures/mock-api.ts` gains the crop-track route (one tracked clip + `c1` 404) and suggested
fields (inert until consumed). Step 1: extract `StagePlaceholder.tsx` (unified render states) +
`hooks/useClipRender.ts` (state machine from `ClipPlayer.tsx:50-79`); ClipPlayer/ShortFormEditor
consume them **pixel-identically**.

**Acceptance**
- [x] All render/failure/expired states reachable from both pages; zero visual-baseline diff
- [x] `npm test` + tsc + eslint + structural gates green; no baseline regen needed

### Issue 424: ShortStage + Review flip + metadata compaction
- [x] **Status:** DONE 2026-08-04 (branch `lane/l26-stage`, merged in `lane/l26`; CI baseline regen pending) · **Track:** C · **Size:** L · **Depends:** 423

**What.** `components/stage/ShortStage.tsx` (stage owns the 9:16 frame: player/placeholder, meta row,
cleaned-preview tab swap, `overlay` + `below` slots; ONE `level="primary"` per page). Sizing
inversion in `toolLayout.ts`: `STAGE_CELL` (lg container-type:size) + `STAGE_MEDIA_W`
(`min(34rem,100cqw,(100cqh−6rem)·0.5625)`) replace the subtraction constants. Review grid
`[minmax(260px,24rem)_minmax(0,1fr)_minmax(300px,26rem)]`; `WhyThisClip.tsx` split into
`ClipCase.tsx` + `ClipMetadataPanel.tsx` (title + hook one truncating row each, `applied_* ??
suggested_*` precedence, Apply chip → existing PATCH, "More suggestions" Disclosure = Regenerate);
YourCall drops its AppliedTitleField embed, gains quiet "Next clip".

**Acceptance**
- [x] Review media box ≥1.8× today's area at 1440×900 (Playwright boundingBox assertion — measured 1.89×)
- [x] Exactly one primary card; title+hook ≤2 collapsed rows; #412 empty-canvas regression guard
- [x] Trim save/apply flows identical (existing tests); axe + disclaimer + no-virality green
- [x] Review baselines regenerated via CI dispatch — done 2026-08-13 for Issues 424/425/426 together: `update_snapshots=true` dispatch run 31672333175 at main `9de69f4`, baselines committed via PR #95 whose own visual job passed against them (the acceptance proof)

### Issue 425: Editor flip + toolbar merge + deletions
- [x] **Status:** DONE 2026-08-04 (branch `lane/l26-stage`, merged in `lane/l26`; CI baseline regen pending) · **Track:** C · **Size:** M · **Depends:** 424

**What.** ShortFormEditor composes ShortStage (cut/document logic untouched); cleaned preview becomes
the in-stage tab swap; `Editor.tsx` h1 + mode tablist merge into one toolbar strip;
CaptionStylePanel collapsed by default; delete `ClipPlayer.tsx`, `WhyThisClip.tsx`, old toolLayout
constants (tests migrate).

**Acceptance**
- [x] One transport player per page; undo/redo/apply-cuts/CAS flows untouched
      (`editor-persistence.spec.ts` unchanged); J/K/L + capture-bus keys unaffected
- [x] Stage card fits its grid row (tool-shell boundingBox guard)
- [x] Editor + long-form baselines regenerated via CI dispatch — same 2026-08-13 dispatch/PR #95 as Issue 424 (the run regenerates ALL smoke-spec baselines)

### Issue 426: Crop-track overlay preview
- [x] **Status:** DONE 2026-08-04 (branch `lane/l26-stage`, merged in `lane/l26`; CI baseline regen pending) · **Track:** C · **Size:** M · **Depends:** 421 (endpoint), 424

**What.** `hooks/useCropTrack.ts` (key `['crop-track', id, render_uri]`, 404→null, staleTime
Infinity) + `components/stage/CropTrackOverlay.tsx`: pointer-events-none ~112px mini-map (source
rect + accent crop window via translateX, cut ticks), label `AI framing` / `Framing: centered` on
fallback. Animated via `subscribeTime` + rAF writing `style.transform` on a ref — zero React renders
per frame. No track → render nothing (honest absence). Pure divs. Mock-api fixture unblocks UI work
before 421 merges. Follow-up (filed, not scheduled): source-framing view over
`GET /videos/{id}/stream` — the #396 host.

**Acceptance**
- [x] Lerp + cut-snap + clamp math unit-tested against fixture keyframes
- [x] 404 → nothing rendered, no error; fallback label honest; overlay never intercepts pointers
- [x] Zero setState in the animation path; e2e fixture shows map on tracked clip, nothing on `c1`
- [ ] Baselines regenerated via CI dispatch

---

# Batch A — Visual credibility

### Issue 384: Adopt an icon system — purge emoji and geometric glyphs
- [x] **Status:** DONE 2026-08-03 · **Batch:** A · **Size:** S

**What we're doing.** Adding a real icon library (`lucide-react`), routing it through a single
re-export module, and removing every emoji and Unicode geometric character currently doing an
icon's job.

**Why — the analysis.** This is the highest ratio of perceived-quality gain to effort in the entire
lane. Icons are the densest signal of software maturity a user reads, because a consistent
monochrome set at one stroke weight is something only a deliberate design process produces —
whereas emoji are what you reach for when there is no system. The specific failure here is
compounded: `👍 Keep` and `👎 Drop` are emoji *and* they sit on saturated pure-green and pure-red
full-bleed fills. That doubles the amateur signal, because it uses color to carry meaning that the
icon is already carrying, and it uses the loudest possible saturation to do it. The 2026 dark-UI
convention is the opposite: monochrome icons carry affordance, and color is spent sparingly on
state and accent so that it still means something when used.

**Evidence in this repo.**
- `frontend/package.json` — 8 runtime dependencies, **no icon library** (no `lucide-react`, no
  `@radix-ui/react-icons`, nothing). Verified by grep: zero imports of any icon package in
  `frontend/src`.
- `frontend/e2e/__screenshots__/desktop-review.png` — `👍 Keep` / `👎 Drop` on saturated fills,
  plus `✂ Save trim`, `⬇ Download`, `↻ Apply trim & re-render`.
- `frontend/src/pages/Editor.tsx:438,446` — `▮ Short-form clip` / `▭ Long-form source`, using
  geometric block characters as mode icons.
- `frontend/src/pages/Editor.tsx:609` — `×` as the remove-cut affordance.

**Industry standard checked.** Dark-toned interfaces with clear monochrome icon sets are the stated
convention for editing tools, and the professionalism judgment users make about a dark interface is
driven by exactly this kind of consistency
([Dark Mode UI Design in 2026](https://www.tech-rz.com/blog/dark-mode-ui-design-in-2026-user-experience-and-ai-powered-interfaces/),
[AI and Dark Mode UI Design](https://www.tech-rz.com/blog/artificial-intelligence-and-dark-mode-ui-design-user-interfaces-in-2026/)).
Dark-mode design-system guidance is explicit that hierarchy should come from luminance and surface,
not from weight and saturation
([Dark Mode Design Systems: Patterns, Tokens, and Hierarchy](https://muz.li/blog/dark-mode-design-systems-a-complete-guide-to-patterns-tokens-and-hierarchy/)).

**Acceptance**
- [x] `lucide-react` added; all icons imported through one `components/ui/icon.tsx` re-export so the
      set is swappable without touching call sites — enforced by ESLint `no-restricted-imports` AND
      a test, so it fails `npm test` as well as `npm run lint`
- [x] Zero emoji and zero geometric-block characters used as icons anywhere in `frontend/src` —
      `src/test/no-glyph-icons.test.ts`, a TypeScript-AST source scan (0 false positives across the
      ~2,600 box-drawing characters in comment banners; see DECISIONS for why not a regex)
- [x] Keep/Drop restyled: new soft `success` button variant mirroring `danger` — semantic border +
      soft surface, icon carries the affordance, no full-bleed saturation. `confirm` stays
      full-bleed for single-action modal confirmations
- [x] Icon sizes drawn from the spacing scale (`iconSizes.ts`, `size-*` utilities — never lucide's
      `size` prop, which emits inline px); decorative icons `aria-hidden`, gate-enforced
- [x] Bundle-size delta recorded: **+10,360 B raw / +2,768 B gzip** for 27 icons of 6,014 available
      (620,801 → 631,161 raw; 175,278 → 178,046 gzip). Installing without importing cost 0 bytes

---

### Issue 385: Build the missing UI primitives
- [x] **Status:** DONE 2026-08-03 · **Batch:** A · **Size:** M
- **Scope amended at build time** (see `docs/DECISIONS.md` 2026-08-03): six primitives, not seven.
  Slider / DropdownMenu / Popover have **zero call sites** today and are deferred to their first
  real consumer (#390 / #394 / #396 / #398); **RadioGroup** — not on the original list but the last
  native control left once the others land — takes their place.

**What we're doing.** Building the primitives the app is missing — Select, Switch, Slider, Tooltip,
Tabs, DropdownMenu, Popover — on Radix headless primitives, styled with the existing tokens, and
removing every native `<select>` and `<input type="checkbox">` outside `components/ui/`.

**Why — the analysis.** This is the direct cause of "blocky and not easy on the eyes." A native
`<select>` and a native checkbox render with **operating-system chrome that ignores your entire
design system**. You can see it in the screenshot: the "Captions on" checkbox is bright OS system
blue, sitting inside a carefully-built OKLCH hue-285 palette where nothing else is that color or
that saturation. Every one of these controls is a small hole punched through the design.

The deeper point is that `index.css` is genuinely good work — OKLCH surfaces, WCAG-verified text
contrast with a documented a11y fix at line 35-37, a real motion scale, role-split accent tokens
following Radix's solid-vs-text convention. That system is being bypassed by the controls that
matter most, because there is nothing to bypass it *to*: `components/ui/` is five files. Build the
missing thirteen-odd primitives once and every screen improves at the same time.

Radix specifically (rather than hand-rolling) because the accessibility work — focus management,
keyboard navigation, ARIA roles, typeahead in listboxes — is the expensive part, and it is exactly
what gets skipped under time pressure. Radix ships unstyled, so it composes with the tokens rather
than fighting them.

**Evidence in this repo.**
- `frontend/src/components/ui/` — **five files**: `badge.tsx`, `button.tsx`, `card.tsx`,
  `fit-badge.tsx`, `modal.tsx`. No select, switch, slider, tooltip, tabs, dropdown, or popover.
- **8 native `<select>`** across `components/review/CaptionStylePanel.tsx`,
  `components/profile/BrandKitSection.tsx`, `components/insights/PerformerPanel.tsx`,
  `components/dashboard/AnalyticsPanel.tsx`.
- **9 native `<input type="checkbox">`** across `CaptionStylePanel.tsx`, `pages/Login.tsx`,
  `BrandKitSection.tsx`, `components/profile/NotificationPreferencesSection.tsx`.
- `frontend/e2e/__screenshots__/desktop-editor-short.png` — the OS-blue checkbox and OS-chrome
  dropdowns against the dark palette.
- `frontend/src/components/review/CaptionStylePanel.tsx:8-9` — `selectCls`, a bare string of
  Tailwind classes doing the job a primitive should do, duplicated per call site.

**Industry standard checked.** Radix Primitives is the reference headless library: components ship
without styles, follow the WAI-ARIA authoring practices, and are tested against assistive tech
([Radix Primitives — Introduction](https://www.radix-ui.com/primitives/docs/overview/introduction),
[Accessibility](https://www.radix-ui.com/primitives/docs/overview/accessibility)). Radix's own
position is directly on point for this issue: the native web-platform implementations are
"inadequate — either non-existent, lacking in functionality, or cannot be customized sufficiently,"
which is precisely the failure visible in our screenshots. The Select primitive adheres to the
ListBox WAI-ARIA pattern ([Radix Select](https://www.radix-ui.com/primitives/docs/components/select)).

**Acceptance**
- [x] **Select, Switch, Checkbox, RadioGroup, Tabs, Tooltip** in `components/ui/`, token-driven.
      Slider / DropdownMenu / Popover deliberately deferred — zero consumers (KISS, per `CLAUDE.md`)
- [x] Built on Radix primitives — no hand-rolled focus traps or keyboard handlers. Tabs additionally
      fixes a live defect: the hand-rolled tablist had `role="tab"` but no `role="tabpanel"`, no
      `aria-controls` and no roving tabindex
- [x] Zero native `<select>` / `<input type="checkbox">` / `<input type="radio">` outside
      `components/ui/` — `src/test/no-native-form-controls.test.ts` (TypeScript-AST source scan)
- [x] `selectCls` removed at both call sites; option lists are now `SelectOption[]` data
- [x] Render + interaction test per primitive (22 tests), incl. the load-bearing empty-string
      round-trip (Radix Select **throws** on `value=""`; 5 of 8 call sites default to it) and a
      clickwrap **label-click** test. All 10 existing `Login.test.tsx` tests pass unmodified
- [x] axe pass at desktop **and** mobile in the e2e suite — **20/20** (10 routes × 2 projects) at the
      Batch A close-out, with `editor` newly added to the gate. `settings` was deliberately NOT added:
      a baseline spike found PRE-EXISTING serious contrast failures there from the 2026-06-23 "Soon"
      preview rows (`docs/OFF_COURSE_BUGS.md`), and adding it would have blocked the batch on
      unrelated work

---

### Issue 386: `VideoPlayer` primitive — replace all native `<video controls>`
- [x] **Status:** DONE 2026-08-03 · **Batch:** A · **Size:** M

**What we're doing.** One custom player component with a real transport, used everywhere. Removing
the `controls` attribute from all 11 call sites.

**Why — the analysis.** A video product whose player is the browser default is telling the user it
didn't build a player. In the screenshots you can see the Chrome control bar — including the
**three-dot kebab menu**, which exposes "Download" and "Picture-in-picture" browser affordances that
have nothing to do with our product and in one case duplicate a paid action.

Beyond appearance, this blocks Issue 390. Editing requires a playhead the app controls: frame
stepping, J/K/L shuttle, speed control, and precise seek. The native element gives us none of that,
and every surface currently reimplements a fragment of it — `pages/Editor.tsx:165` and
`components/editor/LongFormEditor.tsx:324` each hand-roll their own `onTimeUpdate` → `setCurrentTime`
sync. Building the primitive once removes that duplication (DRY, per `CLAUDE.md`) and gives Timeline
v2 something to attach to.

**Evidence in this repo.**
- **11 `<video>` elements across `frontend/src`, 8 of them with `controls`** (git-verified at
  `f298164`, the pre-Batch-A base; the original grep conflated the two — the 2026-08-04 assessment
  reconciled this against `PROJECT_STATE.md`'s "replaces all 8").
- `frontend/src/pages/Editor.tsx:476-487` — the short-form player, `controls`, at `w-[180px]`.
- `frontend/src/pages/Editor.tsx:650-654` — the cleaned-preview player, `controls`, no shared code.
- `frontend/src/components/editor/LongFormEditor.tsx:318-327` — the source player, `controls`.
- `frontend/e2e/__screenshots__/desktop-review.png` — the default control bar with the kebab menu.

**Industry standard checked.** The standard timeline/bin/viewer layout is deliberately preserved
across professional tools to minimize learning curve, and the viewer is part of that contract
([AI Video Tools in 2026](https://pixflow.net/blog/ai-video-tools-in-2026/)). J/K/L shuttle is the
cross-NLE convention for the viewer transport, with I/O for in/out points
([Video Editing 101: J, K, and L](https://www.premiumbeat.com/blog/video-editing-j-k-l-shortcuts/),
[DaVinci Resolve Keyboard Shortcuts 2026](https://pixflow.net/blog/davinci-resolve-keyboard-shortcuts/)).

**Acceptance**
- [x] One `VideoPlayer` primitive: transport, scrub bar, time display, speed readout, fullscreen.
      **Volume is deliberately omitted** — every call site is a short muted-or-unmuted preview and
      the OS/browser owns system volume; add it when a consumer needs per-clip level control
- [x] Keyboard: space, ←/→ (±5s), `,` / `.` (frame step), J/K/L (shuttle). J runs a rAF loop —
      browsers reject a negative `playbackRate`. Frame step states its assumed fps in the tooltip
      rather than implying accuracy we don't have (the render pipeline pins no output rate)
- [x] Exposes `getCurrentTime()` (pull), `subscribeTime()` (push) and imperative `seek()`; the
      per-page `onTimeUpdate` handlers are gone. Pushing the subscription *down* into Timeline is
      left to #390, which rewrites that component's props anyway
- [x] Zero `<video controls>` outside the primitive — `src/test/no-native-video-controls.test.ts`
- [x] Keyboard-accessible, `role="group"` with a **required** `label` so no player ships unnamed;
      scrub bar is a `role="slider"` with `aria-valuetext`. Editor route added to the axe gate and
      passing (10 routes)

---

### Issue 387: Poster-frame thumbnails across the library and clip surfaces
- [x] **Status:** DONE 2026-08-03 · **Batch:** A · **Size:** M
- **Named `poster`, not `thumbnail`** — `routers/thumbnails.py` already owns "thumbnail" for the
  YouTube thumbnail-*pattern* analyzer, and a second unrelated one makes every grep ambiguous.

**What we're doing.** Extracting a poster frame at ingest, storing it, serving it through the authed
path, backfilling existing videos, and rendering thumbnails in the library and clip lists.

**Why — the analysis.** The video library is currently a **text table**. This is the single clearest
"not a video product" signal in the app, and it is not a taste judgment — it is a functional one.
Creators identify their footage visually. A row reading `Desk Setup Tour 2026 · long · xyz98765432`
requires reading and recall; a poster frame is recognized instantly. Asset-management practice treats
thumbnails as part of the core managed structure alongside captions and transcripts, not as
decoration.

The cost here is unusually low because the capability already exists: `clip_engine/render.py:173`
`_extract_keyframe` already grabs frames via ffmpeg for the reframe path. This is plumbing an
existing function to a new consumer, not new capability.

Retention interacts with this and must be handled deliberately: source media is purged per
`docs/COMPLIANCE.md`, so the thumbnail lifecycle needs an explicit decision (a thumbnail is a
derived still, not source media) recorded in `docs/COMPLIANCE.md` rather than assumed.

**Evidence in this repo.**
- `frontend/e2e/__screenshots__/desktop-dashboard.png` — "Your videos" is a
  `VIDEO / STATUS / CLIPS / ACTIONS` table with **zero imagery**, and raw IDs (`dQw4w9WgXcQ`) as
  the secondary line.
- `clip_engine/render.py:173` — `_extract_keyframe` exists and is proven in the render path.
- `frontend/src/components/editor/LongFormEditor.tsx:456-480` — the Export rail lists clips as text
  rows with no visual reference to what the clip contains.

**Industry standard checked.** Video asset platforms treat automated thumbnail generation as baseline
and hold thumbnails, captions, and transcripts in one managed structure
([Cloudinary — Video Asset Management](https://cloudinary.com/guides/digital-asset-management/video-asset-management),
[Video Asset Management Software 2026](https://filestage.io/blog/video-asset-management-software/)).

**Acceptance**
- [x] Extracted at ingest (inside the existing `alocal_path` block, so no second R2 download) **and**
      at render from the actual deliverable; served by `GET /videos/{id}/poster` + `/clips/{id}/poster`.
      **Never fails the work that produced the media** — `ingest_video` is a `RefundOnFailureTask`,
      so a propagating error would refund minutes for a successful transcription
- [x] Hourly backfill (newest-first, batch 25) with a Redis failure marker — required, since the real
      cost is R2 egress and an undecodable file would otherwise be re-downloaded hourly forever.
      Purged sources are skipped **structurally**: the purge nulls `source_uri`, so they never match
- [x] `PosterThumb` in the library rows, merged into the existing Video cell rather than a fifth
      column (keeps #398's grid-card layout reusable). Placeholder says **why** — processing vs
      expired vs none. Hover-scrub correctly out of scope
- [x] Retention recorded in `docs/COMPLIANCE.md` with the argument for why a poster outlives the 72h
      source purge **while the extracted WAV does not**; the audio row was sharpened so the two read
      coherently
- [x] Per-creator isolation via `get_owned`, same as `/clips/{id}/download`. **Caveat stated rather
      than implied:** the mocked-session tests emulate the predicate; the RLS-enforced proof is the
      integration lane, which needs Docker and must run in CI/staging before close

---

### Issue 388: De-debug the creator-facing surfaces
- [x] **Status:** DONE 2026-08-03 · **Batch:** A · **Size:** S

**What we're doing.** Removing raw internals from primary UI — bracketed principle tags, the numeric
score, the setup→peak→end readout, raw YouTube IDs — and fixing a live layout bug in the Review
action row.

**Why — the analysis.** The Review page currently shows the creator what looks like debug output.
`[principle] Open Loop` in bracketed monospace, `Score (fit estimate, not a guarantee) 0.86`, and
`Setup → peak → end 120.0s → 150.0s → 160.0s` are all internal representations rendered directly.
Monospace-plus-brackets reads as a log line, and a log line in a product surface reads as unfinished.

Important nuance: **the honesty constraint is not the problem here and must not be weakened.** The
`CLAUDE.md` rule that we never promise virality and always frame scores as estimates is correct and
stays. What's wrong is the *presentation*: we already have `--color-fit-strong` / `-moderate` /
`-exploratory` tokens (`index.css:66-72`) built exactly for this, and a `FitBadge` component using
them, yet the raw float is shown alongside. Lead with the tier, keep the number available behind a
disclosure, keep every honesty caveat intact.

The layout bug is separate and simply broken: two inline `<button>`s rendered as siblings with
`mt-2` / `mt-1` — margin-top has no effect on inline elements — and no separator between them, so
they collide into `rewrite hookSuggest caption`.

**Evidence in this repo.**
- `frontend/src/components/review/WhyThisClip.tsx:98` and `:181` — the two inline buttons; visible
  collided in `frontend/e2e/__screenshots__/desktop-review.png`.
- `frontend/e2e/__screenshots__/desktop-review.png` — `[principle] Open Loop`, the `0.86` score row,
  the `120.0s → 150.0s → 160.0s` readout.
- `frontend/e2e/__screenshots__/desktop-dashboard.png` — raw `dQw4w9WgXcQ` / `xyz98765432` IDs.
- `frontend/src/index.css:66-72` — the fit-tier tokens that exist and are underused.

**Industry standard checked.** Field tools surface a confidence signal, not the model's raw output;
the differentiator we hold is honesty about what the signal means, not exposure of the float
([11 Best AI Clipping Tools in 2026](https://www.ssemble.com/blog/best-ai-clipping-tools-2026),
[Opus Clip alternatives, tested](https://www.choppity.com/blog/best-opus-clip-alternatives/)).

**Acceptance**
- [x] `FitBadge` leads in the header; the numeric score moves behind a new `ui/disclosure.tsx`
      (native `<details>`, chrome-less — **not** `CollapsibleTool`, whose own card would reproduce
      the nested-box repetition #400 exists to remove). **The wording is byte-identical**, and
      because `<details>` keeps children in the DOM the existing honesty guard passes unmodified
- [x] Principle rendered as a `Badge` with a new `casing="sentence"` variant — the badge base is
      uppercase + wide tracking, right for "SOON" and wrong for a sentence-length principle
- [x] setup→peak→end readout in the same disclosure
- [x] Raw YouTube IDs replaced by `Long-form · 12:34 · 3d ago` (`lib/videoMeta.ts`, shared by both
      call sites). The ID moves to the row's `title` attribute and stays on the detail page
- [x] Action row rebuilt as `flex flex-wrap gap-2`; triggers are real Buttons and open panels take
      `basis-full`, which also fixes the layout jump when one opens. **Two tests**: structure in
      vitest, and geometry in `e2e/review.spec.ts` — verified to FAIL on the pre-fix markup
      (measured 0px horizontal gap) rather than merely passing on the fix
- [x] No-virality structural tests green and untouched (`Review.test.tsx`, `fit-badge.test.tsx`)

---

### Issue 400: Visual hierarchy pass — fix "blocky and hard on the eyes"
- [x] **Status:** DONE 2026-08-03 · **Batch:** A · **Size:** M
- **Split into 400a + 400b.** **400a (DONE 2026-08-03)** is the foundation: the
  `--shadow-inset` → `--inset-shadow-highlight` composition fix at 43 sites, `Card`'s `level` prop,
  the `Card`/`Modal` elevation-ladder corrections, seven dead colour tokens, the
  `design-tokens.contract` gate, and the `docs/UI.md` reconciliation + new Elevation/Hierarchy
  sections. **400b** is the composition pass over Review and the Editor, and runs after #386/#388.

**What we're doing.** An elevation and hierarchy pass across the app surfaces: differentiating card
weight, establishing a real type rhythm, and removing the uniform-box repetition that makes every
screen read as a stack of identical rectangles.

**Why — the analysis.** This is the issue that addresses the original complaint directly, and it was
missing from the first draft of this lane. The other Batch A issues fix *components*; this one fixes
*composition*.

The concrete failure: the app has exactly one visual container idiom — a `border border-default
rounded-md bg-surface` box with an uppercase micro-label — and it is repeated at identical weight for
everything. On the Review screen that is four such boxes stacked down the right rail; on the Editor,
three. Because they are all the same, nothing recedes and nothing dominates, so the eye gets no
entry point and has to scan linearly. That is precisely the sensation of "blocky" and "not easy on
the eyes." It is a hierarchy problem, not a color problem.

The tokens to fix it already exist and are barely used. `index.css:25-28` defines a **four-level
surface ladder** (`bg` → `surface` → `elevated` → `raised`) and the file's own comment explains that
dark-mode elevation must come from surface contrast and borders because black drop-shadows are
invisible on near-black — a conclusion that matches current dark-mode practice exactly. But the app
uses `bg-surface` almost exclusively; `elevated` and `raised` are nearly dead tokens. We built the
ladder and then stood on the bottom rung.

Type is the second half. `index.css:91-115` defines a full semantic scale (`text-h1` … `text-label`)
with line-height and weight bundled, but the screens lean on `text-xs` / `text-[10px]` and low-
contrast `text-subtle`, which is why dense areas feel simultaneously cramped and washed out.

**Evidence in this repo.**
- `frontend/src/index.css:25-28` — the four-surface ladder, with `--color-elevated` and
  `--color-raised` defined; grep shows `bg-elevated` / `bg-raised` used in a handful of places while
  `bg-surface` dominates.
- `frontend/e2e/__screenshots__/desktop-review.png` — four identically-weighted bordered cards
  stacked in the right rail ("Why this clip", "Your call", "Publish", "Open in the editor").
- `frontend/e2e/__screenshots__/desktop-editor-short.png` — "Render options" rail plus timeline and
  transcript blocks, all the same treatment; body text at 10–12px.
- `frontend/src/index.css:91-115` — the semantic type scale that the pages mostly bypass.
- `frontend/src/pages/Editor.tsx:532` and `:580` — `text-[10px]` and `text-xs text-subtle` for
  content the user actually needs to read.

**Industry standard checked.** Current dark-mode design-system guidance calls for a **minimum of four
surface elevation levels** (base, primary elevated, secondary elevated, overlay), replacing drop
shadows with borders and glows, and building hierarchy from **luminance rather than weight** — which
is the ladder we already defined and are not using
([Dark Mode Design Systems: Patterns, Tokens, and Hierarchy](https://muz.li/blog/dark-mode-design-systems-a-complete-guide-to-patterns-tokens-and-hierarchy/)).
Material 3's tonal-surface approach (lighter tonal shades rather than shadows to signal elevation) is
the same conclusion from a different system
([Telerik Design System — Elevation](https://www.telerik.com/design-system/docs/foundation/elevation/)).
Carefully-executed dark interfaces are explicitly identified as driving user judgments of
professionalism and reliability
([Dark Mode UI Design in 2026](https://www.tech-rz.com/blog/dark-mode-ui-design-in-2026-user-experience-and-ai-powered-interfaces/)).

**Acceptance**
- [x] All four levels in use: L0 page **and** recessed wells (transcript scroll box, cut queue),
      L1 secondary cards, L2 the one primary panel + floating menus, L3 modals. Rule documented in
      `docs/UI.md` and encoded as `Card level="panel"|"primary"`
- [x] Primary panel dominant on both tool screens (Review's player, the Editor's viewer); the rail
      recedes — Publish now **collapsed by default**, which is the strongest lever, and the
      gradient "Open in the editor" card demoted so it stops competing with the player
- [x] Body copy on the semantic scale; **all 12 arbitrary sizes gone** (`text-[10px]`/`[11px]`/
      `[15px]` → `text-label`/`text-small`/`text-body`), timecodes onto `text-mono`. The remaining
      ~400 legacy `text-xs`/`text-sm` on untouched surfaces are **explicitly deferred** and stated
      in `UI.md`; the legacy scale is NOT deleted, since it still resolves those utilities
- [x] `docs/UI.md` has the Elevation + Hierarchy sections (400a)
- [x] axe gate green on all 10 routes including the editor, so the AA fix did not regress
- [x] Artifact screenshots refreshed and **reviewed by eye** — which caught two defects tests
      could not: the primary card framing a large empty region beside a 9:16 player (now `w-fit`),
      and Select values wrapping out of their fixed-height trigger (now truncated)

---

# Batch B — Make it an application

### Issue 389: App-shell layout for Editor and Review
- [x] **Status:** DONE 2026-08-03 · **MERGED + DEPLOYED** 2026-08-04 (PR #70) · **Batch:** B · **Size:** M
- **Scope extended at build time** (see `docs/DECISIONS.md` 2026-08-03): also extracted
  `components/editor/ShortFormEditor.tsx` out of the 700-line `Editor.tsx` (all four Batch-B issues
  rewrite that file, so the split makes them conflict in different files) and made the
  `ResizeObserver` test stub fireable, without which #390's zoom-math acceptance criterion is
  untestable. The keyboard shortcut bus went to #390 and the cut-state de-duplication to #391.

**What we're doing.** Converting the tool routes from centered document pages to a full-height
application shell: `100vh`, panel regions that scroll independently, no marketing footer, and the
compliance disclaimer relocated to a persistent affordance rather than a banner in the work area.

**Why — the analysis.** Every screen is `max-w-6xl mx-auto` with a marketing footer
(`Terms · Privacy · Accessibility · © AutoClip 2026`) and the honesty disclaimer band pinned above
the content. That is a correct layout for a landing page and the wrong one for a tool. The
consequences are concrete: the editor's working area is squeezed into a centered column, the player
is 180px wide with a large dead region beside it, and vertical space that should belong to the
timeline is spent on page chrome. When the whole page scrolls, the timeline can leave the viewport
while you are editing against it.

**On the honesty constraint — read before implementing.** `CLAUDE.md` requires the
"predicts fit, does not promise virality" statement in every interface. This issue does **not**
weaken that. The text stays verbatim and stays present on every tool surface; it moves from a
full-width band consuming vertical space in the work area to a persistent, always-visible affordance
in the shell chrome. If that trade looks wrong during implementation, keep the band and reclaim
space elsewhere — the constraint outranks the layout.

**Evidence in this repo.**
- `frontend/src/pages/Editor.tsx:418` — `<main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">`.
- `frontend/src/pages/Editor.tsx:413-416` and `:351-357` — `DisclaimerBand` rendered inline above
  content on every branch, including loading and error states.
- `frontend/src/pages/Editor.tsx:486` — the player at `w-[180px]`; the dead region beside it is
  visible in `desktop-editor-short.png`.
- `frontend/e2e/__screenshots__/desktop-editor-short.png`, `desktop-review.png` — marketing footer
  below the editing surface.

**Industry standard checked.** The timeline / bin / viewer arrangement is deliberately preserved
across professional tools to minimize learning curve — it presumes a persistent full-viewport
workspace, not a scrolling document
([AI Video Tools in 2026](https://pixflow.net/blog/ai-video-tools-in-2026/)). Descript pairs a
simplified timeline with transcript editing inside a single sustained workspace
([Descript in 2026](https://www.fahimai.com/descript),
[Descript Complete Guide 2026](https://aitoolsdevpro.com/ai-tools/descript-guide/)).

**Acceptance**
- [x] Editor and Review render at `100dvh` with no page scroll; panels scroll independently —
      `ToolChrome` + `ToolShell`, engaged at `lg`. Measured: chrome height == viewport height,
      `scrollBy(0,500)` leaves `scrollY === 0`, ≥2 `[data-tool-scroll]` regions per route
      (`e2e/tool-shell.spec.ts`). `dvh` over `svh`/`vh` justified in DECISIONS.
- [x] Marketing footer removed from tool routes; legal links reachable from shell chrome —
      `LegalLinks` extracted from `Footer` and rendered in the docked status bar; tests assert
      exactly ONE legal-link set (two would mean the footer came back, zero a lost Google-
      verification requirement)
- [x] Disclaimer text **unchanged**, still present and visible on every tool route — one
      `HONESTY_STATEMENT` constant replacing ten byte-identical inline call sites; asserted as an
      exact string (not a regex) so softened wording fails loudly, and `toBeInViewport()` on every
      route in both projects
- [x] Existing structural test asserting the disclaimer on tool routes stays green — `Editor.test.tsx`,
      `Review.test.tsx`, `StyleReview.test.tsx` and `VideoPickerLanding.test.tsx` all pass
      **unedited**. This is what forced the status bar into `ToolShell` rather than the route layout
- [x] Player sized proportionally to its importance; no dead regions at 1440px — Editor player
      **180px → 257px**, Review off a frozen `max-w-[320px]`, both height-derived in
      `lib/toolLayout.ts`; card-width-minus-player-width < 40px asserted in e2e
- [x] Responsive down to tablet; mobile keeps a stacked layout (no horizontal page scroll) —
      shell disengages below `lg` (chrome and `main` both `overflow: visible`), panels keep
      viewport-relative caps, no horizontal overflow; asserted on the Pixel 5 project

**Also fixed** (defects the shell exposed, not in the original brief)
- [x] The long-form source player was `aspect-video w-full` → 605px tall at the column width,
      pushing the master timeline to the viewport edge and the clip lists off-screen (top y=831 → 536)
- [x] The Editor transcript was a latent axe `scrollable-region-focusable` failure (SERIOUS) that
      only passed because the fixture is two words long — logged in `docs/OFF_COURSE_BUGS.md`
- [x] `fullPage: true` audit screenshots silently degraded to viewport captures under the shell;
      a second `-expanded` artifact recovers the full content (editor-long: 900px → 1060px)

---

### Issue 390: Timeline v2
- [x] **Status:** DONE 2026-08-03 · **MERGED + DEPLOYED** 2026-08-04 (PR #70) · **Batch:** B · **Size:** L
- **Scope extended at build time** (user decision, see `docs/DECISIONS.md`): the engine is shared by
  BOTH timelines. The brief says "rebuild `Timeline.tsx`", but its own motivating evidence is the
  22-minute long-form source, which was a separate hand-rolled bar with the same mouse-only defect.
  Rebuilding only the short-form clip timeline would have left the surface the issue points at
  unusable.

**What we're doing.** Rebuilding `components/editor/Timeline.tsx`: pointer events, zoom and scroll,
draggable cut edges with snapping, a real ruler, and standard editing keyboard shortcuts.

**Why — the analysis.** The current timeline is a fixed 80px strip that maps the entire clip to the
container width, with no zoom. On a 40-second clip that is tolerable; on the 22-minute source shown
in `desktop-editor-long.png`, one pixel is roughly a second, which makes precise work impossible.
That is the mechanical reason the editor "doesn't seem like you can do a thing."

Four specific defects:

1. **Mouse events only.** `onMouseDown` / `onMouseMove` / `onMouseUp` — so touch and pen input do
   nothing. Pointer events are the unifying API and cost nothing to adopt.
2. **No zoom or scroll.** Precision is capped by container width.
3. **Cuts are immutable once made.** `addTimeCut` pushes a region; the only subsequent operation is
   delete-the-whole-cut (`removeCut`). Every professional timeline lets you drag an edge to trim.
4. **Snapping is invisible.** `timeRangeToIndices` already snaps a dragged range to enclosing word
   boundaries — genuinely good behavior — but nothing in the UI shows it, so the user cannot tell
   the snap happened or predict where it will land.

Note this issue must not disturb clip geometry: the setup-start invariant is enforced by the eval
harness and is load-bearing product behavior.

**Evidence in this repo.**
- `frontend/src/components/editor/Timeline.tsx:108-143` — mouse-only handlers.
- `frontend/src/components/editor/Timeline.tsx:6` — `WAVE_HEIGHT = 80`, fixed; no zoom state.
- `frontend/src/components/editor/Timeline.tsx:226-230` — the "ruler" is three static labels
  (`0:00`, midpoint, end).
- `frontend/src/pages/Editor.tsx:245-259` — `addTimeCut` / `removeCut`; no edge-adjust path.
- `frontend/src/pages/Editor.tsx:63-80` — `timeRangeToIndices`, the invisible word snapping.
- `frontend/e2e/__screenshots__/desktop-editor-long.png` — 22-minute source on a fixed-width bar.

**Industry standard checked.** J/K/L shuttle with I/O in/out points is the cross-NLE convention
([Video Editing 101: J, K, and L](https://www.premiumbeat.com/blog/video-editing-j-k-l-shortcuts/),
[Final Cut Pro shortcuts](https://blog.frame.io/2018/09/17/fcpx-final-cut-pro-shortcuts/),
[DaVinci Resolve Shortcuts 2026](https://pixflow.net/blog/davinci-resolve-keyboard-shortcuts/)).
Timeline zoom including zoom-to-fit, and waveform zoom levels, are standard
([EditMentor — Timeline](https://help.editmentor.com/en/articles/4592281-timeline)). Snapping is
expected to be visible and toggleable, with clip edges behaving as magnets to other edges and the
playhead ([Kdenlive — Editing](https://docs.kdenlive.org/en/cutting_and_assembling/editing.html),
[Kdenlive Timeline/Editing manual](https://userbase.kde.org/Kdenlive/Manual/Timeline/Editing/en)).
The component's own docstring (`Timeline.tsx:38-49`) already cites Descript/Opus/Riverside as the
reference — this issue closes the distance to that reference.

**Acceptance**
- [x] Pointer events throughout; works with touch and trackpad — both timelines on `TimelineRail`
      with `setPointerCapture` and `touch-action: none`. The drag state is a ref and never consults
      `hasPointerCapture`, which jsdom stubs to `false` (that is why the player's scrub drag has
      never been testable). Removes the old `onMouseLeave` commit hack outright.
- [x] Zoom (wheel / pinch / keyboard) plus zoom-to-fit, horizontal scroll, playhead stays in view —
      `lib/timelineZoom.ts` + `useTimelineViewport`. Ctrl/Cmd+wheel zooms and **pinch arrives as the
      same event** (MDN); the listener is `{ passive: false }` because React's `onWheel` is passive
      and `preventDefault` there silently no-ops (react#14856), which would zoom the whole page.
      Zoom is **anchored on the pointer** (Audacity's model) and fit is proven a fixed point.
- [x] Existing cut regions expose draggable edges — 6px grab zone, and merging is deferred to
      gesture completion so a neighbour cannot be absorbed (and the dragged id retired) mid-drag
- [x] Snap-to-word is **visible** and toggleable — a line at the snapped instant, a `data-snapped`
      ring on the thumb, the word named in the caption and in `aria-valuetext`, and a `Switch`
      persisted at `editor:snap`. **Threshold is in PIXELS**, so it feels identical at every zoom
      (~10s of tolerance at fit on a 22-minute source, ~4ms at 64×)
- [x] Real time ruler with adaptive tick density — intervals drawn from a human-round table
      (1s/5s/30s/2m…) constrained by minimum label spacing, never `duration / n`. DOM, not canvas
- [x] Keyboard: I/O set in/out, space, ←/→, Delete removes the selected cut — plus `=`/`-`/`0` zoom
      and `S` snap, all on one shortcut bus. **A bare arrow steps ONE PIXEL at the current zoom**;
      Shift keeps the old 5s jump
- [x] Clip-quality eval harness green — setup-start geometry unchanged. Verified structurally:
      `git diff 73c3223..HEAD -- clip_engine worker routers models.py alembic ingestion tests` is
      **empty**, so 390 is frontend-only by construction. `tests/test_clip_engine.py` 53 passed
- [x] Component test covers zoom math and edge-drag at multiple zoom levels — 24 zoom-maths cases,
      plus a `Timeline` test asserting the same 67px drag trims **4× fewer seconds at 4× zoom**

**Also fixed** (defects found while building, not in the original brief)
- [x] The container's `role="slider"` was **suppressing the waveform's `role="img"` and every
      per-cut label in production** — MDN: a slider forces all descendants to `presentation`. Now a
      `group` with the playhead and each cut edge as real thumbs (W3C APG multi-thumb)
- [x] The scrub bar's arrow keys **had never worked**: an `onKeyDownCapture` calling
      `stopPropagation` in the capture phase also prevented the element's own bubble handler
- [x] `mergeAdjacent` mutated caller-owned objects, so single-level undo was already wrong for any
      merged cut; cuts were index-addressed while the merge re-sorts, so an edge drag detached
- [x] `clientXToTime` derived zoom from stale state width, mapping every pointer x to the end of
      the clip before the first ResizeObserver

---

### Issue 391: Real edit persistence — undo/redo stack + server-side edit document
- [x] **Status:** **DONE 2026-08-04** — shipped across PR A (#71, `0b59a75`, migration `0052`) and
      PR B (#73, `7b8f281`, the render path), both merged and deployed · **Batch:** B · **Size:** L · **Agent:** `python-senior-engineer`

> **Split into two PRs at plan time.** `POST /clips/{id}/cuts` is paid, flag-gated and
> budget-checked, and LEFT_OFF ranked it the SEV1 risk of this issue. **PR A is purely additive:**
> nothing reads the document at render time, and the client still POSTs `segments` from the same
> in-memory state, so behaviour on the paid path is byte-identical. **PR B** flips the render path to
> read the document via `base_revision`, makes `/clean/confirm` clear it, and deletes the legacy
> branch. See `docs/PROJECT_STATE.md` 2026-08-04.

**What we're doing.** Replacing single-level undo with a command stack, and moving edit state out of
`localStorage` into a server-side, per-creator-isolated edit document with autosave.

**Why — the analysis.** Two separate defects that share a root cause: edit state was treated as
throwaway UI state rather than as the user's work.

**Undo is one level.** `const [undo, setUndo] = useState<EditorCut[] | null>(null)` — each mutation
overwrites the single snapshot, and undo clears it. Two cuts then two undos is impossible. For a
tool whose entire interaction model is "make destructive edits," one level of undo is the wrong
order of magnitude.

**Edits live in the browser.** Cuts persist to `localStorage` under `clip:${clipId}:cuts`. Clearing
site data, switching machines, or using a second browser destroys the work with no warning, and the
`catch { /* quota — recoverable */ }` at `Editor.tsx:237` means a quota failure silently discards a
save. There is no project, version, or recovery concept. The field has had cloud-persisted projects
with multiplayer editing and commenting for years; we do not need multiplayer for a ≤100-user beta,
but we do need the creator's work to survive a browser.

This is also the prerequisite for #393: once the edit document is authoritative server-side,
client-side preview can be the fast path while the server retains the truth used at export.

**Evidence in this repo.** (Line refs corrected 2026-08-04 — this code moved to
`components/editor/ShortFormEditor.tsx` in #389; the brief still cited `pages/Editor.tsx`.)
- `ShortFormEditor.tsx:148` — `const [undo, setUndo] = useState<EditorCut[] | null>(null)`.
- `ShortFormEditor.tsx:183,188,249,270,547` — all five mutation sites overwrite the single snapshot.
- `ShortFormEditor.tsx:36` — `storageKey = (clipId) => \`clip:${clipId}:cuts\``.
- `ShortFormEditor.tsx:170-176` — persistence to `localStorage`, quota failure swallowed.
- `ShortFormEditor.tsx:57-66` — `loadCuts` reads from `localStorage` on mount.
- `ShortFormEditor.tsx:158-165` — the clip-change reload effect, carrying a
  `react-hooks/set-state-in-effect` suppression whose comment names `key` as the real fix.

**Industry standard checked.** Descript ships Google-Docs-style collaboration — multiplayer editing,
comments with mentions, notifications — which presumes a server-authoritative document model
([Descript in 2026](https://www.fahimai.com/descript),
[Descript Review 2026](https://filmora.wondershare.com/video-editor-review/descript-ai.html)).

**Acceptance**
- [x] Command-stack undo/redo, unbounded within a session; Cmd/Ctrl+Z and Shift+Cmd/Ctrl+Z
      (+ Ctrl+Y). Bound on #390's existing bus — a second document listener is how one keypress
      undoes twice. `lib/editCommands.ts`, 9 tests.
- [x] Edit document persisted server-side per clip; RLS-enforced per-creator isolation
      (`clip_edit_documents`, migration `0052`, hardened `NULLIF` GUC form + `GRANT`)
- [x] Debounced autosave with an explicit saved/saving indicator; save failures surfaced, not
      swallowed. 800ms trailing / **2s max-wait** — the ceiling is not optional, a pure trailing
      debounce fires zero times during continuous dragging. `SaveStatus` is persistent and
      non-toast with a Retry, because a toast vanishes on a timer.
- [x] Edits survive reload and follow the creator across devices
- [x] One-time migration: existing `localStorage` cuts imported on first load, then cleared —
      **only at `revision === 0`** (the anti-resurrection rule), and the key is removed only on a
      200 or a 409, which makes a double import structurally impossible.
- [x] `localStorage` demoted to an offline cache, never the source of truth — pinned by a new
      structural gate, `src/test/no-local-cut-storage.test.ts` (mutation-checked).
- [x] Alembic migration written; **RLS integration test extended but NOT run locally** — it needs
      Docker, which this box lacks. CI-verified only; say so plainly rather than claiming it passed.
- [x] **(PR B)** Render path reads the document via `base_revision`; legacy `segments` branch
      **deleted, not deprecated** — leaving it would keep a second, unvalidated way to drive a paid
      render. `CutSegmentIn` went with it, and a test asserts the old shape is now REJECTED.
- [x] **(PR B)** `POST /clean/confirm` clears the document server-side **in the same transaction as
      the swap** and reports the new revision so the client can adopt it; `/clean/discard` does not
      — the creator rejected that render, and their cuts still describe an unapplied edit.

**Two races on the paid path were found and fixed while wiring PR B**, neither of which was in the
plan: `flush()` was fire-and-forget, so export could post `base_revision` while the matching PUT was
still in flight and have the server render the *previous* document; and the revision was going to be
read off `query.data`, which never advances under `staleTime: Infinity`, so every export after the
first save would have 409ed against a base the client itself had moved past. Both are mutation-checked.

---

### Issue 392: Replace the fabricated long-form waveform
- [x] **Status:** DONE 2026-08-03 · **MERGED + DEPLOYED** 2026-08-04 (PR #70) · **Batch:** B · **Size:** S (backend turned out M)
- **Scope extended at build time** (approved; see `docs/DECISIONS.md` 2026-08-03): the SHORT-form
  timeline moved onto the same artifact too, deleting the client-side WebAudio decode that fetched
  the whole rendered mp4 and built an ~8 MB `Float32Array` per clip switch, plus the dead
  `waveformImageUrl` prop. Leaving it would have meant #390 building its renderer against PCM and
  then rewriting it.

**What we're doing.** Serving real audio peak data for the long-form source timeline, or rendering an
honest neutral track when peaks are unavailable. Removing the synthetic amplitude generator.

**Why — the analysis.** `LongFormEditor.tsx` draws 48 bars whose heights come from
`20 + ((i * 37) % 60)%` — a deterministic arithmetic pattern with no relationship to the audio. It is
labeled **"Source timeline"** and sits above a 22-minute video, so a creator will read those peaks as
loud moments and navigate by them. They are decoration.

This is the one item in the lane I'd classify as more than unfinished. Everything else is "we haven't
built it yet"; this actively asserts something false about the user's own content, and it does so on
the surface whose entire job is helping them find moments. Under the project's honesty constraint
this should not survive to beta.

The short-form editor is not affected — it does a real WebAudio decode
(`Editor.tsx:180-203`) and falls back to a low-opacity placeholder that reads as absent rather than
as data. That fallback is the correct pattern to copy.

**Evidence in this repo.**
- `frontend/src/components/editor/LongFormEditor.tsx:131-138` — the synthetic bars.
- `frontend/src/components/editor/LongFormEditor.tsx:85` — the "Source timeline" label above them.
- `frontend/src/pages/Editor.tsx:180-203` — the real WebAudio decode, for contrast.
- `frontend/src/components/editor/Timeline.tsx:254-267` — `PlaceholderWave`, the honest fallback
  pattern (20% opacity, explicitly a placeholder).
- `frontend/e2e/__screenshots__/desktop-editor-long.png` — the fabricated waveform as shipped.

**Industry standard checked.** Waveform display is a real data surface in every reference tool, with
zoom levels applying to actual audio tracks
([EditMentor — Timeline](https://help.editmentor.com/en/articles/4592281-timeline)). The component's
own sibling docstring (`Timeline.tsx:38-49`) names Descript/Opus/Riverside waveform behavior as the
standard being targeted.

**Acceptance**
- [x] Real peak data: precomputed peaks generated server-side and served for the source —
      `ingestion/peaks.py` emits the **BBC `audiowaveform` JSON format** (the interchange standard)
      from the 16 kHz WAV ingest already extracts, via ffmpeg + numpy with **no new dependency**;
      migration 0051 `videos.peaks_uri`, R2 `peaks/{creator_id}/…`, served by
      `GET /videos/{id}/peaks` (authed byte proxy, `private, immutable`)
- [x] When peaks are unavailable, render a flat neutral track — **never synthetic amplitude**.
      Enforced at three layers: `peakEnvelope` returns `null` rather than a zero array (so "no data"
      cannot be confused with "measured silence"), `Waveform` draws a flat line, and the timeline
      says *"Waveform unavailable for this source"* in words
- [x] Peak generation is incremental/cached, not recomputed per page load — computed **once at
      ingest**, hourly `backfill_video_peaks` for older rows, `staleTime: Infinity` client-side, and
      `has_peaks` gates the request so a video that will never have one costs zero fetches
- [x] Test asserts no synthetic amplitude generator remains — `src/test/no-synthetic-waveform.test.ts`,
      a source-scanning gate (bar height may not derive from a loop index in `components/editor/`;
      `Waveform.tsx` must be the only painter). **Verified it fires** by reintroducing the old
      generator

**Known limitation, accepted and documented** (`docs/COMPLIANCE.md`)
- Peaks derive from `audio_uri`, which is purged at 72h, so a video whose audio is already gone can
  never get peaks — `has_peaks` stays false permanently and the flat track is shown. Retaining audio
  longer to enable backfill would weaken a retention promise to improve a navigation aid.

---

# Batch C — Close the capability gap

### Issue 393: Client-side cut preview — make editing feel instant
- [ ] **Status:** open · **Batch:** C · **Size:** M · **Agent:** `general-purpose`

**What we're doing.** Playing the edit locally by seeking across keep-ranges, so cuts are audible and
visible immediately. Reserving the server render for final export and publish.

**Why — the analysis.** This is the deepest problem in the lane, and it is a *latency* problem rather
than a missing-feature problem. Right now every edit is a job submission: apply cuts → POST →
`setStatus('Editing your clip — come back in ~20s.')` → poll → preview. A tool where the feedback
loop is 20–30 seconds cannot be used for iterative work, because iteration is the whole activity.
You cannot try a cut, dislike it, and adjust — you commit to it and wait.

This is why the surface "doesn't feel like an editor" even where it is functionally correct. Editing
feels like editing when the result is immediate; the field previews locally and renders only on
export. The good news is that the hard part is already done — `validate_user_cuts` in
`clip_engine/edits.py` already inverts cuts into `keep_ranges` for ffmpeg, which is exactly the data
structure a client-side preview needs. Playing keep-ranges by seeking across gaps in the existing
`<video>` element requires no new media infrastructure.

Server-side validation stays authoritative at export — the client preview is a view, not a source of
truth.

**Evidence in this repo.**
- `frontend/src/pages/Editor.tsx:296` — `setStatus('Editing your clip — come back in ~20s.')`.
- `frontend/src/pages/Editor.tsx:284-301` — `apply()`: every edit is a POST plus a render job.
- `frontend/src/hooks/useCleanedUriPoll.ts` — the polling that exists solely to wait for renders.
- `frontend/src/components/review/CaptionStylePanel.tsx:101` — the same pattern for styling:
  `'Render queued — come back in ~30s.'`
- `clip_engine/edits.py:168` — `_invert_cuts`, already producing keep-ranges.

**Industry standard checked.** Agentic and AI-assisted editing tools in 2026 are built around
immediate feedback with render deferred to export, and text-based editing specifically depends on
edits reflecting instantly in playback
([What Is AI Video Editing in 2026 — Overlap](https://overlap.ai/blogs/how-do-agentic-tools-work),
[Descript Complete Guide 2026](https://aitoolsdevpro.com/ai-tools/descript-guide/),
[AI Video Editing — ChatCut](https://chatcut.io/blog/ai-video-editing)).

**Acceptance**
- [ ] Playback skips cut ranges client-side with no server call
- [ ] Cuts reflected in preview immediately on edit
- [ ] Preview duration and the removed-percentage warning derive from the same keep-ranges
- [ ] Server render invoked only for final export / publish
- [ ] `validate_user_cuts` invariants still enforced server-side at export (client cannot bypass)
- [ ] Preview and rendered output verified to agree on a fixture clip (regression test)

---

### Issue 394: WYSIWYG caption preview (merges the parked #363)
- [ ] **Status:** open · **Batch:** C · **Size:** L · **Agent:** `general-purpose`

**What we're doing.** Rendering captions live over the player with immediate style feedback,
drag-to-position, and — folding in the parked Issue 363 — editing the caption **text** that gets
burned into the render.

**Why — the analysis.** Caption styling is currently five dropdowns and three checkboxes behind a
"Render with style" button. Choosing "Bold Pop — one word, scale-pops" and waiting ~30 seconds to
discover what that means is a guess-and-check loop over an aesthetic decision, which is the worst
possible fit for a batch workflow. Captions are also the highest-leverage surface in short-form: the
tool most often singled out for per-clip visual quality in this category is singled out specifically
for caption craft, delivered through direct manipulation rather than a preset list.

Folding in #363 (caption **text** editing, parked 2026-07-30 by the #382 scope freeze) is a
deliberate reconciliation: once captions render live over the player, the text is right there and
editable in place. Shipping preview now and text-editing later would mean building the same surface
twice. Reversing the park is justified by the new dependency, and should be noted in
`docs/DECISIONS.md` when this issue is picked up.

Existing engine behavior to preserve: `clip_engine/captions.py:280` positions captions clear of the
Shorts subscribe-button overlay at ~y=70% of 1920. The preview must honor that safe zone or it will
lie about the output.

**Evidence in this repo.**
- `frontend/src/components/review/CaptionStylePanel.tsx:108-171` — five `<select>` + three
  checkboxes + a render button; no preview of any kind.
- `frontend/src/components/review/CaptionStylePanel.tsx:101` — `'Render queued — come back in ~30s.'`
- `clip_engine/captions.py:280` — the subscribe-overlay safe zone the preview must respect.
- `docs/issues-archive-2026-08-03.md` — Issue 363, `**Status:** PARKED 2026-07-30`, reversible.

**Industry standard checked.** Submagic is identified across comparisons as producing the most
visually striking captions in the category — animated text, emoji overlays, keyword highlighting —
and that is its primary differentiator
([Opus Clip vs Klap vs Submagic](https://www.submagic.co/vs/opus-pro-vs-klap),
[11 Best AI Clipping Tools in 2026](https://www.ssemble.com/blog/best-ai-clipping-tools-2026),
[Best Opus Clip Alternatives](https://www.choppity.com/blog/best-opus-clip-alternatives/)).
Opus exposes caption templates, fonts, colors, emoji, and keyword highlights as directly-edited
elements ([Opus Clip 2026 Complete Guide](https://aitoolsdevpro.com/ai-tools/opus-clip-guide/)).
Windows' 2026 editor ships text-animation presets auto-synced to the audio waveform, indicating
timing-aware caption animation is becoming baseline
([AI Video Tools in 2026](https://pixflow.net/blog/ai-video-tools-in-2026/)).

**Acceptance**
- [ ] Captions render live over the player at correct position and word timing
- [ ] Style changes (template, font, color, highlight) reflect immediately with no render call
- [ ] Caption block drag-positionable; position persists to the brand kit
- [ ] **Caption text editable in place** (closes #363); edits flow into the burned render
- [ ] Preview honors the `captions.py:280` safe zone
- [ ] Preview vs rendered output verified to agree on a fixture clip
- [ ] `docs/DECISIONS.md` records the #363 un-park and the merge rationale

---

### Issue 395: Resumable direct-to-R2 multipart upload — retire the 500 MB cap
- [ ] **Status:** BUILT 2026-08-05 (all code + tests green; DECISIONS 2026-08-05) — remaining: deploy, run `scripts/r2_set_cors.py https://autoclip.studio` on the VM, then the live Phase-4 acceptance drills (>2 GB upload, resume, session-expiry, CORS/ETag check) · **Batch:** C · **Size:** L · **Agent:** `python-senior-engineer` · **BETA BLOCKER**

**What we're doing.** Moving uploads to presigned S3-multipart direct to R2 — browser uploads parts
in parallel, the app server only signs and completes — with cross-session resume, a drag-and-drop
queue, and honest end-to-end progress. Raising or removing `UPLOAD_MAX_MB`.

**Why — the analysis.** This is the only item in the lane that blocks beta outright, because it
breaks the first thing a new user does.

`UPLOAD_MAX_MB = 500`. A 20-minute 1080p OBS recording is routinely 1–3 GB; 4K screen capture passes
500 MB in minutes. And raw upload is **mandatory by design**: under the YouTube ToS we never download
from a link, which is the correct and deliberate architecture (`routers/videos.py:336` docstring says
so explicitly). So the compliance posture makes upload the only door, and the cap locks it for
typical footage. Those two decisions were made at different times and were never reconciled.

The mechanism compounds the cap in three ways:

1. **Single-shot POST through the app server.** The whole file streams through FastAPI in 1 MB chunks
   to a temp file, then goes to R2 — so one upload occupies an app worker for the entire transfer,
   and the file traverses the network twice.
2. **No resume.** A dropped connection at 94% restarts at zero. On a 3 GB file over a home
   connection, that is close to a guaranteed failure mode.
3. **Dishonest progress.** `xhr.upload.onprogress` measures bytes reaching *our server*. The bar hits
   100% and then the user waits through the R2 leg with no indication anything is happening.

The existing implementation is otherwise careful — the 1 MB chunk loop, the early `Content-Length`
rejection (Issue 232), and the `try/finally` temp-file cleanup (Issue 104) are all good work. The
problem is architectural, not sloppy.

**Evidence in this repo.**
- `config.py:521` — `UPLOAD_MAX_MB: int = 500`; `.env.example:120`.
- `routers/videos.py:336-420` — single POST, streamed to a temp file, then R2.
- `routers/videos.py:344-353` (docstring) — "The raw file is always the source media — we never
  download from YouTube," establishing the mandatory-upload constraint.
- `frontend/src/components/dashboard/UploadVideoForm.tsx:56-99` — one `XMLHttpRequest`, no chunking,
  no resume; `xhr.onerror` → "connection lost. Please retry" restarts from zero.
- `frontend/src/components/dashboard/UploadVideoForm.tsx:66-68` — progress measured to the app server.
- `frontend/src/components/dashboard/UploadVideoForm.tsx:106-116` — a bare `<input type="file">`; no
  drop zone, no queue, no multi-file.

**Industry standard checked.** S3 multipart with presigned URLs is the reference pattern: parts
uploaded directly from the browser, the backend issuing signatures only, with a 5 MiB minimum part
size and up to 10,000 parts, and resumability because only failed parts are retried
([Uppy — AWS S3](https://uppy.io/docs/aws-s3/),
[Uppy — Choosing the uploader you need](https://uppy.io/docs/guides/choosing-uploader/),
[Resumable uploads with S3 Multipart — transloadit/uppy#2121](https://github.com/transloadit/uppy/issues/2121)).
Multipart is specifically recommended past ~100 MiB for throughput and network-failure recovery. The
alternative protocol is tus, purpose-built for resumable uploads
([Supabase — Resumable Uploads](https://supabase.com/docs/guides/storage/uploads/resumable-uploads),
[Supabase Storage v3: 50 GB resumable uploads](https://supabase.com/blog/storage-v3-resumable-uploads)).
Reference implementations of the presigned-multipart flow:
[File Upload Strategies with S3 + Uppy](https://www.fullstackfoundations.com/blog/javascript-upload-file-to-s3),
[uppy-s3_multipart server endpoints](https://github.com/janko/uppy-s3_multipart).
**R2 is S3-compatible**, so `@uppy/aws-s3` applies directly with no protocol work.

**Acceptance**
- [x] Presigned multipart direct to R2; app server signs parts and completes the upload only (`/videos/uploads/*` endpoints + `worker/storage.py` multipart helpers)
- [x] Resumable across page reload and dropped connection (parts already uploaded are not re-sent) — Uppy `@uppy/aws-s3` per-part retry + golden-retriever + `listParts`; **live drill still owed** (see status line)
- [x] `UPLOAD_MAX_MB` raised to a real ceiling or replaced by the minutes/quota check — quota is the gate + `UPLOAD_MAX_FILE_GB=20` abuse ceiling; `UPLOAD_MAX_MB` re-scoped to proxy paths
- [x] Drag-and-drop zone, multi-file queue, per-file progress reflecting true end-to-end state (R2-acked bytes)
- [x] Signature issuance authenticated and per-creator isolated; no unsigned write path to the bucket (`_validate_upload_key` 403s foreign/malformed keys; tested)
- [x] Abandoned multipart uploads cleaned up — R2's built-in 7-day auto-abort of incomplete multipart uploads (DECISIONS 2026-08-05; no sweeper needed)
- [x] Local-disk dev path preserved — `GET /videos/uploads/config` → proxy mode → legacy `/videos/upload` via the same Uppy queue UI
- [x] `.env.example`, `docs/SOT.md` storage section, and `docs/COMPLIANCE.md` updated
- [ ] Load-tested with a >2 GB file end to end — **deploy-gated**: requires prod deploy + one-time `scripts/r2_set_cors.py` run; drill list in DECISIONS 2026-08-05
- [x] *(added during build)* Session-expiry mid-upload pauses the queue and resumes after re-login without re-sending parts — driven by the 2026-08-05 prod incident (60-min JWT died inside a 40-min upload)

---

### Issue 396: Manual overrides — reframe, overlay, music
- [ ] **Status:** open · **Batch:** C · **Size:** L · **Agent:** `general-purpose`

**What we're doing.** Letting the creator correct and extend the automated render: manual crop-center
override on top of the computed reframe track, text/logo overlays, and a music bed with speech
ducking.

**Why — the analysis.** The current edit vocabulary is "delete a time range." Everything else is a
preset. That means when the automation is *nearly* right, the creator has no recourse — and
near-right is the common case for auto-reframe, where a single bad crop on a two-person shot ruins an
otherwise good clip.

The reframe piece is the highest value for the least work, because the hard part is built.
`clip_engine/reframe.py` already computes a smoothed, pan-clamped crop-center track and emits an
ffmpeg `sendcmd` script. What's missing is a way for a human to say "not there, here." That is an
override layer on an existing data structure, not new capability. Note the module's own header flags
that real multi-speaker ffmpeg output is still staging-pending, which makes a manual override more
valuable, not less: it is the escape hatch while the automation is unproven.

Overlays and music are lower down because they are net-new render paths, and every new render path
has to pass the loudness and eval gates.

**Evidence in this repo.**
- `clip_engine/reframe.py:297,371,489` — `build_crop_center_track`, `smooth_crop_track`,
  `compute_reframe_crop`; the track exists and is well-built.
- `clip_engine/reframe.py:35` — the module's own note that real multi-speaker ffmpeg output is
  render-env/staging-pending.
- `frontend/src/components/review/CaptionStylePanel.tsx:120-139` — aspect and background are
  dropdown presets with no manual control.
- `clip_engine/render.py:313` — `_punch_in_filter`, the only compositional effect available, and it
  is a checkbox.

**Industry standard checked.** Opus exposes overlays, logos, and brand elements as timeline objects —
draggable in position, resizable, with adjustable on-screen duration — and supports user-uploaded
images alongside brand templates carrying font, color, logo, intro and outro
([How to Add B-Roll to Opus Clip Videos 2026](https://edimakor.hitpaw.com/video-editing-tips/opus-add-own-broll.html),
[Opus Clip 2026 Complete Guide](https://aitoolsdevpro.com/ai-tools/opus-clip-guide/)).
Opus's ReframeAnything performs object-tracked reframing without manual keyframing, which is the
automation tier we already match ([OpusClip](https://www.opus.pro/home-a-b)) — the differentiator
available to us is letting the creator correct it. Descript's multitrack model keeps music and
speech on independent tracks so levels can be controlled separately
([Descript in 2026](https://www.fahimai.com/descript)).

**Acceptance**
- [ ] Manual crop-center override per clip, layered over the computed reframe track; visible on the
      player as a draggable frame
- [ ] Override persists in the edit document (#391) and is honored at render
- [ ] Text and logo overlays with position, timing, and brand-kit defaults
- [ ] Music bed with level control and speech ducking
- [ ] Loudness normalization gate green on every new render path
- [ ] Clip-quality eval harness green
- [ ] Render-time budget measured; no path exceeds the existing worker timeout

---

### Issue 401: Source-edit export — export the edited long-form video
- [ ] **Status:** open · **Batch:** C · **Size:** M · **Agent:** `python-senior-engineer`

**What we're doing.** Adding a render path that exports the edited **source** as a single file,
replacing the current honest-but-dead-end message in the long-form Export rail.

**Why — the analysis.** The long-form editor tells the user, in the product:
*"Full source-edit export isn't available — export individual rendered clips."* That message is
admirably honest — the `DECISIONS` note at `LongFormEditor.tsx:449-452` shows it was a deliberate
choice not to fake it — but it means the long-form mode is structurally a **clip-discovery browser,
not an editor**. You can find moments in your source and open them as shorts; you cannot edit the
source and get the source back.

That matters beyond the missing button. It is the reason the "Long-form source" tab feels thin: two
of its three affordances (master timeline, suggested clips) are navigation, and the third (transcript)
is reference. Nothing you do there produces an artifact. A creator who wants to remove three dead
sections from a 40-minute video and re-upload the tightened cut has no path.

The render primitives largely exist: `render_cleaned_clip_file` already concatenates keep-ranges with
per-segment audio filters and concat loudnorm (`render.py:664`, `:598`, `:616`), and
`build_summary_filtergraph` (`:815`) already handles multi-segment video assembly. The work is
applying them at source scale, which is mainly a resource-budget question — a 40-minute 4K export is a
materially different job from a 40-second clip, so timeouts, temp-disk headroom, and minute-cost
accounting all need explicit decisions.

**Evidence in this repo.**
- `frontend/src/components/editor/LongFormEditor.tsx:482-484` — the in-product message:
  "Full source-edit export isn't available — export individual rendered clips."
- `frontend/src/components/editor/LongFormEditor.tsx:449-452` — the deliberate DECISIONS note
  (2026-07-30) that it is not faked.
- `clip_engine/render.py:664` — `render_cleaned_clip_file`, the multi-segment concat path.
- `clip_engine/render.py:598,616` — `_audio_segment_filter`, `_measure_concat_loudnorm`.
- `clip_engine/render.py:815,847,879` — `build_summary_filtergraph`, `build_summary_render_cmd`,
  `render_summary_file` — multi-segment assembly already in service.
- `clip_engine/render.py:71` — `_run(..., timeout_s: float = 120.0)`, the default that a long export
  will exceed.

**Industry standard checked.** Export of the edited long-form timeline is baseline in every reference
tool — Descript's multitrack timeline exists precisely so the composed sequence can be exported as one
piece ([Descript in 2026](https://www.fahimai.com/descript),
[Descript Review 2026](https://filmora.wondershare.com/video-editor-review/descript-ai.html)).
Asset-management practice expects clear parent-child relationships between a master asset and its
derivatives, which presumes the master itself is an exportable artifact
([Cloudinary — Video Asset Management](https://cloudinary.com/guides/digital-asset-management/video-asset-management)).

**Acceptance**
- [ ] Source-edit export renders the keep-ranges of the full source to a single downloadable file
- [ ] Timeout, temp-disk, and memory budgets sized for long sources; `_run` timeout raised
      deliberately, not incidentally
- [ ] Minute cost defined and charged consistently with existing billing; spend guard respected
- [ ] Progress surfaced via the existing task SSE, not a "come back later" message
- [ ] Loudness normalization applied across the concatenated result
- [ ] The in-product "isn't available" message removed only when the path actually works
- [ ] Retention: export honors source-purge state; expired source fails with the existing clear error

---

### Issue 397: Wire the assistant to editing actions
- [ ] **Status:** open · **Batch:** C · **Size:** L · **Agent:** `python-senior-engineer`

**What we're doing.** Exposing edit operations as tools to the existing chat runner, so a creator can
say "cut the dead air in the first minute and tighten the intro" and get a **reviewable proposed
edit** — a diff on the timeline — that applies only on explicit confirmation.

**Why — the analysis.** This is the differentiated play in the lane, and the argument is a positioning
argument rather than a feature argument.

2026's convention is AI as the interface rather than a feature panel: describing edits in plain
language and having them execute. We have the two halves and they are not connected — `chat/runner.py`
and `chat/tools.py` exist with a working tool layer, and the edit operations exist as API endpoints,
but the assistant lives on its own page and cannot touch a clip.

The reason this is *ours* to win: everyone else's agent operates on a generic notion of a good clip.
Ours operates on the creator's own DNA, their own analytics, and their own confirmed preferences.
"Tighten this the way my audience likes" is a sentence only this product can answer, and it is the
North Star restated as an interaction. Every competitor can ship agentic editing; none can ship
agentic editing that knows the channel.

Two hard constraints. **Nothing auto-applies** — proposals are reviewed as a diff and confirmed, which
is both a trust requirement and consistent with the honesty constraint. And every tool call passes the
same budget, kill-switch, and per-creator isolation checks as the HTTP path — the lesson of archived
Issue 357, where `/clips/generate` bypassed the `llm_generation` kill switch and `require_budget`. A
new invocation surface is a new place to make that mistake.

**Evidence in this repo.**
- `chat/runner.py`, `chat/tools.py`, `chat/prompt.py`, `chat/intake.py` — the tool-calling layer,
  built and working.
- `frontend/src/pages/Chat.tsx` — the assistant as a standalone page with no edit surface.
- `docs/issues-archive-2026-08-03.md` — Issue 357 (`/clips/generate` bypassed kill switch +
  `require_budget`, DONE 2026-07-20): the precedent this issue must not repeat.
- `dna/`, `preference/`, `knowledge/` — the channel-knowledge modules the assistant would draw on.

**Industry standard checked.** AI is now the primary interface rather than a feature panel, with
plain-English edit instructions executed directly
([AI Video Tools in 2026](https://pixflow.net/blog/ai-video-tools-in-2026/)). Agentic editing
automates trimming, silence removal, caption styling, vertical resizing, and B-roll insertion in
parallel, with commands of the form "remove filler words, add captions, and generate B-roll"
([Agentic Video Editing for 2026](https://www.reelnreel.com/agentic-video-editing/),
[What Is AI Video Editing in 2026 — Overlap](https://overlap.ai/blogs/how-do-agentic-tools-work),
[AI Video Editing — ChatCut](https://chatcut.io/blog/ai-video-editing)). TikTok's Agentic Hub
(Cannes Lions 2026) targets automating up to 85% of routine editing
([AI Video Agent for Content Creators 2026](https://resource.digen.ai/ai-video-agent-for-content-creators-2026/)).

**Acceptance**
- [ ] Edit tools exposed to the chat runner: cut, caption style, reframe override, overlay
- [ ] Proposed edits render as a reviewable diff on the timeline before applying
- [ ] **Nothing applies without explicit creator confirmation**
- [ ] Every tool call enforces per-creator isolation, `require_budget`, and the `llm_generation`
      kill switch — regression test per tool (the Issue 357 lesson)
- [ ] Assistant reachable from the Editor, not only the standalone page
- [ ] `/claude-api` skill consulted for SDK patterns; prompt caching preserved; tokens logged
- [ ] No response promises virality (structural test green)

---

# Batch D — Asset management

### Issue 398: Video library — search, filter, sort, bulk actions
- [ ] **Status:** open · **Batch:** D · **Size:** M · **Agent:** `general-purpose`

**What we're doing.** Turning the video table into a real library: grid/list toggle, search, status
filter, sort, multi-select with bulk actions, and a proactive retention badge.

**Why — the analysis.** The library has no search, filter, sort, or bulk operation of any kind. At
three videos that is invisible; at fifty it is unusable, and a creator uploading weekly reaches fifty
inside a year. This is the surface that quietly decides whether the product is usable in month six.

The retention point is the sharper one. Source media is purged on a retention schedule, and today the
creator discovers this **after** opening the editor and finding a dead player with "Source media
expired." The honest error handling there is good work — but it is reactive. The creator had no
warning and no chance to act, and the thing they lost is their own footage. A days-ahead badge in the
library converts a dead end into a decision.

Layout is also just crowded: three differently-styled buttons stacked in one table cell, with a bare
"Why no clips?" text link between them, which is what happens when actions accumulate in a cell that
was designed for one.

**Evidence in this repo.**
- `frontend/e2e/__screenshots__/desktop-dashboard.png` — the table, the stacked mixed-style buttons,
  the bare "Why no clips?" link.
- `frontend/src/pages/Dashboard.tsx` — no search, filter, sort, or selection state.
- `frontend/src/components/editor/LongFormEditor.tsx:328-342` — "Source media expired" surfaced only
  on entering the editor.
- `frontend/src/components/editor/LongFormEditor.tsx:304` — `sourceAvailable` derived from
  `video.clippable`, which the library already has access to and does not display.

**Industry standard checked.** Video-library practice expects search across metadata from a single
box, multi-dimensional filtering, and bulk operations applying settings to many assets at once
([Cloudinary — Video Asset Management](https://cloudinary.com/guides/digital-asset-management/video-asset-management),
[Video Asset Management Software 2026](https://filestage.io/blog/video-asset-management-software/),
[Best DAM Software for Video 2026](https://thedigitalprojectmanager.com/tools/best-digital-asset-management-software-for-video/)).

**Acceptance**
- [ ] Grid/list toggle; grid uses the #387 thumbnails
- [ ] Search by title; filter by status; sort by date / duration / clip count
- [ ] Multi-select with bulk delete; destructive actions confirm and are per-creator isolated
- [ ] Retention badge showing days-until-purge **before** it happens, from `clippable` + ingest date
- [ ] Row actions consolidated into a single overflow menu (uses #385's DropdownMenu)
- [ ] Query performance verified at 500+ videos; pagination or virtualization as needed

---

### Issue 399: Clip triage grid
- [ ] **Status:** open · **Batch:** D · **Size:** M · **Agent:** `general-purpose`

**What we're doing.** A scannable grid of clip cards — thumbnail, duration, fit tier, principle — with
hover-scrub and inline Keep/Drop, alongside the existing detail view.

**Why — the analysis.** Review is one clip at a time behind "Next clip →". For a creator with twenty
candidates from one source, that is twenty sequential page states to make what is essentially one
batch decision. The linear flow is well-suited to *deciding* on a clip and badly suited to *triaging*
a set, and triage is what actually happens first.

There is a product reason beyond speed. Our differentiator is that ranking reflects the creator's own
DNA — but a sequential flow hides the ranking, because you never see the ordering as an ordering. A
grid makes the engine's judgment legible: strong-fit clips visibly cluster at the top, and the
creator can evaluate our claim in one glance instead of trusting it clip by clip. The moat is only
persuasive if it is visible.

This depends on #387 for thumbnails and pairs with #388's fit-tier presentation.

**Evidence in this repo.**
- `frontend/src/pages/Review.tsx` — single-clip flow with "Next clip →".
- `frontend/e2e/__screenshots__/desktop-review.png` — one clip, four stacked rail cards.
- `frontend/src/components/editor/LongFormEditor.tsx:408-431` — the "Suggested clips" list is the
  closest existing thing: a text list with tier labels, no imagery.
- `frontend/src/lib/fit.ts`, `frontend/src/components/ui/fit-badge.tsx` — tier presentation to reuse.

**Industry standard checked.** Category tools present generated clips as a scannable set with per-clip
scores and previews so a creator can triage before refining
([Opus Clip 2026 Complete Guide](https://aitoolsdevpro.com/ai-tools/opus-clip-guide/),
[90 Days Deep in Opus Clip](https://sendshort.ai/guides/opus-review/),
[11 Best AI Clipping Tools in 2026](https://www.ssemble.com/blog/best-ai-clipping-tools-2026)).

**Acceptance**
- [ ] Grid of clip cards: thumbnail, duration, fit tier, cited principle
- [ ] Hover-scrub preview
- [ ] Keep/Drop inline from the grid; detail view retained for deep review
- [ ] Ranking order preserved and visibly explained
- [ ] Keyboard navigable (arrows + keep/drop); axe pass
- [ ] Honesty constraint intact — tiers are fit estimates, never virality

---

### Issue 402: Library depth — collections, storage visibility, and recoverable delete
- [ ] **Status:** open · **Batch:** D · **Size:** M · **Agent:** `general-purpose`

**What we're doing.** The organizational layer #398 deliberately leaves out: collections/tags, a
storage-and-minutes indicator, and a recoverable delete (trash with restore) instead of immediate
permanent deletion.

**Why — the analysis.** Three gaps that share a theme — the library has no concept of the creator's
own organizational intent, and no safety net.

**Collections and tags.** A creator with a podcast, a tutorial series, and one-off uploads has no way
to express that. Metadata is what makes a library navigable at scale; without it, search (#398) is
the only affordance and it only works if you remember the title.

**Storage and minutes visibility.** Minutes are purchased and consumed, and source media occupies
storage under a retention policy, but neither is visible as a running total. The creator can see a
minute balance in the nav; they cannot see what is stored, what is about to be purged, or what is
consuming their quota. That is the information needed to make the decisions the retention policy
forces on them.

**Recoverable delete.** Deletion is immediate and permanent. For a product whose entire value is the
creator's own footage — footage we may be the only remaining copy of once they've cleared their local
drive — no undo on delete is a data-loss design. This is distinct from the GDPR erasure path
(`DELETE /auth/me`), which is a compliance obligation and should stay immediate and complete; a trash
is for ordinary accidental deletion of a single asset.

**Evidence in this repo.**
- `frontend/src/pages/Dashboard.tsx` — no collection, tag, or grouping concept in the model or UI.
- `frontend/e2e/__screenshots__/desktop-dashboard.png` — the nav shows `142 min` and nothing about
  stored media.
- `docs/COMPLIANCE.md` — the retention/purge policy driving the storage question.
- `frontend/src/components/profile/AccountDeletion.tsx` — the erasure path that must remain
  immediate and is explicitly out of scope for the trash.
- `frontend/src/components/editor/LongFormEditor.tsx:304,328-342` — purge state exists per-video
  (`clippable`) but is never aggregated for the creator.

**Industry standard checked.** Metadata captured at ingest is described as the index that makes a
library usable, with filtering across many dimensions and clear parent-child relationships between
masters and derivatives
([Cloudinary — Video Asset Management](https://cloudinary.com/guides/digital-asset-management/video-asset-management),
[Best DAM Software for Video 2026](https://thedigitalprojectmanager.com/tools/best-digital-asset-management-software-for-video/),
[The Best DAM Software in 2026](https://www.mediavalet.com/blog/best-digital-asset-management-platform)).

**Acceptance**
- [ ] Collections (or tags) creatable, assignable, and filterable in the library
- [ ] Storage + minutes summary: what is stored, what is scheduled for purge and when, quota consumed
- [ ] Soft-delete with a trash view and restore; retention window documented
- [ ] Purge and GDPR erasure (`DELETE /auth/me`) remain **immediate and complete** — trash does not
      delay either; regression test asserts this
- [ ] Per-creator isolation on every new query
- [ ] `docs/COMPLIANCE.md` updated with the soft-delete window and its interaction with purge

---

# Batch E — Breadth cluster (filed, deliberately not funded)

> **Scope note.** These three were surfaced by the 2026-08-03 field comparison and are filed at the
> owner's explicit instruction so the decision is recorded rather than forgotten. They are **not
> scheduled**, and they sit downstream of everything above. The reasoning follows archived Issue 382
> (scope freeze — "deprioritize the breadth cluster, fund the moat"): each of these makes us more like
> a generic clipper, while Batches A–D make the channel-knowledge product usable. Revisit only after
> Batch D closes. **#403 is the most likely of the three to need reversing** — see its note.

### Issue 403: AI + stock B-roll insertion
- [ ] **Status:** filed, NOT SCHEDULED · **Batch:** E · **Size:** L · **Agent:** `general-purpose`

**What we're doing.** Contextual B-roll — stock library and/or generated — auto-inserted at points
the engine identifies, with manual override of clip choice and duration.

**Why — the analysis, and why it's deferred.** Honest position: **this is table stakes in the
category and we do not have it.** Opus inserts contextually relevant AI-generated or stock B-roll to
cover visual gaps; Submagic ships B-roll from a stock library as part of its caption-first package;
agentic editing treats B-roll insertion as one of the parallel automated steps. A creator comparing
tools feature-by-feature will notice.

The counter-argument, and the reason it sits in Batch E: B-roll is table stakes for a *generic
clipper*, and we are not selling a generic clipper. Our claim is that we know the channel. B-roll
does not deepen that loop — it is the same stock footage every competitor inserts, and inserting it
better is not a claim we can win on. Every hour spent here is an hour not spent on #397, where our
data advantage is the whole point.

It is also the most expensive item in the lane: a stock-library licensing relationship or a
generation-model spend line, a search/selection UX, new render composition paths, and a materially
larger per-render cost — against a ≤100-user beta.

**Reversal trigger (why this is the likeliest to move):** if beta creators cite missing B-roll as a
reason for not converting, or if the assistant work in #397 makes contextual insertion cheap because
the model is already reasoning about content, reopen immediately and log the reversal in
`docs/DECISIONS.md`.

**Evidence in this repo.** No B-roll path exists. `clip_engine/render.py` composes a single source
with filters; there is no secondary-media ingest, no asset library, and no compositing timeline.

**Industry standard checked.** Opus inserts contextually relevant AI-generated or stock B-roll to
cover visual gaps, with a choice between royalty-free stock and generated visuals for abstract
concepts, and lets users upload their own images, drag to move, pinch to resize, and drag overlay
length in the timeline
([Opus Clip 2026 Complete Guide](https://aitoolsdevpro.com/ai-tools/opus-clip-guide/),
[How to Add B-Roll to Opus Clip Videos 2026](https://edimakor.hitpaw.com/video-editing-tips/opus-add-own-broll.html)).
Submagic ships B-roll from a stock library alongside animated captions and emoji overlays
([Opus Clip vs Klap vs Submagic](https://www.submagic.co/vs/opus-pro-vs-klap),
[11 Best AI Clipping Tools in 2026](https://www.ssemble.com/blog/best-ai-clipping-tools-2026)).
Agentic tooling auto-inserts contextual B-roll as part of pacing adjustment
([Agentic Video Editing for 2026](https://www.reelnreel.com/agentic-video-editing/),
[What Is AI Video Editing in 2026 — Overlap](https://overlap.ai/blogs/how-do-agentic-tools-work)).

**Acceptance (when scheduled)**
- [ ] Explicit `[DEC]` in `docs/DECISIONS.md` reversing this deferral, with the trigger that caused it
- [ ] Stock source licensing (or generation spend) resolved before any build
- [ ] B-roll suggestions cite why they were chosen, consistent with the principle-citation rule
- [ ] Manual override of clip choice, in/out, and duration
- [ ] Per-render cost measured and reflected in minute pricing
- [ ] Eval harness green; no regression to setup-start geometry

---

### Issue 404: Multi-track timeline — layers for video, audio, and overlays
- [ ] **Status:** filed, NOT SCHEDULED · **Batch:** E · **Size:** XL · **Agent:** `general-purpose`

**What we're doing.** A layered timeline: independent tracks for source video, speech audio, music,
overlays, and B-roll, with per-track lock, mute, and level.

**Why — the analysis, and why it's deferred.** Multi-track is the structural prerequisite for
compositional editing, and it is the honest architectural answer to several Batch C items — #396's
music bed and overlays, and #403's B-roll, are all "extra layers" being simulated as filter
parameters on a single track. Descript's model is explicit: separate audio tracks per speaker, music,
sound effects, and B-roll, each independently controlled in one timeline.

It is deferred for two reasons. First, **sequencing** — #390 (Timeline v2) must land first regardless,
and much of that work (pointer events, zoom, ruler, snapping, edge-dragging) is the foundation
multi-track builds on. Doing single-track properly is a prerequisite, not a detour. Second, **fit** —
multi-track serves compositional editing of long-form. Our product is short-form clip extraction from
a single source with an automated reframe. The number of tracks a 40-second vertical clip needs is
close to two, and #396 covers that with filter parameters at a fraction of the cost.

The real decision point: if #396 and #403 both ship, simulating four layers through filter parameters
will become the harder path, and multi-track becomes the cheaper refactor. That is the trigger.

**Evidence in this repo.**
- `frontend/src/components/editor/Timeline.tsx` — one track, one waveform, one cut list; no track
  model in the component or the types.
- `frontend/src/types.ts` — `EditorCut` is `{start_s, end_s, indices}`; there is no track dimension.
- `clip_engine/render.py:598,798` — `_audio_segment_filter` / `_video_segment_filter` operate on a
  single source stream.

**Industry standard checked.** Descript supports multitrack editing with layered audio, video, and
graphics — separate audio tracks per speaker, background music, sound effects, and B-roll, each
independently controlled in the same timeline, alongside a traditional timeline for fine control
([Descript in 2026](https://www.fahimai.com/descript),
[Descript Review 2026](https://filmora.wondershare.com/video-editor-review/descript-ai.html),
[Descript Complete Guide 2026](https://aitoolsdevpro.com/ai-tools/descript-guide/)).
Multitrack timelines with ripple editing, trim handles, and track locking are the standard NLE feature
set ([Kdenlive — Editing](https://docs.kdenlive.org/en/cutting_and_assembling/editing.html),
[EditMentor — Timeline](https://help.editmentor.com/en/articles/4592281-timeline)).

**Acceptance (when scheduled)**
- [ ] Explicit `[DEC]` in `docs/DECISIONS.md` reversing this deferral
- [ ] #390 shipped and stable first (non-negotiable prerequisite)
- [ ] Track model in types, edit document (#391), and render pipeline
- [ ] Per-track lock, mute, solo, level
- [ ] Render composes tracks deterministically; loudness gate green
- [ ] Eval harness green

---

### Issue 405: Transitions, speed ramps, and zoom keyframes
- [ ] **Status:** filed, NOT SCHEDULED · **Batch:** E · **Size:** L · **Agent:** `general-purpose`

**What we're doing.** Time-based effects: transitions between segments, speed ramping, and
keyframed zoom/pan beyond the current single punch-in checkbox.

**Why — the analysis, and why it's deferred.** These are the effects that separate "cut together"
from "edited," and we have exactly one of them: `zoom_on_peak`, a boolean, implemented as
`_punch_in_filter` at a single peak offset. There is no keyframing, no ramp, and no transition —
concatenated segments hard-cut.

Deferred because this is the clearest case in the lane of craft-for-craft's-sake relative to our
thesis. A transition does not know anything about the creator's channel; it looks the same for
everyone. And the short-form convention actively favors hard cuts — pacing comes from cut rhythm,
not from dissolves. Speed ramping is the most defensible of the three (it changes pacing, which is a
clip-quality lever the engine already reasons about), but it is still downstream of everything that
makes the tool usable at all.

There is also a real technical dependency: keyframed zoom/pan wants the same override-track
infrastructure as #396's manual reframe. Building keyframes before that override layer exists means
building it twice.

**Evidence in this repo.**
- `clip_engine/render.py:313` — `_punch_in_filter(peak_offset_s, out_w, out_h)`, single-point, no
  keyframe track.
- `frontend/src/components/review/CaptionStylePanel.tsx:148-155` — "Punch-in at peak" as a checkbox.
- `clip_engine/render.py:798` — `_video_segment_filter`; segments concatenate with hard cuts.
- `clip_engine/reframe.py:452` — `sendcmd` script generation, the closest existing thing to a
  keyframe track and the natural foundation to extend.

**Industry standard checked.** Windows' 2026 editor ships AI text-animation presets auto-synced to
the audio waveform, indicating timing-aware motion is becoming baseline rather than premium
([AI Video Tools in 2026](https://pixflow.net/blog/ai-video-tools-in-2026/)). Opus's
ReframeAnything performs object-tracked reframing explicitly **without manual keyframing**, which is
evidence the category is moving away from hand-keyframed motion toward automated tracking — the tier
we already occupy ([OpusClip](https://www.opus.pro/home-a-b),
[Opus Clip 2026 Complete Guide](https://aitoolsdevpro.com/ai-tools/opus-clip-guide/)). Standard NLE
speed and transition handling: [Kdenlive — Editing](https://docs.kdenlive.org/en/cutting_and_assembling/editing.html).

**Acceptance (when scheduled)**
- [ ] Explicit `[DEC]` in `docs/DECISIONS.md` reversing this deferral
- [ ] #396's override-track infrastructure shipped first (keyframes extend it)
- [ ] Transitions between concatenated segments, with a sane short-form default (hard cut)
- [ ] Speed ramping with pitch-corrected audio
- [ ] Keyframed zoom/pan replacing the single-point punch-in, backward compatible with `zoom_on_peak`
- [ ] Loudness gate and eval harness green
- [ ] Render-time budget measured per effect

---

## Carried forward — the only open items from the archive

Everything else in issues **345–383 is DONE** (verified 2026-08-03 against the `**Status**` line of
each brief in `docs/issues-archive-2026-08-03.md`, consistent with `docs/PROJECT_STATE.md`'s
2026-07-31 W5 entry). These are the exceptions:

- [ ] **363 Caption TEXT editing** — PARKED 2026-07-30 by the #382 scope freeze, reversible.
      **Now merged into #394**, which builds the live-preview surface it needs. Un-park when #394 is
      picked up and record the reversal in `docs/DECISIONS.md`.
- [ ] **376(b) No-auth public demo** — DESCOPED 2026-07-30 (reversible). 376(a), the public marketing
      landing, shipped. Worth revisiting after Batch A, since the demo's value depends on the surface
      looking credible.
- [ ] **381 Chat-density signal via live capture** — OPEN, W3, size L, external verify. The only
      genuinely unbuilt carry-over. Adjacent to #397 but independent.

---

# Hygiene — outside the L25 lane

### Issue 406: Clear the 6 `pip-audit` advisories in aiohttp and cryptography
- [x] **Status:** **DONE 2026-08-04** · **Batch:** — (hygiene) · **Size:** S · **Agent:** `python-senior-engineer`

**What we're doing.** Bumping `aiohttp` 3.14.1 → **3.14.3** and `cryptography` 48.0.1 → **50.0.0**
to clear all six advisories the `pip_audit` gate has been failing on since the Batch A baseline, and
returning that gate to green **without** adding anything to `[tool.pip-audit].ignore-vulns`.

**Why — the analysis.** The gate has been red across the whole of L25 Batch B. It is genuinely
pre-existing — `git diff main -- requirements*.txt '*.py'` on `wave/l25-batch-b` is empty, so no
editor work caused it — and it was correctly logged rather than papered over. But a permanently red
security gate is a gate nobody reads, and `cryptography` is the library behind the Fernet token path
(`crypto.py`), which makes "we'll get to it" the wrong posture even when the specific advisories miss us.

**The six, triaged against actual usage.** Exposure is genuinely low; the *fix* is cheap. Both are
true, and the second is the reason to just do it.

| Advisory | Pkg | Fix | Real exposure here |
|---|---|---|---|
| CVE-2026-69243 (GHSA-mfx4-hv73-q22v) — request smuggling via WebSocket upgrade | aiohttp | 3.14.2 | **None.** Server-side component only; we never run an aiohttp server. |
| CVE-2026-59881 (GHSA-mq44-7p77-q5h7) — client decompresses RSV1 frames without negotiated `permessage-deflate` | aiohttp | 3.14.2 | **None.** WebSocket path only. `ingestion/transcribe.py:130-150` uses Deepgram **prerecorded** (REST); the live/streaming client is never constructed. |
| CVE-2026-69244 (GHSA-cq5v-8q36-5273) — OOB heap read in the C response parser while formatting a malformed-response error | aiohttp | 3.14.3 | **Low but real.** Needs a hostile/faulty response from Deepgram or Voyage. |
| CVE-2026-69248 (GHSA-m2h6-j472-rp4c) — name-constraint escape: constrained CA + wildcard leaf SAN | cryptography | 49.0.0 | **None.** X.509 path validation; we import `Fernet`/`MultiFernet` only. |
| CVE-2026-69249 (GHSA-jwv3-5hgf-82ww) — exponential blowup on chains with duplicate self-signed certs | cryptography | 49.0.0 | **None.** Same — X.509 verifier unused. |
| CVE-2026-69247 (GHSA-g6cj-pr64-35w5) — PKCS#7 decryption oracle (distinguishable `encryptedKey` failures) | cryptography | 50.0.0 | **None.** `pkcs7_decrypt_*` unused. |

The seventh advisory pip-audit reports — pytest `PYSEC-2026-1845` / `GHSA-6w46-j5rx-g56g`, the
predictable `/tmp/pytest-of-{user}` path — is **already accepted-risk** in
`pyproject.toml [tool.pip-audit].ignore-vulns` with a written justification (dev/CI only; the fix is
pytest 9, which `pytest-asyncio<0.25` caps). That entry is why the gate reads **6** and not 7.
**Leave it alone** — it is not part of this issue.

**Why this is a small change.** Neither bump is constrained by anything:
- `aiohttp` is **transitive only** — no `import aiohttp` anywhere in the tree. Consumers are
  `deepgram-sdk` (`aiohttp>=3.9.1`) and `voyageai` (unpinned). 3.14.3 is a patch release inside both ranges.
- `cryptography` shows `Required-by:` **(nothing)** — it is a direct dependency with no downstream
  consumers in the venv, so the 48 → 50 major bump has exactly one caller to satisfy: `crypto.py`'s
  Fernet/MultiFernet usage, which the `crypto` module's **100.0% coverage floor** already pins.

**Evidence in this repo.**
- `requirements.txt:40` — `cryptography==48.0.1` · `requirements.txt:49` — `aiohttp==3.14.1`.
- `crypto.py:3` — `from cryptography.fernet import Fernet, InvalidToken, MultiFernet` (the whole
  surface we use); `config.py:6` likewise.
- `ingestion/transcribe.py:130-150` — `_deepgram_prerecorded_options()`; prerecorded REST, no live socket.
- `pyproject.toml [tool.pip-audit].ignore-vulns` — the pytest and pip entries, each with a rationale.
- `docs/OFF_COURSE_BUGS.md` — logged 2026-08-03 during Batch B; **this issue is that log entry promoted.**

**Industry standard checked.** OSV/GHSA advisory data via `pip-audit` (2026-08-04 run against the
installed environment; fix versions above are OSV's `fix_versions`). The house rule is the one
already written into `pyproject.toml`: an `ignore-vulns` entry requires a stated reason *and* a
re-evaluation trigger, and is reserved for advisories with no fix in our compatible range. Both of
these have a fix in range, so neither qualifies — bump, don't ignore.

**Acceptance**
- [x] `aiohttp==3.14.3` and `cryptography==50.0.0` pinned with `==` in `requirements.txt`
- [x] `pip-audit` reports **0** vulnerabilities beyond the existing justified `ignore-vulns` entries
      (7 raw findings → 1, and that one is the already-justified pytest entry)
- [x] **Nothing new added to `ignore-vulns`** — the file is byte-identical
- [x] `run_layer0.py` `pip_audit` gate green. **No baseline change was needed:**
      `docs/assessment/baselines.json` already had `pip_audit_vulns: 0` and was never relaxed, so
      the gate stayed honestly red for the whole of Batch B rather than being bumped away. The fix
      makes reality meet the baseline, which is the right direction.
- [x] Full pytest run green (2572), `crypto` module coverage still **100.0**
- [x] Deepgram + Voyage paths exercised (unit lane) after the aiohttp bump —
      `tests/ingestion/` + `tests/test_crypto.py` + `tests/test_doctor.py` green
- [x] `docs/OFF_COURSE_BUGS.md` entry marked resolved, pointing at this issue

**Verified before bumping** (`cryptography` 48 → 50 is two majors):
the [changelog](https://cryptography.io/en/latest/changelog/) confirms **no Fernet or MultiFernet
API change** in 49.0.0 or 50.0.0 — the only surface this codebase uses. Every backward-incompatible
change is in X.509 parsing, ChaCha20 nonce semantics, FFDH deprecation or OCSP version validation,
none of which is imported here. The dropped platforms (x86_64 macOS wheels, 32-bit Windows) do not
apply to `python:3.12-slim` on linux/amd64.

---

# Assessment 2026-08-04 — verified findings from the Batch A/B review

Filed from the post-Batch-B assessment (all gates independently re-run and green: backend 2581,
frontend 595/83, Playwright 76, Layer 0 clean). Every finding below was confirmed by code trace or
direct repro before filing — none is speculative. 407–409 are Issue-391 hardening and should land
**before or alongside #393**, which builds directly on the same editor state.

> **ALL SIX CLOSED 2026-08-04.** 407–411 + the two deferred 387 proofs shipped as **PR #74**
> (`9672de8`); 412 as **PR #75** (`500b715`). Both merged and deployed. **Batches A and B are
> 100% closed** — no unchecked box, no deferred proof, no open bookkeeping contradiction remains.

### Issue 407: Conflict resolution is INVERTED in the resume-from-cache path
- [x] **Status:** **DONE 2026-08-04** (close-out wave) · **Batch:** C (pre-393 hardening) · **Size:** S

**What's wrong.** `deriveSeed` (`frontend/src/hooks/useEditDocument.ts:78-89`) handles "dirty
offline cache at the same revision" by seeding `history` with the **server** document and stuffing
the **local cached** document into `conflict.serverDoc` — the reverse of the 409-driven conflict
path (`:223-235`), where the naming is correct. Every downstream consumer trusts the names, so in
this branch both buttons do the opposite of their labels (`SaveStatus.tsx:47-57`):
- **"Keep my edits"** (`keepMine`, `:416-425`) keeps the server's document and schedules a save of
  it; the save's `writeCache` then overwrites the dirty cache — the creator's unsaved work is
  **destroyed by the button that promises to keep it**.
- **"Use the other version"** (`takeTheirs`, `:439-442` → `adoptServerDocument`) adopts the local
  cached document but writes it `dirty: false` and sets state `idle` — the editor displays content
  the server does not have while claiming it is synced, and never schedules the save.
- The copy "This clip was edited somewhere else" is also false here: `cached.revision ===
  data.revision` means the server has NOT moved on; the true situation is "you have unsaved work
  from a previous session on this device."

**Why it survived review:** `useEditDocument.test.tsx` covers `keepMine`/`takeTheirs` only via the
409 path (`:140-205`), where the wiring is correct. No test seeds a dirty cache before mount.
Reachable in production: any save that never lands (network loss, crash inside the debounce window,
failed flush on unload) leaves `dirty: true` at the server's revision.

**Acceptance criteria**
- [x] The cache-resume branch presents the LOCAL dirty document as "mine" and the SERVER document
      as "theirs", consistent with the 409 path — either by seeding `history` from the cache, or by
      renaming/restructuring `ConflictInfo` so the fields cannot be crossed again
- [x] "Use the other version" leaves the client in a state that is actually synced (or actually
      saving) — never `idle` with divergent content
- [x] Conflict copy distinguishes "edited somewhere else" from "unsaved work from your last session"
- [x] A test mounts with a pre-seeded dirty cache and asserts which document each button keeps —
      the test that was missing

### Issue 408: `POST /clips/{id}/cuts` silently ignores a legacy `segments` body
- [x] **Status:** **DONE 2026-08-04** (close-out wave) · **Batch:** C (pre-393 hardening) · **Size:** S

**What's wrong.** `CutsIn` (`routers/clips.py:1075-1090`) has no `extra="forbid"`, and Pydantic v2
defaults to ignoring unknown fields — verified by direct repro:
`CutsIn.model_validate({'base_revision': 3, 'segments': [...]})` validates cleanly and drops
`segments`. The pinning test (`tests/test_edit_document.py:585-592`, "the legacy shape is REJECTED,
not ignored") posts `segments` **without** `base_revision`, so its 422 comes from the missing
required field — it does not test what its docstring claims. A stale client posting
`{base_revision, segments}` gets a 202 and a render of the server document, its posted cut list
silently discarded — precisely the "hoping two copies agreed" failure mode the 391 design notes say
was eliminated.

**Acceptance criteria**
- [x] `CutsIn` (and `EditDocumentIn`) get `model_config = ConfigDict(extra="forbid")`
- [x] The rejection test posts a VALID `base_revision` plus `segments` and asserts 422
- [x] A quick sweep of the other request models on paid/render routes for the same gap, with a
      one-line note of the outcome

### Issue 409: Persistence has zero e2e coverage — the mock API has no edit-document route
- [x] **Status:** **DONE 2026-08-04** (close-out wave) · **Batch:** C (pre-393 hardening) · **Size:** S

**What's wrong.** `frontend/e2e/fixtures/mock-api.ts` mocks peaks and transcript but has no
`/clips/{id}/edit-document` route; the GET falls through the catch-all `json(route, {}, 200)`
(`:521`). `useEditDocument` silently tolerates the malformed body — `initHistory(undefined)` seeds
`present: undefined` and `doc` falls back to `EMPTY_DOC` (`useEditDocument.ts:205`) — so every
Playwright editor test runs against a hook in a degraded state nobody designed, and hydration,
autosave, save-status, and conflict UI have **zero** e2e coverage. This is how Issue 407 shipped:
the one lane that renders real UI against realistic responses never exercised the branch.

**Acceptance criteria**
- [x] `mock-api.ts` serves a realistic `GET`/`PUT /clips/{id}/edit-document` (revision advancing on
      PUT), and the catch-all logs or fails on unmatched API routes so the next missing mock is loud
- [x] One e2e: make a cut in the editor, observe the save indicator reach "Saved"
      *(shipped as: hydrate a stored cut via `editDocSeed`, commit through "Clear all", await
      "Saved" — the same commit → autosave → Saved loop; authoring a cut via I/O needs playhead
      movement against a stub `<video>`, which is exactly the kind of flake an e2e gate cannot
      carry)*
- [x] `useEditDocument` treats a body without a numeric `revision`/`doc` as an error state rather
      than silently degrading

### Issue 410: Short-form timeline's "waveform unavailable" state is invisible to sighted users
- [x] **Status:** **DONE 2026-08-04** (close-out wave) · **Batch:** C or first polish pass · **Size:** S

**What's wrong.** When peaks are absent, `MasterTimeline.tsx:175` renders a visible explanation
("Waveform unavailable for this source — the audio is past its retention window") but the
short-form `Timeline.tsx:173` only sets `ariaLabel` — a sighted creator sees an unexplained empty
band (visible in `Editor.png`, 2026-08-04 screenshot) and reasonably concludes the timeline is
broken. The honesty rule (real data or honestly absent) is met for screen readers and not for
everyone else.

**Acceptance criteria**
- [x] The short-form timeline shows a visible unavailable message consistent with the master
      timeline's, sized so it does not crowd the rail
- [x] Both messages share one source string (DRY)

### Issue 411: `/settings` contrast failures — promote from OFF_COURSE_BUGS and join the axe gate
- [x] **Status:** **DONE 2026-08-04** (close-out wave) · **Batch:** hygiene · **Size:** S

**What's wrong.** `docs/OFF_COURSE_BUGS.md:32` (2026-08-03) has carried "open — promote to an
issue / not yet tracked" through two batch closes — a standing violation of the off-course rule in
`CLAUDE.md`. The "Soon" preview rows (`80a7474`, 2026-06-23) sit at 2.14–2.54:1 contrast under
`pointer-events-none opacity-50` while remaining in the accessibility tree, and `/settings` is
consequently the one dense route excluded from `e2e/a11y.spec.ts`. Likely a one-attribute fix
(`aria-hidden` on the decorative mock) plus restoring the route to the gate.

**Acceptance criteria**
- [x] The mock preview rows are `aria-hidden` (or restyled to pass), `/settings` is added to the
      axe gate, and the gate is green
- [x] The OFF_COURSE_BUGS row is flipped to fixed with this issue number

### Issue 412: Presentation balance — the screens read emptier than the product is
- [x] **Status:** **DONE 2026-08-04** — PR #75 (`500b715`), merged + deployed · **Batch:** — (shipped as the close-out wave's follow-up) · **Size:** M

**What's wrong.** From the 2026-08-04 screenshot review (`Review.png`, `Channel.png`,
`Insights 1-3.png`, `Videos.png`, `Assistant.png`), judged against `docs/UI.md`:
- **Review**: the left "Why this clip" column is ~60% empty dark canvas below the card at 1080p —
  the screen's dominant region is void, which fights the "one dominant panel" hierarchy rule.
- **Channel**: the Creator-DNA card wraps its copy every 3–4 words in a narrow column beside a
  mostly-empty card interior; one lone "Sports" trait chip amplifies the imbalance.
- **Insights**: opens on a ~350-word explainer card before any data; "VIDEOS ANALYSED 23" sits
  beside "INGESTED 2" with no visible definition of either, reading as a contradiction.
- **Videos**: two `UNTITLED` rows in monospace read as debug output on the primary library surface;
  the review-queue tile shows the logged badge-count defect (10 = all rendered clips).
- **Cross-cutting**: monospace is applied to prose meta ("based on 4 review notes", account name,
  minute balance) beyond `docs/UI.md`'s timecodes/IDs rule; five of eight screens are dominated by
  empty states with no forward affordance beyond a single button.
None of this is a correctness defect; together it is the difference between "designed" and
"assembled" on first contact, working against the Batch A/B investment.

**Acceptance criteria**
- [x] Review/Channel: no primary column whose majority is empty canvas at 1080p — content reflows,
      the column narrows, or the empty region earns its space (e.g. scoring detail moves up)
- [x] Insights: the explainer collapses to one line per panel (disclosure for the rest); paired
      stats that use different denominators are labeled so they cannot read as contradictory
- [x] Untitled videos get a derived label (filename or date), never `UNTITLED`
- [x] Monospace audit: mono for timecodes/IDs/counts only, per `docs/UI.md`
- [x] Empty states name the next action in context (what unlocks this panel, and where)
      *(measured against code, two of the three flagged panels — Videos analytics and
      Proof-of-lift — already named their next action; the genuinely mute one was the Profile
      library card's bare em-dashes, now explained. The Review mascot's "counters" turned out to
      be decorative floating binary — a static-screenshot misread, deliberately left alone)*

### Issue 413: Swap the app's text face — Geist Sans/Inter → Lexend
- [x] **Status:** DONE 2026-08-04 · **Batch:** — (user-driven follow-up to A/412) · **Size:** S

**What we're doing.** Replacing Geist Sans (app chrome/body) and Inter (display headings) with
**Lexend** for all non-mono text. Geist Mono stays for timecodes/IDs/counts.

**Why.** Direct creator feedback on 2026-08-04, after Batch A + Issue 412 shipped: the main text
still read "super blocky and ugly". Geist is a squarish technical grotesque; the complaint survived
the hierarchy and balance passes because it is about the letterforms themselves.

**Industry standard checked.** 2026 UI-typography surveys (Untitled UI "Best Free Fonts", DiverseKit,
Superfiles "Inter alternatives") name Inter/Figtree/DM Sans/Manrope/Hanken Grotesk as the safe slate.
Decision was made from an 11-face specimen artifact rendering the real Review card (actual OKLCH
tokens, actual copy, exact type scale) — six researched + five characterful candidates. The creator
picked **Lexend** ("soft and helps fill the screen"): a wide, low-contrast face built from
reading-proficiency research, whose width also works against the void-heavy layouts Issue 412 fought.

**Acceptance criteria**
- [x] `--font-ui`, `--font-display`, `--font-sans` all resolve to `'Lexend Variable'`; `--font-mono`
      unchanged (`index.css`)
- [x] `@fontsource-variable/lexend` self-hosted (GDPR posture unchanged); `geist` + `inter` packages
      removed from the dependency tree; bundle ships Lexend + Geist Mono only (verified in `dist/`)
- [x] Full unit suite green on node 22 (600/600), tsc + eslint clean (one pre-existing warning logged
      off-course), production build green
- [x] `docs/UI.md` typography section + `docs/DECISIONS.md` updated
- [x] e2e visual snapshots regenerated on the CI runner — all six baselines committed (`5c6861d`)
      and a plain CI dispatch (run 30960808300) is 12/12 green against them. Two process defects
      were found and fixed en route: the captures raced webfont loading (fixed: force-load both
      families + `document.fonts.ready` in `smoke.spec.ts`), and the regen dispatch used
      changed-mode `--update-snapshots`, which silently keeps any stale baseline inside the 1%
      diff tolerance (fixed: `--update-snapshots=all` in `ci.yml`) — a fallback-font
      `login-desktop.png` had survived two "regenerations" that way

---

## L26 follow-ups — filed from the first live A→B run + clip audit (2026-08-05)

> Evidence base: video `e290e6f4` (26:57 Commanders podcast, uploaded via the new Issue-395
> direct-to-R2 path in 58 s), 8 clips generated + rendered. Audit = all 8 windows checked
> against the diarized transcript + frame-extraction review of 3 rendered MP4s + loudness pass.
> Renders are clean (1080×1920 h264/aac, −16 dB mean, no clipping); findings below are the
> defects worth engine/render work. Clip ids abbreviated.

### Issue 427: Caption placement collides with the speaker's face

- [ ] **Status:** CODE-COMPLETE 2026-08-05 (427-430 wave) — only the live frame-extraction spot-check remains · **Size:** S · **BETA-visible**

The karaoke captions (one word at a time) render at ~50% frame height — in every sampled frame
of all 3 audited clips the word sits ON the host's face (over the sunglasses/nose). Standard
Shorts placement is lower-third, inside title-safe, BELOW the subject's chin.

**Acceptance**
- [x] Caption baseline moved to ~68–75% frame height (below typical talking-head chin line), configurable (`CAPTION_BASELINE_FRAC=0.70`; creator override `caption_position` top|middle|bottom via panel + brand kit)
- [x] Never overlaps the detected face box when face data exists (Haar box → pushdown, floored at the bottom-UI zone; per-frame-reframe box threading lands with Issue 422)
- [x] 3-word karaoke grouping (`CAPTION_WORDS_PER_GROUP=3`, gap-split 0.6 s; `1` = legacy byte-identical, pinned); style tokens kept (highlight gains the required reset tag)
- [ ] Frame-extraction spot-check on a re-rendered clip shows no face overlap

### Issue 428: Clips open mid-sentence — word-boundary snap instead of sentence-boundary snap (one meaning-INVERTING cut)

- [x] **Status:** DONE 2026-08-05 (427-430 wave) — sentence_snap.py + merge clamp + hook grounding; eval red→green verified · **Size:** M · **Clip-quality core**

5 of 8 clips open on a sentence fragment. Worst case `84b362b0` (1438.01): source audio is
"I don't **really think it's gonna happen** …" and the clip opens at "really think it's gonna
happen" — the negation is cut, INVERTING the speaker's meaning. Others: `85e8f48d` opens "when
they're cut, when rosters go out…" (fragment tail), `2893e613` opens "standing out." (tail of
prior sentence), `4b1269e5` opens "maybe the dig" (fragment), `311596f0` opens on a pronoun with
no referent ("he's not a free safety" — who?). Also `616ad186`'s suggested_hook text ("It's not
the receivers. It's not the corners") promises an open the audio doesn't deliver until ~8 s in.
LLM-origin `960d3931` additionally carries ~8 s of outro housekeeping pre-roll ("I'm gonna go
ahead and close this one out… oh, before we get out of here —") before its story AND runs 110 s
(over the 60–90 s target) — LLM moment validation should sentence-snap AND clamp length.

**Acceptance**
- [x] `setup_start_s` snaps to the nearest SENTENCE start (`clip_engine/sentence_snap.py`, 0.3 s lead-in floored at the previous sentence end)
- [x] Negation-preserving guard: a start is NEVER left mid-sentence (backward-preferred within 10 s; forward only for run-ons; backward regardless when forward is invalid)
- [x] LLM-proposed windows: sentence-snap both edges + hard 90 s clamp (`CLIP_TARGET_MAX_S`, sentence-aligned cut)
- [x] `suggested_hook` generated from the ACTUAL first ~5 s (`extract_transcript_opening` + `clip_opening_*` prompt envelope + prompt rule)
- [x] Eval scenario added: `mid_sentence_open.yaml` verified RED on the legacy path (setup stayed 76.0 mid-sentence) and green post-fix (71.2); `SCENARIO_FLOOR` 18→21

### Issue 429: Near-duplicate overlapping clips both rendered (cross-origin NMS gap)

- [x] **Status:** DONE 2026-08-05 (427-430 wave) — suppress_contained at the rank→trim seam · **Size:** S

`84b362b0` (1438.0–1481.2, signal, "Terry knows something") sits ENTIRELY inside LLM clip
`960d3931`'s window (1390–1500, "Terry & Diggs") — ~43 s of identical content rendered twice
(IoU ≈ 0.39, under the NMS threshold). The creator reviews 8 "clips" but only 7 stories.

**Acceptance**
- [x] Post-ranking containment pass: overlap coefficient (IoMin) ≥ 0.8 → DROP with refill from the pool (cross-origin, after scoring, before trim/persist — union untouched, preference reranker can't resurrect)
- [x] Eval fixture `contained_duplicate_suppressed.yaml` (the live 1390-1500 / 1438-1481 pair) asserts one of the pair survives

### Issue 430: Static crop slices the source video's own layout chrome — detect the camera region

- [ ] **Status:** CODE-COMPLETE 2026-08-05 (427-430 wave), flag OFF (`CAMERA_REGION_DETECT_ENABLED`) — staging frame check then flip · **Size:** M · interacts with Issue 422 (reframe staging)

The source was a produced podcast layout (show logo card top-right, guest name chip left,
"@WSHCARTER" social banner bottom). The full-height center crop keeps all of it: logo cut off
mid-word, name chip truncated at the frame edge, and the source's own social banner occupying
the bottom ~20% of every rendered Short. The face framing itself is good — the problem is the
crop treats the whole 16:9 frame as camera when much of it is chrome.

**Acceptance**
- [x] Detect the active camera region (`clip_engine/camera_region.py`, cv2 temporal variance) and crop/zoom INTO it before the 9:16 composition (region-space crop chain in `render_clip_file`)
- [x] Falls back to today's full-height crop when no chrome is detected (fail-open gates; byte-identical vf pinned by tests; skipped under the reframe flag — 422 seam documented)
- [ ] Frame-extraction check on a produced-layout source shows no truncated third-party chrome —
      **superseded by Issue 433**: with the speaker reframe live (2026-08-05), camera-region
      detection is skipped by design (full-frame sendcmd contract); the flag stays off until the
      region-aware reframe integration lands

### Issue 433: Region-aware reframe — compose camera-region crop with speaker cuts

- [x] **Status:** DEPLOYED 2026-08-05 (night, `433aaa6`) with `CAMERA_REGION_DETECT_ENABLED=true` in prod — live verification on the next produced-layout upload pending · **Size:** M · **Depends:** 422 (flag on), 430 (detector)

The Issue-430 camera-region pre-crop and the Issue-422 speaker reframe are mutually exclusive
today: `render_clip_file` skips chrome detection when `ACTIVE_SPEAKER_REFRAME_ENABLED` is on,
because the sendcmd x-commands and the persisted `reframe_track_jsonb` keyframes are
full-frame-coordinate contracts. With the reframe flag now live, produced-layout chrome (the
source's own SUBSCRIBE banner / top-band fragments) stays in every frame.

**Sketch**
- Run `detect_camera_region` first; pass the region into `compute_dynamic_crop` so face
  detection, `clamp_crop_x`, and the cut planner operate in region space.
- Label the ffmpeg 9:16 crop filter (`crop@spk`) and target sendcmd commands at it
  (`crop@spk x N`) so the region pre-crop filter is not also addressed; apply the region
  x-offset ONCE at sendcmd/track emission (one-geometry contract preserved).
- `GET /clips/{id}/crop-track` consumers (CropTrackOverlay) need the region in the payload or
  pre-offset keyframes — pick one and version the wire contract.

**Acceptance**
- [x] Produced-layout source renders with speaker cuts AND no third-party chrome — LIVE-VERIFIED 2026-08-05 on `b8505eb7` ranks 1+2 re-renders (with the 0.45 height floor): region "cropping into (169,326,1576,551)", SUBSCRIBE banner/socials/letterbox gone from every frame, cuts intact (keyframes=3, cuts=2), mapping confidence IMPROVED 0.46→0.51 (detection now runs on region-sliced frames)
- [x] Plain sources byte-identical when no region is detected (pinned: static chains keep the unlabeled crop spelling; region-None reframe chain differs only by the @spk label; `test_camera_region_none_is_byte_identical`)
- [x] Crop-track overlay still aligns in the frontend (`source` = pan-space rect by contract; additive `region` field; isCropTrack tolerance pinned)

### Issue 432: On-demand renders collapse under concurrency — dedicated render queue

- [ ] **Status:** open · **Size:** S · filed 2026-08-05 (live defect from the first post-wave upload)

The creator clicked "render" on ranks 9–12 in Review within ~10 s; four `render_clip` tasks ran
four concurrent ffmpeg 1080p encodes on the 4-core VM, every encode starved, and **all four hit
the render timeout (~266 s) together** → Celery retries, two clips stuck `failed`. The
auto-render batch (`render_video_clips`) is sequential by design; the on-demand path had no
concurrency guard, and a click DURING the batch would collide the same way.

**Fix (built with this filing):** route every ffmpeg-encoding task
(`render_clip` / `render_video_clips` / `clean_clip` / `edit_clip` / `render_summary`) to a
dedicated `render` queue via `task_routes` (`worker/celery_app.py`), consumed by a new
`render-worker` compose service with `--concurrency=1`; the main worker takes `-Q celery` only.
Dev compose consumes both queues in one worker. Note for the parked K8s track: the Helm chart
needs a matching render-worker Deployment when Issue 275 lands.

**Acceptance**
- [x] Render tasks route to the `render` queue (pinned: `tests/test_celery_routing.py`)
- [x] Prod compose: `render-worker` service `--concurrency=1 -Q render`; main worker `-Q celery`
- [x] Live: 4 queued renders completed strictly serially on the render-worker, none timed out (2026-08-05)
- [x] Failed rank-9/12 clips from video `6c221f12` reset and re-rendered — all 12 clips rendered

### Issue 431: "Generate more clips" — user-triggered regeneration excluding existing windows

- [x] **Status:** DONE 2026-08-05 (night) — live smoke rides the next fresh upload · **Size:** M · filed 2026-08-05 (user directive during the 427–430 wave)

Generation is deliberately one-shot idempotent (`POST /videos/{id}/clips/generate` short-circuits
when clips exist — Issue 61). With the wider pool (12 persisted / top-8 rendered, shipped in the
427–430 wave) the creator has more options per ingest, but no way to ask for MORE clips from the
same video after reviewing. A regenerate affordance closes the loop: like/dislike feedback trains
the preference model, then "more clips" surfaces fresh windows ranked by the updated model —
"this keeps ingestion to one and helps the DNA out when they like or don't like a particular clip."

**Sketch (research in Phase 1 — CHECK)**
- New endpoint (e.g. `POST /videos/{id}/clips/generate-more`): re-runs `score_and_rank` and
  excludes windows ≥N% contained in EXISTING clips via the Issue-429 `suppress_contained` pass
  (seed the kept-list with the persisted windows); appends new rows at ranks n+1…
- No minute charge (minutes were charged at ingest); LLM scoring call is the only new spend —
  gate behind the existing spend guard + a per-video regeneration cap.
- Review-screen button ("Generate more clips") shown once feedback exists or all clips reviewed.

**Shipped (2026-08-05):** `POST /videos/{id}/clips/generate-more` — same guard stack as
generate (flag + spend breaker + 10/hour + daily LLM limit + positive balance); 409 without an
engine baseline or at `CLIP_REGEN_TOTAL_CAP` (24); `score_and_rank(exclude_windows=…)` drops
candidates ≥80% contained (IoMin) in ANY persisted window (creator-made clips' NULL setup
coerces to `start_s`); `append_ranked_clips` continues ranks past the max engine rank, skips
the preference rerank (rank-collision hazard — DECISIONS), retries once on a
`uq_clips_video_rank` race. `CLIP_REGEN_BATCH_MAX=6` per call; fill-only metadata task
enqueued; appended clips never shortlisted/auto-rendered. Review UI: `GenerateMoreClipsButton`
in the toolbar + the all-reviewed terminal state (redirect held open while in flight, 2 s→8 s).

**Acceptance**
- [x] Regeneration never duplicates an existing clip window (containment vs persisted set — `tests/test_generate_more.py`)
- [x] Existing clips, feedback, and renders are untouched (append-only; no delete path exists in `append_ranked_clips`)
- [x] No minute deduction; LLM spend guarded + capped (guard stack + 6/call + 24/video)
- [x] Button in Review; honest "No new distinct moments" message when a pass finds nothing new
- [x] Live smoke on the next fresh upload: review → generate more → appended non-duplicate clips, metadata fills, no minute deduction — verified 2026-08-07 on video `3b6992fe` (Backboard Media). Ranks 13+14 created `12:21:41Z`, three minutes after the keep/drop; the original 12 were created `2026-08-05 23:35:02Z`. `minute_deductions` holds exactly one row for the video (27 min at ingest, `23:33:13Z`) — regeneration deducted nothing. Both appended clips carry generated titles. Neither appended window overlaps any existing clip (the overlaps found on this video are all inside the *original* batch — Issue 441). Caveat: rank 13 came back `render_status=done`, so an appended clip did get rendered; that is the manual "Render this clip" path, and it exposed Issue 438

### Issue 434: Review page is silent — muted autoplay with no unmute control

- [x] **Status:** DONE 2026-08-05 (night) — filed+fixed from the fresh-upload review (`b8505eb7`) · **Size:** S

The rendered MP4s carry healthy audio (aac, −16.2 dB mean loudnorm); Review is the only
surface passing `autoPlay` to VideoPlayer, and `muted={autoPlay}` was a static prop with no
volume control in the custom chrome (controls deliberately not exposed — Issue 386), so the
queue was permanently silent while the Editor had sound.

**Shipped:** `muted` is state seeded from `autoPlay` (browser autoplay policy still starts
muted — Issue 359d); an audio toggle in the player chrome (both densities; `Volume`/`VolumeX`
via the icon seam — digits rejected by the no-glyph allow-list, so not `Volume2`); the choice
persists per session (`sessionStorage` `cc-player-muted`) so advancing the Review queue
(player remounts per clip) keeps sound on.

**Acceptance**
- [x] Unmute toggle in the chrome; autoplay still starts muted (`video-player.test.tsx`)
- [x] Choice survives the per-clip remount (sessionStorage pin)
- [x] Non-autoplay surfaces unaffected (start unmuted, ignore the stored choice)
- [x] Live: sound audible in Review after one click on the next upload — creator-confirmed 2026-08-07 ("speaker icon allows me to hear the video now"). The delivered files were never the problem: all 9 rendered clips of `3b6992fe` ffprobe as `aac` stereo and measure −13.9 to −14.0 LUFS integrated, dead on the `I=-14` render target

### Issue 435: Video titles — filename seed + inline rename ("Untitled" dead end)

- [x] **Status:** DONE 2026-08-05 (night) — filed+fixed from the fresh-upload review · **Size:** M

`Video.title` was never written anywhere (upload endpoints ignored the filename they already
receive) and no update endpoint existed — every upload showed "Untitled · date" forever.

**Shipped:** uploads seed `title` from the filename stem (proxy path from `file.filename`;
the stateless multipart flow sends `filename` with the complete call — `uploader.ts`);
`PATCH /videos/{video_id}` with the `ClipMetadataPatch` tri-state idiom (omitted = untouched,
null/blank = clear, string = set, 200-char clamp, `get_owned` isolation); Dashboard title cell
is now `VideoTitleCell` — inline Rename → input (Enter/Escape) → PATCH → `['videos']`
invalidation, so the review/editor pickers pick the rename up for free.

**Acceptance**
- [x] Upload seeds title from filename (`test_videos_multipart_upload.py` happy path)
- [x] Tri-state PATCH + isolation (`tests/test_video_title.py`: set/clear/untouched/404)
- [x] Inline rename on the Dashboard (`VideoTitleCell.test.tsx`)
- [x] Live: rename the fresh upload on the Dashboard and see it in the Review picker — creator-confirmed 2026-08-07. Video `3b6992fe` carries the title "Video 2 Test"; the pre-435 upload `b8505eb7` still has `title IS NULL` (the "Untitled" dead end this issue closed)

### Issue 436: Jittery AI framing — virtual-tripod hold (stop chasing the raw face center)

- [x] **Status:** DONE 2026-08-05 (night) — filed+fixed from the fresh-upload review · **Size:** M

Measured on the live crop tracks: speaker_cut clips made ~4 crop micro-moves/second (median
2–3 px, 67 direction flips in 67 s) because the crop followed the per-sample face center
WITHIN a turn; face_pan see-sawed in 25–60 px steps. The only smoothing was EMA α=0.2 + a
300 px/s clamp — a low-pass, not a hold — and sendcmd emitted one x per 5 Hz sample
(staircase), while the frontend preview lerps (preview looked better than the render).

**Shipped:** piecewise-constant crop paths ("pick a spot and hold it").
- `speaker_cut`: ONE hold per segment = the owning track's windowed **median** position
  (`FaceTrack.median_cx`; `cx_at` deleted); position changes only at cuts. Zero-cut clips
  emit an empty sendcmd → render.py's static branch (a true locked-off crop).
- `face_pan`: `plan_pan_holds` — hold the opening median; retarget ONLY when every real
  detection in the last `REFRAME_PAN_RETARGET_S` (1.0 s) sits beyond
  `REFRAME_PAN_DEADBAND_FRAC` (0.15 × crop width ≈ 91 px) from the hold, then ONE linear
  glide at `REFRAME_PAN_GLIDE_PX_PER_S` (600) densified at `REFRAME_GLIDE_SAMPLE_FPS` (30);
  fallback samples never vote; glides never cross a shot cut.
- EMA machinery deleted (`smooth_crop_track`, `_EMA_ALPHA`, pan clamp) — holds have nothing
  to smooth, and the glide has its own speed knob.
- Wire contract stays **v1** (keyframes are now sparse breakpoints; lerp/snap trivially
  exact; JSON shrinks ~10–100×); `render.py` untouched.

**Acceptance**
- [x] Wobbling detections within a turn → constant x; sendcmd lines == segments, only delta = the cut jump (`test_reframe_planner.py::TestHoldPointsFromSegments`)
- [x] Deadband oscillation/transient spike/detection loss never move the tripod; sustained move → exactly one monotonic glide (`TestPlanPanHolds`)
- [x] Median outlier robustness pinned (speaker_map + planner tests)
- [x] `test_render.py` and punch-in pins pass unmodified (consumer contract intact)
- [x] Live: re-rendered ranks 1 (speaker_cut) + 2 (face_pan) of `b8505eb7` 2026-08-05 — crop track collapsed 336→3 keyframes (cuts+1 exactly) and 172→47 (holds + glide ramps); by construction the only x-changes left in rank 1 are the two cuts. Frames verified: steady, tight, chrome-free

### Issue 437: Keep/Drop fails silently — a lost rating is painted as a confirmation

- [x] **Status:** DONE 2026-08-05 — filed+fixed from a live 502 on autoclip.studio · **Size:** S

During a brief 502 (Cloudflare edge up, origin unreachable) Keep/Drop "didn't work" with no
usable signal. The lost write is expected — `POST /clips/{clip_id}/feedback` is a live write
with no local buffer, so an unreachable origin means no `ClipFeedback` row. **Presenting that
loss as a success was the defect**, and it destroyed the creator's input on the way out:

- `YourCall.tsx:124` hardcoded the status line to `text-success`, so `'Error — try again'`
  rendered in the **success green**, 12px mono, in the card header.
- `submitTagged` called `setPanel(null)` *before* awaiting the POST, so the tag panel closed
  exactly as it does on success; `onAdvance` fires only on success, so the clip didn't change.
  Panel closed + same clip + green text = "the button did nothing."
- The selected tags and note were discarded (`openPanel` resets them) — a retry started blank.
- No in-flight guard at all, so the write could also be fired twice.

`StyleReview.tsx` — same directory, same feature — already did all of this correctly
(`:87-89` separate `errorMessage`, `:182,186` `disabled={isPending}` + `'Saving…'`).
`YourCall` was the outlier; the fix brings it to the sibling's standard.

**Shipped:** `flash` becomes the repo's `{ text, tone: 'muted' | 'success' | 'danger' }` idiom
(five profile sections already use it), rendered through `FLASH_TONE_CLASS`. `sendFeedback`
returns a boolean and owns a `submitting` flag cleared in `finally`; `submitTagged` closes the
panel **only** on success, so tags + note survive untouched. A ≥500 `ApiError` or a network
drop reads *"Couldn't reach the server — nothing was saved. Try again."* (a <500 `ApiError`
still shows the server's own message, matching `applyTrimRender`); the error tone does **not**
auto-clear. Submit/Skip/Save-trim are `disabled` while in flight, Submit reading `'Saving…'`.
The status span is now an always-rendered `role="status" aria-live="polite"` live region. Also
fixed the wire-enum leak — `"upvote recorded"` → `"Kept"`/`"Dropped"`/`"Skipped"`.

Rule written into `docs/UI.md` → *Status messaging*, because **no gate can catch this class**:
`design-tokens.contract.test.ts` only flags *undeclared* token names, and both `text-success`
and `text-danger` are declared, so a semantic swap compiles and ships. The regression tests are
the only guard.

**Deliberately not done:** automatic retry / offline outbox. `POST /clips/{id}/feedback` is not
idempotent — it inserts a row and retriggers the preference-model retrain (`routers/review.py:227-229`)
— so retrying a timed-out-but-applied write double-counts the rating. Preserving the choice so
*one* click re-submits is the correct fix at this size; a real outbox needs a server-side
idempotency key. The 502's own root cause on the VM was not investigated.

**Acceptance**
- [x] Failed Keep keeps the panel open, keeps the tag selected, renders `text-danger` (not `text-success`), says nothing was saved, and does not advance (`YourCall.test.tsx`)
- [x] Successful Keep closes the panel, advances once, exactly one POST
- [x] Submit locked while in flight — double-click writes one row
- [x] Status line is a `role="status" aria-live="polite"` region rendered before its first message
- [x] Full frontend lane green on the CI-pinned Node 22: 649/649
- [ ] Live: block `**/clips/*/feedback` in devtools on the next Review pass and confirm the red persistent message + preserved tags
- [x] Live (success path only): the 2026-08-07 Review session wrote exactly two `clip_feedback` rows — `upvote` on rank 1 at `12:18:17Z`, `downvote` on rank 2 at `12:18:26Z`, one row each (no double-count), both `feedback_note` values persisted verbatim, and `preference_models` advanced to v3. The *failure* path above is still owed

---

## Wave 11 · Lane L26 · Batch D — post-render quality audit (2026-08-07)

> Filed from the first full-set audit of a real creator upload (video `3b6992fe`, Backboard
> Media, 14 clips / 9 rendered). Method and evidence: `scripts/clip_audit.py` (read-only prod
> manifest + local ffprobe/loudness/contact-sheet pass). Every finding below was confirmed on
> delivered media, not inferred from code. What was **verified good**: all 9 renders are
> 1080×1920 h264/aac, duration exact to ±0.01 s against `end_s - COALESCE(setup_start_s, start_s)`,
> integrated loudness −13.9 to −14.0 LUFS against the `I=-14` target, and 5 of 9 clips
> (ranks 1/3/4/5/8) are tight, stable, chrome-free and correctly captioned.

### Issue 438: Clips render captionless when `style_preset` is NULL — kit style is seeded onto the auto-render top-N only

**Severity: high — silent, visible, already shipped to a creator.**

Rank 13 of `3b6992fe` has **zero burned-in captions** across its full 89 s. Every other rendered
clip on the same video has them. `style_preset` is populated on ranks 1–8 and `NULL` on ranks
9–14; rank 13 is the only NULL clip that has been rendered so far.

Two gaps compose into it:
- `worker/tasks.py:3547-3552` seeds the brand-kit style only onto the slice it is about to
  auto-render (`ordered = sorted(clips, …)[:top_n]`, `AUTO_RENDER_TOP_N=8`). Clips outside the
  top-N are never given a preset.
- `routers/clips.py:871` backfills the kit style **only `if body is not None`**. A bodiless
  `POST /clips/{id}/render` — the plain "Render this clip" button and any retry — skips the
  merge entirely and leaves `style_preset = None`.

`clip_engine/render.py:676` (`if style_preset:`) then drops the whole caption branch, and
`:715-717` treats a `None` from `build_ass_subtitles` as normal, so the `subtitles=` filter is
omitted with no error, no warning, and a `done` render status. Ranks 9–12 and 14 are all primed
to do exactly the same on first render.

**Fix direction:** seed the kit style for every persisted clip (not just the render slice), and
have the render endpoint resolve the kit whether or not a body is present. Consider making a
requested-but-unbuildable caption track a loud failure rather than a silent omission.

**Status: DONE (2026-08-07)** — backend 2886/0, Layer 0 all green (coverage 84.35).

**What changed**
- `worker/tasks.py` — style seeding is now its own block over **every** persisted clip, hoisted
  out of both the `[:top_n]` slice and the `AUTO_RENDER_CLIPS` guard. `AUTO_RENDER_TOP_N` still
  caps rendering; it no longer caps styling.
- `routers/clips.py` — the brand-kit resolve/merge no longer sits inside `if body is not None`.
  It runs unconditionally, and the body's overrides collapse from seven `if … is not None`
  branches to `merged.update(body.model_dump(exclude_none=True))` — every `RenderStyleIn` field
  is optional with `None` meaning "keep existing", so `exclude_none` is exactly the override set.
  This also repairs every already-NULL row without a migration or backfill.
- `clip_engine/render.py` — two `logger.warning` calls on the silent-drop paths: a requested
  caption track that produced no file, and a `subtitle` key outside `VALID_STYLES` (which is the
  same set as `_ANIMATED_CAPTION_STYLES`, so an unknown key fell straight through). The render
  still succeeds — a captionless clip beats no clip — but it is no longer invisible.

**Acceptance**
- [x] A clip persisted outside the auto-render top-N, rendered with a **bodiless** `POST /clips/{id}/render`, resolves the brand kit (`test_brand_kit_render_applies_kit_with_no_request_body` — demonstrated failing first: `assert None == 'bold_pop'`)
- [x] `style_preset` is non-NULL for every clip a generation pass persists, including clips beyond `AUTO_RENDER_TOP_N` and when `AUTO_RENDER_CLIPS` is off (two tests in `test_progress_emit_wiring.py`, both demonstrated failing first)
- [x] A requested caption track that resolves to nothing logs a warning (`test_requested_captions_that_resolve_to_nothing_are_logged`, demonstrated failing first)
- [ ] Surface the captionless state in the clip's render metadata, not only the worker log — **not done**, deliberately deferred: it needs a field on the clip row and a UI affordance, which is its own issue. The warning is the interim signal
- [x] Live (seeding half): **VERIFIED 2026-08-10 on video `7e988321`** — `style_preset` is non-NULL on **all 12** persisted clips, including ranks 9–12 which sit beyond `AUTO_RENDER_TOP_N = 8`. On the baseline video, from the same source recording, ranks 9–14 were all NULL and rank 13 shipped captionless. Burned-in captions confirmed visually on all 8 rendered clips' contact sheets
- [x] Live (bodiless-render half): **VERIFIED 2026-08-10 23:45 UTC on `7e988321` rank 13.** The creator rendered an appended clip at rank 13 — beyond `AUTO_RENDER_TOP_N = 8` — and it came back `render_status = done` with `style_preset = {subtitle: bold_pop, captions_enabled: true, zoom_on_peak: false, denoise: false}` resolved from the brand kit, and **burned-in captions on all 12 sampled frames**. This is the decisive comparison: on the 2026-08-07 baseline, **rank 13 of this same source recording shipped with zero captions across its full 89 s** — that clip is what the issue was filed from. Delivery normal (1080×1920 h264/aac, 89.80 s exact, −14.0 LUFS, true peak −5.4 dBFS). **Issue 438 is fully closed.**

### Issue 439: Camera-region detection unions animated overlays into the region — a whole clip shipped with the SUBSCRIBE/socials overlay burned in

**Severity: high — the exact defect Issue 433 was built to prevent, recurring on a sibling clip.**

Rank 6 of `3b6992fe` carries the source's **SUBSCRIBE button, the `@WSHCARTER` socials strip
(TikTok/X/Instagram/YouTube icons), and a live superchat overlay** across roughly the bottom
third of the 9:16 frame for all 84 seconds. Captions are drawn on top of that band, so in places
both the caption and the donation text are unreadable.

The cause is region disagreement between clips of the *same source*. Detected rects:

| rank | region (x,y,w,h) | height frac |
|---|---|---|
| 1 | 169,330,1728,547 | 0.507 |
| 4 | 301,338,1408,539 | 0.499 |
| 5 | 185,330,1524,547 | 0.507 |
| **6** | **0,330,1918,749** | **0.694** |
| 8 | 185,326,1676,551 | 0.510 |
| 13 | 137,326,1772,551 | 0.510 |

Rank 6's band extends ~200 px lower than every sibling and spans the full frame width, sweeping
the overlay strip into the crop. Rank 7 shows a milder version — a residual `NSON91` name-tag
overlay left in the bottom-left corner.

**Root cause (corrected 2026-08-07 after reading the detector).** An earlier draft of this issue
said detection "runs once per clip on the midpoint keyframe". That is wrong, and the real
mechanism matters for the fix. `detect_camera_region` samples **10 frames spread across the clip
window** and takes a per-pixel temporal standard deviation
(`clip_engine/camera_region.py:166-213`); the midpoint keyframe feeds only the Haar face box
(`clip_engine/render.py:535-547`). The defect is at `camera_region.py:92-102`: **every motion
contour whose area is ≥20% of the largest blob is unioned into the rect**, a rule written so a
side-by-side two-camera layout yields one region. An animated SUBSCRIBE button, a socials strip
or a superchat popup is its own motion contour, so it is absorbed rather than excluded — pulling
`y+h` down ~200 px and `x`/`w` out to the full frame.

Three gates should have caught it and structurally cannot:
- `CAMERA_REGION_MIN_HEIGHT_FRAC=0.45` is a **floor with no matching ceiling**
  (`camera_region.py:128-134`), so 0.694 passes unchallenged.
- `CAMERA_REGION_FULL_FRAME_FRAC=0.92` passes too — the rect is 0.693 of the frame.
- The face gate (`camera_region.py:141-152`) is a **containment** test, so *widening* a region
  can never trip it; it only fires when the region is in the wrong place.

There is also no consistency check of any kind between clips of the same source: detection is
called from exactly one site (`render.py:562`), uncached, so N clips mean N independent answers.

**Fix direction (staged).** Stage 1 — stop unioning overlay strips: admit a secondary contour
only when it overlaps the primary blob's **vertical** span (a second camera sits *beside* the
largest blob; a banner sits *below* it), and add a `CAMERA_REGION_MAX_HEIGHT_FRAC` ceiling to
match the existing floor. Stage 2 — resolve the region **once per video** during ingest, where
the source is already on disk, store it, and have the render path prefer it, so clips of one
source can no longer disagree.

**Status: DONE (2026-08-07)** — backend 2904/0, Layer 0 all green. Coverage 84.29 → 83.98:
the backfill task is an ffmpeg/R2 I/O shell, covered structurally by
`tests/test_camera_region_ingest_safety.py` in the same idiom as
`tests/test_poster_ingest_safety.py`. Module floors unmoved (`clip_engine` 92.28 vs 91.0).

**What changed — Stage 1 (detector)**
- `clip_engine/camera_region.py` — the region is anchored on the largest blob's bounding box, and
  a secondary blob is unioned in only if it clears the (now-named) `_SECONDARY_AREA_FRAC` **and**
  shares ≥`_MIN_VERTICAL_OVERLAP_FRAC` (0.5) of its vertical span with the primary. Excluded
  blobs are logged with the reason.

**What changed — Stage 2 (video-level consensus)**
- Migration `0056` + `Video.camera_region_jsonb` — the rect resolved once per video, stored with
  the source dimensions it was measured against.
- `detect_video_camera_region` originally sampled 24 frames across the whole runtime, on the
  premise that sampling wide dilutes an intermittent overlay. **That premise was wrong and is the
  subject of Issue 443** — a wide span saturates the motion mask instead of diluting it. Since
  2026-08-10 it runs the per-clip detector over 9 disjoint 60-second windows (10 frames each) and
  takes a majority-agreed component-wise median.
- Resolved during ingest inside the existing `alocal_path` block (the source is already local —
  a separate task would pay a second full download), with the poster's never-fails-ingest
  posture: `ingest_video` is a `RefundOnFailureTask`, so a propagating error would retry the
  ingest and then refund minutes for a transcription that succeeded.
- `render_clip_file` prefers the stored rect and falls back to per-clip detection when it is
  absent, when the source dimensions no longer match, or when **this clip's** face box sits
  outside it — the video-level rect carries no face-sanity check of its own.
- `backfill_video_camera_regions` Beat task, hourly, batch 5 (vs the poster sweep's 25 — this
  pass decodes up to 9 sixty-second windows per video rather than one frame; batch was 10 until
  Issue 443 rebuilt Stage 2 as a consensus).

**Acceptance**
- [x] An animated overlay strip below the camera band is excluded, not unioned in (`test_animated_overlay_strip_below_the_camera_is_excluded`, demonstrated failing first at bottom edge 1079 vs the camera band's 800)
- [x] The side-by-side two-camera union the rule exists for still works (`test_side_by_side_second_camera_is_still_unioned`, green before and after)
- [x] Region is resolved per video, stored, and preferred by the render path; per-clip detection remains the fallback for older videos (`test_video_level_region_is_preferred_over_per_clip_detection`, `test_absent_video_region_still_detects_per_clip`) — the wiring was always right; the measurement it fed was wrong and was **rebuilt as a per-window consensus in Issue 443** (2026-08-10)
- [x] A clip whose speaker falls outside the stored rect re-detects rather than cropping them away (`test_video_level_region_falls_back_when_the_face_is_outside_it`)
- [x] A rect measured against different source dimensions is distrusted (`test_video_region_rejected_when_source_dimensions_changed`)
- [x] Every clip of one source unpacks an identical rect (`test_video_region_is_shared_by_every_clip_of_one_source`)
- [x] Ingest and backfill safety contracts pinned (`tests/test_camera_region_ingest_safety.py`)
- [~] **A height ceiling was NOT added** — see `docs/DECISIONS.md` 2026-08-07. Building it showed the instrument cannot work: `test_detects_inner_camera_region` asserts a legitimate 0.648–0.815 height fraction and the defective region was 0.694, inside that band. Any ceiling catching the real failure would reject correct regions
- [x] Live: **VERIFIED 2026-08-10 on video `7e988321`** for the static chrome it was filed against. `videos.camera_region_jsonb` = `(169, 326, 1704, 551)` on a 1920×1080 frame — height fraction **0.5102**, against the ~0.51 target and NOT the 0.694 defect. Provenance present and healthy: `version 2`, `windows 9`, `windows_detected 9`, `windows_agreeing 8`, `window_span_s 60.0`, `sample_frames 10` (so Issue 443's consensus gate passed rather than declining). A source frame with the rect overlaid shows it excluding the SUBSCRIBE button, the `@WSHCARTER` socials strip, the WSH Carter logo and the top player graphic. All 8 rendered clips carry `chrome_removed = true`; a 2 Hz scan of the bottom 200 px found **no static chrome in any of them**. ⚠️ A *transient* superchat overlay drawn INSIDE this correct region still reaches the render — filed as **Issue 448**, a different mechanism, not a regression of this one

### Issue 440: `face_pan` fallback degenerates into repeated full-width sweeps — the virtual tripod only holds in `speaker_cut` mode

**Severity: high — this is the clip the creator dropped.**

Issue 436 delivered the tripod for `speaker_cut` (ranks 1/4/5/8 hold at 2–3 keyframes; x changes
only at cuts). It does **not** hold in the `face_pan` fallback. On `3b6992fe`:

| rank | mode | keyframes | monotonic runs | run size | verdict |
|---|---|---|---|---|---|
| 3 | face_pan | 47 | ~1 | ±897 px | acceptable — one transition |
| 2 | face_pan | 99 | 2 | ±890 px | **a sampled frame at t≈22.7 s sits on empty background — no person in shot** |
| 7 | face_pan | 343 | **7** | ±900 px | 5 of 8 sampled frames are mid-pan; subject repeatedly half-out of frame |

Each run traverses essentially the entire pan range (rank 7 `x_range` = [199, 1141]), so the
framing whips across the full width of the camera region and back, seven times in 42.6 s.

The three `face_pan` clips are exactly the three below `REFRAME_MIN_MAPPING_CONFIDENCE=0.2`
(0.031, 0.163, 0.189) — low confidence forces the fallback, and the fallback has no sweep budget.
The deadband is the mechanism: `REFRAME_PAN_DEADBAND_FRAC=0.15` is a fraction of **crop width**
(0.15 × ~309 px ≈ 46 px), while the two speakers sit ~900 px apart in region space. Any
attention change sustained for `REFRAME_PAN_RETARGET_S=1.0` clears a 46 px deadband by a factor
of 20 and commits to a full-width glide. The deadband is scaled to the wrong space.

**Fix direction:** scale the deadband (or a separate sweep gate) to the **pan space** rather than
the crop width; and/or budget sweeps per unit time; and/or — when mapping confidence is low on a
wide multi-person shot — hold a static two-shot instead of panning, which is what the creator
asked for in their own words on 2026-08-05 ("I love the cropping being still").

**Status: DONE (2026-08-07)** — backend 2905/0, Layer 0 all green, eval harness 59/59 unchanged.
Rulings in `docs/DECISIONS.md` 2026-08-07.

**What changed** (`clip_engine/reframe.py`)
- New `_seat_hold_plan`: seats come from the `FaceTrack`s already built for the speaker-cut rung
  (no new clustering); seats closer together than the crop width collapse into one framing. On a
  genuine two-shot the framing **holds the dominant seat per shot** and moves only at a source
  shot boundary.
- **Concurrency is the discriminator.** Seats qualify as a two-shot only when occupied
  *simultaneously*. Two well-separated tracks that never co-occur are one subject who relocated —
  a real camera move that stays with the pan planner. Getting this wrong is what broke
  `test_sustained_move_earns_glide_sendcmd_lines` in the first attempt.
- The surviving pan path's deadband now scales to the **pan space** (`frame_width - crop_w`),
  not the crop width — the 20× mismatch that let any attention change commit to a full-width glide.

**Deliberately not done:** cutting to whoever is speaking on this rung. It was built and rejected
by its own test — the framing still flipped five times in ten seconds, because this rung is
reached precisely when speaker mapping is untrustworthy and the only signal left (largest face)
flips as speakers lean. See DECISIONS. No traversal-budget constant was added either: with the
deadband corrected and multi-seat layouts no longer panning, nothing is left that needs it.

**Acceptance**
- [x] A wide two-shot on the `face_pan` rung produces a bounded-motion track — ≤1 direction flip and ≤8 keyframes over 10 s (`test_two_shot_face_pan_holds_seats_instead_of_sweeping`, demonstrated failing first at **93 keyframes**)
- [x] The crop never comes to rest between seats, on empty background (same test asserts every keyframe centre lands within 25% of the pan space of a seat)
- [x] Low mapping confidence on a wide shot prefers a hold over a full-width glide
- [x] A single subject who genuinely relocates still earns one monotonic glide — the two pre-existing tests that pinned this (`test_sustained_move_earns_glide_sendcmd_lines`, `test_x_in_script_is_within_frame`) pass unchanged rather than being rewritten
- [x] The `speaker_cut` rung is untouched (`test_speaker_cut_mode_end_to_end` still 2 keyframes for a 1-cut clip)
- [x] Live (motion criteria): **VERIFIED 2026-08-10 on video `7e988321`** — the same source recording as the baseline (`duration_s = 1617.216667` on both), so this is a true A/B. The one `face_pan` clip (rank 1) shows **1 keyframe, 0 direction flips, 0.0 visible moves/s** over 34.6 s — a complete hold. The baseline's failures on this identical source were rank 7 at **343 keyframes / 7 runs of ±900 px / 7.6 moves/s** and rank 2 resting on empty background. Across all 8 rendered clips: 1–4 keyframes, ≤2 direction flips, 0.023–0.047 moves/s, and the cut sheets (±0.2 s around every cut) show instant speaker-to-speaker switches with both subjects centred — no frame rests between seats
- [ ] ⚠️ **The motion criteria passed but the clip is still wrong — see Issue 450.** The creator dropped rank 1 the same day with the note *"When Rio is talking (the guy on the right), it is on the man on the left (who is not talking)."* Verified: the crop sits at `x = 230` of a 1704-wide region (the LEFT seat) for the whole clip, while source frames at t = 758 / 768 / 780 show the RIGHT seat mid-sentence and the left seat silent; diarization attributes all 31.0 s of speech in the window to one speaker. **This audit graded 440 green on the numbers alone and missed it** — "few keyframes, ≤1 flip, no empty background" are all satisfied by a shot of the wrong person. The stated tradeoff at `reframe.py:884-905` (*"a still frame on the wrong person is far cheaper than a cut to the wrong person, and stillness is what the creator asked for"*) has now been falsified by the creator it was made for

### Issue 441: Primary clip generation emits overlapping windows and mid-sentence cold opens

**Severity: medium-high — editorial quality; the eval harness is green while live output fails.**

Five overlapping pairs on one video, all inside the **original** 12-clip batch (regeneration is
clean — Issue 431's dedup works):

| pair | overlap | share |
|---|---|---|
| rank 7 [1257-1300] × rank 10 [1208-1275] | 17.4 s | 41% of rank 7 |
| rank 2 [788-821] × rank 6 [812-897] | 9.1 s | 28% of rank 2 |
| rank 1 [1140-1177] × rank 4 [1173-1212] | 4.1 s | 11% of rank 1 |
| rank 4 × rank 10 | 3.3 s | 8% |
| rank 2 × rank 3 | 0.8 s | 3% |

The duplication is verbatim: rank 2's closing line ("I'm starting to feel like I wanna see Rahsao
Douglas and Trey Amos starting on the outside…") **is** rank 6's opening line.

Adjacent clips also split single sentences across the boundary — rank 3 ends "…I worry about this
room some **because**" and rank 2 opens "**because** they still don't know what Mikey is." Cold
opens on subordinate clauses or dangling referents affect 5 of 9 rendered clips: rank 2
("because…"), rank 5 ("the Terry thing, no."), rank 8 ("when they're cut, when rosters go out."),
rank 13 ("yeah, they've been awesome so far." — "they" has no antecedent), and rank 1 opens on
"Like, football speed, easily to me", where the subject Nick Cross is named only in the lead-in
the clip discards.

**Why the eval did not catch this.** Issues 428/429 added `mid-sentence-open` and
`contained-duplicate` fixtures and both pass at `SCENARIO_FLOOR=21`. The containment pass only
rejects windows that are *fully contained* in another, so a 41% partial overlap survives; and
`sentence_snap` evidently does not treat a conjunction-initial or answer-fragment opening as
mid-sentence. The fixtures encode narrower failures than the ones production produces.

**Fix direction:** add a partial-overlap rejection (or merge) pass alongside containment; extend
the setup-start check to reject windows opening on a subordinating conjunction, a bare discourse
marker ("yeah", "so"), or a pronoun whose referent lies outside the window. Widen the eval
fixtures to the live failures before changing the ranker.

**Status: DONE (2026-08-07)** — backend 2915/0, Layer 0 all green, eval 24 scenarios / 100%.
Rulings in `docs/DECISIONS.md` 2026-08-07.

**What changed**
- `clip_engine/ranking.py` — `_MAX_OVERLAP_S = 3.0` as a **second, independent predicate** inside
  the existing `suppress_contained` loop (and the `exclude_windows` filter). `_CONTAINMENT_THRESHOLD`
  is untouched: it is pinned, and deliberately above the 0.67 IoMin ceiling, so no ratio could
  catch the live pairs (IoMin 0.419 and 0.27) without dropping legitimate ones.
- `clip_engine/sentence_snap.py` — `build_sentence_index` carries each span's first token (it
  discarded all text, which is why no lexical check was possible); `snap_start` walks back off an
  opener that cannot stand alone, landing on the previous sentence's **start** so the main clause
  comes with it, bounded by 3 steps and the existing `max_snap_s`.
- Eval — `max_pairwise_overlap_s` and `opens_on_content_word` assertions, fixtures
  `partial_overlap_suppressed` and `dependent_clause_open`, `SCENARIO_FLOOR` 21 → 23, landing-page
  count 22 → 24.

**Scope cut back after evidence:** the opener list ships as subordinating conjunctions +
discourse markers only. Including coordinators and pronouns broke two pinned snap cases and
collapsed two candidates onto one opening, and **none of the live failures were coordinator- or
pronoun-initial**. Rank 5's "the Terry thing, no." is knowingly not covered — it is a fragment,
not a closed grammatical class.

**A pinned test was deliberately changed:** `test_partial_overlap_admitted_by_nms_is_kept`
asserted two 60 s windows sharing **40 s** must both survive. That is the defect, not a keep. It
is replaced by a pair covering both sides of the new boundary.

**Acceptance**
- [x] Two clips may not share more than 3 s of speech; the lower-ranked one is dropped and ranks renumber densely (`test_partial_overlap_over_the_seconds_budget_is_dropped`)
- [x] A partial overlap *under* the budget still survives — the NMS union is not re-litigated (`test_partial_overlap_under_the_seconds_budget_is_kept`)
- [x] A clip window may not open on a subordinating conjunction or a discourse marker (`test_snap_start_walks_back_off_a_subordinating_conjunction`, `..._off_a_discourse_marker`)
- [x] A coordinator-initial opening is left alone (`test_snap_start_leaves_a_coordinator_alone`), and a start in an inter-sentence pause stays a clean open (`test_snap_start_clean_boundary_unchanged`, pre-existing)
- [x] New eval fixtures reproduce the rank2×rank6 partial overlap and the "because…" cold open — **both verified failing with the fixes neutralised**, then green
- [x] `SCENARIO_FLOOR` 21 → 23; landing-page public count synced 22 → 24
- [x] Live (overlap half): **VERIFIED 2026-08-10 on video `7e988321`** — **maximum pairwise overlap across all 12 clips is 0.00 s**, against the 3.0 s budget. No verbatim duplicated speech between any pair. The baseline, from the same source recording, had five overlapping pairs (largest 17.4 s = 41% of rank 7) and verbatim duplication between ranks 2 and 6
- [ ] Live (cold-open half): **FAILED — see Issue 449.** Rank 4 opens audibly on **"Yeah."**, which `is_weak_opener` classifies `True`. `snap_start` never reached its walk-back: the start `1306.43` sat in the inter-sentence pause `[1306.27, 1306.51]`, 0.08 s before the sentence and outside `_BOUNDARY_EPSILON_S = 0.05`, so the pause branch returned it untouched. Not a budget limit — the walk-back would have cost 4.56 s against a 10.0 s budget. Ranks 6 (`"Like,"`) and 12 (`"maybe"`) open on hedges outside the shipped list, and rank 7 (`"the Terry thing, no."`) is the fragment 441 knowingly scoped out

### Issue 442: `style_preset["background"]` is accepted, persisted, and never applied

**Severity: medium — a creator-visible setting that silently does nothing.**

Found while fixing Issue 438. `background` ("blur" | "black") is accepted by `RenderStyleIn`,
merged into the clip's preset, stored on the brand kit (`CreatorStyle.style`) and round-tripped by
`GET/PUT /creators/me/brand-kit` — but the render never reads it. The `_BACKGROUND_STYLES` table
holding a boxblur graph was dead code, referenced nowhere; it has been deleted and the render
docstring now says plainly that the key is not applied, rather than leaving a promise in the code.

The reason it does nothing is structural, not a missing branch: the filter chain is crop→scale,
i.e. **full-bleed at every supported aspect** (`OUTPUT_PRESETS` picks the canvas, the crop keeps
full region height). There is no letterbox to fill and nothing to composite behind, so a
background only becomes meaningful alongside a "fit whole frame" export mode.

Not fixed inline because both honest options are breaking changes beyond a cleanup: rejecting the
key at the endpoint would 422 the existing brand-kit UI, and implementing it means a new
letterbox/contain render mode.

**Status: DONE 2026-08-13 (owner ruled REMOVE; W4 lane, branch `fix/issue-442-remove-background`).** `background` deleted end-to-end: API input models, brand-kit read/write/merge, `style_learn._KIT_FIELDS` (red proof: the suggester was actively proposing the dead knob from historical dicts and its accept endpoint would have written the orphan key back), both frontend panels, types, fixtures, and originality's style signature (integration). No migration — stored JSONB keys silent-ignore, verified by a legacy-row round-trip test. Render POSTs carrying the key now 422 deliberately (SPA+API deploy together). Successor: Issue 483 (contain/fit export mode, demand-gated).

**Acceptance**
- [x] Decide: build a contain/letterbox mode where a background is meaningful, or remove `background` end to end (API field, `CreatorStyle` payload, brand-kit UI, docs)
- [x] Whichever way, no code path accepts a style key it does not honor
- [x] A creator setting a background either sees it in the render or cannot set it at all

### Issue 443: video-level camera region measures the wrong thing — rebuild as a per-window consensus

**Severity: high — Issue 439 Stage 2 is shipped, DISABLED in data, and produces a defective rect.**

Found during the 2026-08-07 live drill, in two steps, neither of which the unit suite could see.

**First defect (fixed, commit `479f24e`).** `_sample_gray_frames` extracted frames with one ffmpeg
pass driven by an `fps` filter, forcing a linear decode of the whole span. Across a 27-minute
video that meant `fps=0.0148` over 1617 s and a 60 s timeout, so `detect_video_camera_region`
timed out on every real input and Stage 2 was inert. Long spans now use one input-seek per frame.
The suite missed it because every other test in `tests/test_camera_region.py` patches the sampler
out; three tests now exercise it directly.

**Second defect (open — this issue).** With sampling fixed, the backfill stored
`{x: 0, y: 322, width: 1918, height: 757}` for both real videos — height fraction **0.701**,
essentially identical to rank 6's defective `0,330,1918,749` (0.694) and nothing like the healthy
per-clip siblings' 0.507.

The premise is wrong, not the implementation. Temporal-variance detection works per clip
*because* a 30–90 s window has a stable layout. Over 27 minutes nearly every pixel changes at some
point — overlays appear and disappear, segments differ, layouts shift — so the motion mask
approaches full-frame and the region grows to swallow the chrome it exists to exclude. Stage 1's
vertical-overlap rule cannot help here: it separates a banner contour from a camera contour, but
in the whole-video mask the *primary* blob already spans nearly the full height.

This mis-built the design that was actually approved, which said resolve the region once per video
**from a consensus of several keyframes** — what shipped was one detection over a wide span, which
is a different and worse thing.

**Current state (pre-fix):** both stored rects were nulled on prod and the
`camera_region_backfill_failed:` markers re-set (7-day TTL) so the hourly beat could not refill
them. The render path fell back to per-clip detection, i.e. Stage 1 behaviour. Nothing was
user-visibly broken — the column was simply unused.

**Fix (shipped 2026-08-10):** run `detect_camera_region` over **N short windows** (each inside
`_LINEAR_DECODE_MAX_SPAN_S` — 9 × 60 s) spread across the runtime, discard the declines, and take
a component-wise **median** of the surviving rects. A genuine consensus: robust to a window that
happens to contain an overlay burst (the median's breakdown point is 50%), and each detection runs
on the stable-layout span the detector was designed for. Reuses the per-clip path unchanged. The
median is then kept only if a **strict majority of survivors agree with it at IoU ≥ 0.80** —
otherwise the video-level rect is not stored at all. See `docs/DECISIONS.md` 2026-08-10 for the
three rulings (IoU over height-MAD, no gate re-validation, version-scoped markers).

**Acceptance**
- [x] A single overlay-heavy window cannot move the consensus (`test_one_poisoned_window_cannot_move_the_consensus` — 8 clean + 1 poisoned → 0.507, demonstrated failing under the old single-window premise)
- [x] A rect that deviates materially from the per-clip detections is not stored at all — declining is correct, and the render already falls back (`test_consensus_declines_when_the_windows_disagree`, `test_consensus_declines_below_the_survivor_quorum`)
- [x] No detection may span the whole runtime — the root cause, pinned (`test_video_region_never_runs_one_detection_over_the_whole_runtime`, demonstrated failing at `1617.0 <= 60.0`)
- [x] The failure marker is not left suppressing retries after a code fix (this cost a full cycle to notice) — the marker key is now scoped to `VIDEO_REGION_VERSION`, so a detector fix invalidates the markers its own bug wrote (`test_backfill_marker_is_scoped_to_the_detector_version`). Bumping 1 → 2 also orphans the two markers currently set on prod, with no manual Redis operation
- [x] Live: **VERIFIED 2026-08-10 on video `7e988321`.** The rect is `(169, 326, 1704, 551)` on a 1920×1080 frame — height fraction **0.5102**, i.e. the healthy ~0.51 and not the 0.694/0.701 defect. The consensus gate **passed rather than declining**: `version 2`, `windows 9`, `windows_detected 9`, `windows_agreeing 8`, `window_span_s 60.0`, `sample_frames 10`. Decisive because this upload is the SAME source recording that produced the 0.701 measurement under the old single-window premise (`duration_s = 1617.216667` on both), so the rebuild is what changed the answer. No static overlay in any of the 8 rendered clips. The ingest-time consensus log line was NOT read — the worker container restarted at 20:42 and retention starts after the 19:23 ingest — but the stored provenance is strictly stronger evidence than the log, which only matters when the column is NULL. *(Superseded concern: a transient superchat inside this correct region does reach the render — Issue 448.)* ~~**Re-scoped from `3b6992fe` to the next fresh upload**~~ — that video's source was purged 2026-08-08 (`source_uri IS NULL`), so there is nothing left to backfill or re-render. Check `videos.camera_region_jsonb` for the height fraction plus the new `windows` / `windows_detected` / `windows_agreeing` provenance. A NULL column is **not** a failure: it means a gate declined and the render fell back to per-clip detection, which is the working path — the decline log line names which gate fired

---

## Lane L27 — Clip triage & upload management (Issues 444–447)

**Filed 2026-08-10.** The creator reviews clips with no record of what they have already reviewed,
and no way to manage or remove uploads at all. This is not a side quest: Keep/Drop **is** the
channel-knowledge loop — `POST /clips/{id}/feedback` is what trains the preference model — so a loop
the creator cannot see or trust is a loop they under-use, and it is the product's one differentiator.

Owner decisions taken up front (2026-08-10): **archive by default, purge media, keep ratings**;
four issues built in order; the Keep pile tracks a clip to a finish line (rendered → downloaded →
published); the 72-hour source expiry becomes visible.

### Issue 444: clips have no persistent triage state, and the model trains on contradictory labels

**Severity: high — blocks 445/446/447 and fixes a live training-data defect.**

Review state exists today **only** as an append-only `ClipFeedback` log that no read path surfaces:
- `models.py:681-780` — `Clip` has no triage/review column. `shortlisted` is *computed*
  (`clip_engine/ranking.py:134`, `rank <= SHORTLIST_SIZE`), not stored.
- `routers/clips.py:59-102` — `ClipOut` exposes no feedback state, so the UI is physically incapable
  of showing what has been rated.
- `frontend/src/pages/Review.tsx:208` — queue position is a local `useState` index; a reload or a
  shortlist toggle resets to clip 1 with every clip looking untouched.

**The latent defect.** `preference/train.py:50-83` treats **every feedback row as a separate
training sample** — there is no dedup by `clip_id` anywhere, and no unique constraint on
`clip_feedback (clip_id, creator_id)`. A creator who rates a clip Keep and later changes it to Drop
contributes **two contradictory labels**. Reversible piles (the whole point of a restorable Drop
pile) make this dramatically worse, so it must be fixed in the same issue.

**Approach.** Separate the two concerns that are currently conflated:
- a **mutable, idempotent workflow state** on the clip (`triage`), set by `PUT /clips/{id}/triage` —
  cheap, reversible, no retrain, no side effects; this is what pile-moving calls;
- the existing **append-only event log** (`POST /clips/{id}/feedback`), contract untouched — still
  carries tags/note/trim and still triggers `retrain_preference` + `distill_style_prefs`.

Training then reads only the creator's **latest verdict per clip**. This is a label *correction* by
a single annotator over time, not inter-annotator disagreement, so latest-wins is right and
majority-vote is not.

**Industry standard checked (2026-08-10):**
- [Crunchy Data — Enums vs Check Constraints in Postgres](https://www.crunchydata.com/blog/enums-vs-check-constraints-in-postgres)
  — recommends CHECK for a status column whose value set will grow. **We went the other way and
  shipped a native enum:** the argument rests on `ALTER TYPE … ADD VALUE` being impossible inside a
  transaction, which PostgreSQL 12 made false and this project targets PG16. Consistency with the
  schema's 15+ existing enums won. Recorded in `docs/DECISIONS.md` 2026-08-10.
- [koder.ai — Soft deletes vs hard deletes](https://koder.ai/blog/soft-deletes-vs-hard-deletes)
  — nullable timestamp over boolean for archive; soft-delete-then-purge to reclaim storage; the real
  cost is applying the filter **unevenly** across read paths; soft delete alone is not erasure.
- [restfulapi.net — Idempotent REST APIs](https://restfulapi.net/idempotent-rest-apis/) ·
  [Postman — PUT vs POST](https://blog.postman.com/put-vs-post/) — PUT for a retry-safe state
  transition on a known URI; POST stays correct for the feedback event.

**Alternatives ruled out:** deriving triage from the feedback log (every list call needs a
latest-row-per-clip subquery, and "restore from Drop" would have to write a training label just to
change a workflow state — exactly the bug); a native PG enum (`ALTER TYPE … ADD VALUE` is awkward
inside Alembic's transaction and values cannot be removed or renamed); reusing `FeedbackAction` as
the triage vocabulary (conflates a training signal with a workflow state — `skip`/`format` have no
triage meaning, `trim` is orthogonal).

**Acceptance**
- [x] Migration `0057`: `clips.triage` as native enum `clip_triage_enum` NOT NULL DEFAULT `'pending'`; `videos.archived_at` nullable timestamptz (added now so 446 is a pure code change); bounded-CTE backfill deriving triage from the latest feedback row per clip (`upvote|trim|format` → kept, `downvote` → dropped, else pending); real `downgrade()` with `drop_column` before `DROP TYPE`
- [x] **Native enum, reversing the plan's VARCHAR+CHECK** — the plan's premise (`ALTER TYPE … ADD VALUE` can't run in a transaction) has been false since PG12 and this project targets PG16. Recorded in `docs/DECISIONS.md`
- [x] `server_default` is present, so the PREVIOUS image's `persist_ranked_clips` INSERT (which does not name the column) keeps working through a rolling restart
- [x] `PUT /clips/{clip_id}/triage` is idempotent — the same body twice is a 200 no-op that writes no row, enqueues no task and fires no activation event (`test_triage_is_idempotent_and_a_repeat_is_a_total_noop`)
- [x] Unknown state → 422; unknown field → 422 (`extra="forbid"`); another creator's clip → 404 via `get_owned`
- [x] **A pile move records the verdict** — state and derived label commit in ONE transaction, so the pile and the model can never disagree (`test_triage_sets_the_state_and_records_the_verdict`). Owner decision 2026-08-10, reversing the plan's label-free triage
- [x] Returning a clip to `pending` records `skip`, which supersedes the old verdict in the partition and then drops out — a real retraction, not a stale label (`test_triage_back_to_pending_records_a_retraction`)
- [x] The `clip_kept` activation funnel survives the move to the pile board (`test_first_keep_via_triage_fires_the_activation_event`); dropping never fires it
- [x] `POST /clips/{id}/feedback` keeps its exact contract — 201, `FeedbackOut {id, action}`, its rate limit, and the `_is_first_keep` guard — and now advances triage too, so the two surfaces cannot diverge whichever the client calls
- [x] **Training uses only the latest verdict per clip** (`tests/test_clip_triage_integration.py::test_contradictory_feedback_yields_one_training_label`). `PREFERENCE_MAX_TRAINING_LABELS` applies AFTER the dedup; `PREFERENCE_FEEDBACK_SCAN_LIMIT` bounds the pre-dedup scan so the window function never sorts a whole history
- [x] `format` does not supersede a verdict (render mechanics, not judgement); `skip` does (`test_choosing_a_format_does_not_supersede_a_verdict`, `test_returning_a_clip_to_pending_retracts_its_label`)
- [x] `preference/efficacy.py` shares `latest_verdict_subquery`, so the offline NDCG measures the dataset production actually trains on and the warn-ratchet cannot fire on a phantom shift
- [x] Retrain enqueued with `PREFERENCE_RETRAIN_DEBOUNCE_S=60` so a burst of pile moves coalesces into one model fit (`test_retrain_is_enqueued_with_a_countdown`)
- [x] The `label_count` consequence is stated in `docs/DECISIONS.md`: counts become distinct clips, can drop below `PERSONALIZATION_THRESHOLD_LABELS = 20`, and the threshold was deliberately NOT lowered to compensate
- [x] `ClipOut` exposes `triage`; `GET /videos/clips/counts` returns per-video `pending`/`kept`/`dropped`
- [x] `GET /videos/{id}/clips` gained NO `triage=` filter — a filtered call would log `ClipImpression` rows for a subset at their global ranks, and the table has no column distinguishing a filtered view from the full ranked list, so the exposure record would be corrupted irreversibly. The list is capped at 100 and engine clips at 24, so the piles partition client-side for free. If 445 ever needs server-side filtering, add a `context` discriminator to `ClipImpression` FIRST
- [x] Gates: backend 2938/0, Layer 0 all green (coverage 84.17, `preference` 90.24 vs floor 88.0)
- [ ] Live: after deploy, `PUT /clips/{id}/triage` twice with the same body → both 200, one row state, exactly one derived `clip_feedback` row, and one retrain enqueued rather than two

**Known gap, accepted rather than papered over:** a clip judged by the OLD image during the rolling
restart gets a `clip_feedback` row but no triage update, and the one-shot backfill has already run.
Those clips read as `pending` until re-triaged — a handful of rows in a ~30 s window.

### Issue 445: three workable piles — needs review / keep / drop

**Severity: high — this is the part the creator feels.**

Depends on 444. The queue becomes "clips where `triage = 'pending'`", which fixes the
reset-to-0-on-reload problem for free rather than as separate work.

**Settled up front (owner, 2026-08-10):** Keep/Drop commits on the FIRST click and advances; the
tag chips become optional post-hoc enrichment with an Undo, plus K/X keyboard shortcuts. Rationale:
a mandatory optional field gets satisficed — twenty forced Submits produce twenty junk tags, which
is worse for the style distiller than no tags at all.

**Open design questions to settle in this issue's own CHECK phase:** where the piles live (tabs
inside the focused one-clip-at-a-time `/review` ToolChrome flow vs a new `/library` list route —
note `App.tsx:117`'s catch-all means a new path needs a real `Nav.tsx` entry); how a pile filter
coexists with the Issue-377 shortlist toggle (`Review.tsx:231-249`); whether Keep/Drop should still
open the tag panel every time (`components/review/YourCall.tsx:139-143` — the interaction performed
dozens of times per video); and whether to introduce this codebase's **first** optimistic-update
pattern (`grep -r 'onMutate|setQueryData|cancelQueries' frontend/src` returns zero hits today)
without regressing Issue 437's honest-failure contract.

**Acceptance**
- [ ] Three piles, each independently workable: review the unreviewed, restore or purge the dropped,
      export or un-keep the kept
- [ ] Reviewed clips stay visibly reviewed across a reload; the queue resumes where it left off
- [ ] Moving a clip between piles keeps the model in step — it records the new verdict rather
      than stacking a contradiction, so the pile and the model never disagree (Issue 444
      `PUT /clips/{id}/triage`; owner decision 2026-08-10 reversed the original label-free plan)
- [ ] The Dashboard badge counts pending-triage clips and decrements as you rate — replacing the
      `counts[].rendered` sum at `pages/Dashboard.tsx:108`; the test pinning the old wrong behaviour
      (`pages/Dashboard.test.tsx:169`) is updated with a note on why it changed
- [ ] Per-video review progress is visible ("5 of 12 reviewed")
- [ ] `docs/UI.md` status-token contract honoured; exactly one `data-elevation="primary"` panel; no
      native form controls; no glyph icons; keyboard-operable

### Issue 446: manage, archive, and delete uploads — and close the render erasure gap

**Severity: high — includes a live right-to-erasure defect.**

There is no `DELETE /videos/{id}`; the only DELETE routes in the whole API are `/auth/me` and
API keys. Archive sets `videos.archived_at`, hides the video from the library, and purges its media
while **preserving `ClipFeedback`** so the preference model does not lose its training labels.
A separate, explicitly-worded "erase permanently" removes the rows.

**The gotcha that is also a bonus fix.** Renders are written **non-creator-scoped** —
`clips/{clip_id}.mp4` (`worker/tasks.py:2538`), `clips/{clip_id}_clean.mp4` (:2810),
`clips/{clip_id}_edit.mp4` (:2900) — so `delete_prefix` cannot reach them. This is not only a 446
problem: `erase_creator` purges `clips/{creator_id}/` (`routers/auth.py:486-495`), which **matches
nothing**, so **account deletion today leaves every rendered clip in R2**. The code comment says so.
The per-clip URI enumeration this issue needs is exactly the primitive that closes it.

**Status: DONE 2026-08-13 (W4 erasure lane, branch `fix/issue-446-471-erasure`).** `DELETE /videos/{id}` (archive: purge media via enumeration, PRESERVE clips + feedback + posters/peaks per the owner default), `POST /videos/{id}/restore`, `POST /videos/{id}/erase` (permanent, confirm-gated, names the training-data loss) — archive/restore idempotent. `archived_at IS NULL` on list/catalog/counts/aggregates/export job/backfills, one test per path; the source-retention beat deliberately still matches archived rows (Object-Lock retry belt — DECISIONS). `source_expires_at` + expired flag surfaced per video.

**Acceptance**
- [x] Archive/restore a video from `VideoTable`; archived videos leave the library
- [x] `archived_at IS NULL` applied to EVERY read path — `GET /videos`, `GET /videos/catalog`,
      `GET /videos/clips/counts`, the Dashboard and Profile aggregates, the data-export job
      (`worker/tasks.py:4876`), and the backfill/purge beat tasks. Enumerated by 444, audited here
- [x] Media purge enumerates per-clip URIs from the DB (not `delete_prefix`) and reuses the
      `_purge_stale_source_media_async` posture (`worker/tasks.py:3983-4066`): release the session
      before I/O, delete each blob independently, null only what succeeded
- [x] Archiving does **not** delete `ClipFeedback`; "erase permanently" says plainly that it does
- [x] `erase_creator` is wired to the same per-clip enumeration — account deletion actually removes
      renders. `docs/COMPLIANCE.md` updated; the known-gap note at `routers/auth.py:486-489` removed
- [x] The 72-hour source expiry is visible per video: time remaining, and an explicit
      "can no longer be re-rendered" state once `source_uri IS NULL` (today the only signal is a
      409 `source_expired` at render time, `routers/clips.py:841-854`)

### Issue 447: the Keep pile needs a finish line

**Severity: medium — "where do my approved clips end up?"**

`render_status` and `ClipPublication` already carry two thirds of it. The gap: **a download is a
302 to a presigned URL with no server-side record** (`routers/clips.py:1854-1895`), so "did I
already download this?" is unanswerable. Download and publish both exist but are buried in the
review card (`YourCall.tsx:212-224`) and the long-form Export panel
(`components/editor/LongFormEditor.tsx:317-353`).

**Status: DONE 2026-08-13 (same lane; migration 0060).** Download tracking is a COLUMN (`clips.downloaded_at`): publications record publishes not downloads, event_logs purges at 90 days, and the endpoint is dual-purpose (inline backs the in-app player) — so the stamp writes only for `disposition=attachment` AND `variant=original`, cleared on re-render and the confirm swap. `ClipOut` gains `downloaded_at` + a latest-publication summary via ONE aggregate (no N+1, pinned). Keep pile shows rendered → downloaded → published chips, an authed Download link, and a Publish entry reusing PublishPanel; neutral copy, no-virality tests green.

**Acceptance**
- [x] Each kept clip shows where it is: rendered → downloaded → published
- [x] Decide with evidence whether a download needs a server-side record (a column) or can be
      inferred; if a column, justify it against simply reading `ClipPublication`
- [x] Download and schedule-publish are reachable from the Keep pile, not only from the review card
- [x] No surface promises virality or reach (structural test stays green)

---

## Lane L28 — Audit findings from the 2026-08-10 fresh upload (Issues 448–449)

**Filed 2026-08-10** from the first full-set audit of video `7e988321` ("2026-08-05 07-59-55",
Backboard Media, 12 clips / 8 rendered). This upload is a **true A/B against the 2026-08-07
baseline** — `duration_s = 1617.216667` on both, i.e. the SAME source recording re-uploaded — so
every difference is attributable to the engine, not the input. 438/440/443 all verified fixed on
it. The two issues below are the residue: neither is a regression, and neither was reachable
before this upload.

### Issue 448: a transient superchat overlay inside the camera region is burned into the render

**Severity: high — visible, burned in, on a delivered clip; recurs on every livestream source.**

**Measured against the source, 2026-08-11 (supersedes the first estimate — see below).** A 1 Hz
scan of all 1617 s of source found a superchat present in **two spans — 885–914 s (30 s) and
930–1006 s (77 s) — 107 s total, 6.6 % of the runtime.**

Two of the nine rendered clips overlap those spans:
- **Rank 3** — `@Drew-l6j $1.99 / "Worried they're gonna bench Mikey"` for **11.6 s (13.8 % of the
  clip)**, running to the clip's end.
- **Rank 13** — `@jacobcortes939 $1.99 / "how did the center position look"` for **25.3 s (28.1 %)**,
  starting at frame one, so it is burned into the hook. It is also the clip the creator **kept**.

Ranks 1, 2, 4, 5, 6, 7, 8 are clean, as are the four unrendered clips.

⚠️ **The first revision of this issue said rank 13 carried the band for 5.0 s. That was wrong —
it is 25.3 s.** The original figure came from scanning the *rendered* clip for cyan pixels in the
bottom 200 px, which caught only the portion where the banner sat in that strip at that hue. Two
lessons, both now encoded in `tests/fixtures/superchat/README.md`: measure on the **source**, and
do not key on colour. Rank 3's 11.5 s estimate was close (11.6 s actual).

**Never measure this on a rendered clip.** The burned-in captions are themselves a bright
lower-frame band; a rendered-clip scan on 2026-08-11 flagged ranks 1 and 6, both visually clean.

**This is not a regression of 439 or 443 — the region is correct.** The stored consensus rect is
`(169, 326, 1704, 551)`, height fraction **0.5102**, from 9 windows detected with 8 agreeing. A
source frame at t≈887 s with the rect overlaid shows it tightly enclosing the two camera feeds and
**excluding** the SUBSCRIBE button, the `@WSHCARTER` socials strip, the WSH Carter logo and the
top player graphic. The superchat is drawn by the streaming software *on top of* the lower part of
the camera feed — i.e. **inside** an otherwise-correct region.

**Why the existing machinery cannot catch it.** The region is resolved **once per video** and is
static in time. Worse, Issue 443's fix actively hides this class: the per-window component-wise
median exists precisely to discard transient outliers, so an overlay present in one window of nine
can never move the consensus. Per-clip detection would not help either — the overlay is transient
*within* a single clip.

**Fix direction (needs its own CHECK phase — do not build from this sketch).** The honest options
are temporal, not geometric: detect overlay bands per-segment and either (a) shrink the crop for
the affected span, (b) letterbox/blur the band, or (c) surface it as a flagged clip the creator
can re-cut. Option (c) is cheapest and matches the honesty constraint. Note the render's crop is
a single static `crop=` for the whole clip today, so (a) is a real architectural change.

**Status: DONE (2026-08-11)** — backend **2960/0**, eval 25 scenarios / 100%, Layer 0 clean.
Design rulings in `docs/DECISIONS.md` 2026-08-11.

**What shipped**
- `clip_engine/overlay_bands.py` — detection over the whole source at ingest, reusing
  `camera_region`'s samplers and analysis-to-source scaling. Feature: per frame, rows in the
  lower 40 % whose mean exceeds *that frame's own* lower-region median by 25. Fixture separation
  is 14-20 clean vs 26-28 band; the threshold sits between.
- Migration **`0058`** — `videos.overlay_spans_jsonb`, additive nullable JSONB (Template A, no
  offline-mode branch needed). `OVERLAY_SPANS_VERSION` carries the same bump-on-semantic-change
  contract as `VIDEO_REGION_VERSION`.
- Ingest resolves it with the same never-fails-ingest posture as the camera region; the existing
  hourly sweep backfills it, sharing ONE source download rather than paying egress twice.
- `clip_engine/render.py` inserts `split` -> `crop` -> `boxblur` -> `overlay` with
  `enable='between(t,…)'` FIRST in the chain, in absolute source pixels, so the region pre-crop
  and the 9:16 crop carry the masked pixels through by construction.
- `OVERLAY_BAND_DETECT_ENABLED` (default false) in `config.py` + `.env.example`.

**Acceptance**
- [x] A clip whose region contains a transient overlay does not ship it burned in — **proven on
      the real source**: rank 3 re-rendered through the production filter chain, masked from
      72.78 s to the clip end, donor name/amount/message unreadable, framing and captions
      untouched. Clean before the span at t=70 s
- [x] Proven on both placements: rank 3 (11.6 s at the end) and rank 13 (25.3 s from frame one).
      The mask is one time-gated overlay, so start-of-clip and end-of-clip need no special-casing
- [x] `detect_video_camera_region` is untouched — this is a separate module and a separate
      column, precisely because 443's outlier rejection is correct and load-bearing
- [x] No regression on clean clips: `test_no_overlay_mask_is_byte_identical` asserts a clip that
      misses every span renders the exact vf string it renders today, and all pre-existing
      render/camera-region tests pass unchanged
- [x] Detector unit-tested on the frozen fixtures — both transitions land on the measured
      seconds (885 / 1006) and the clean control produces no run
- [x] The emitted graph is compiled by real ffmpeg in CI, not just string-matched
- [ ] Live: re-render through the deployed pipeline with `OVERLAY_BAND_DETECT_ENABLED=true`.
      **Re-scoped 2026-08-12 (owner decision, `docs/DECISIONS.md`):** the original source was
      deliberately allowed to purge (2026-08-13 19:23 UTC) because Issue 466 breaks the sampler
      on this 1617 s source — the drill would false-fail. The live proof moves to the **W6
      fresh-upload back-test**: after 466+467 land, the owner re-uploads the SAME recording
      ("2026-08-05 07-59-55") and the drill runs against the frozen ground truth
      (885–914 s / 930–1006 s, `tests/fixtures/superchat/README.md`) plus the pre-L29 snapshot
      (`docs/assessment/exports/7e988321_pre_l29_snapshot.json`)

### Issue 449: `snap_start`'s inter-sentence-pause exemption bypasses the Issue-441 weak-opener guard

**Severity: medium — one shipped clip opens on a discourse marker, which 441's live box forbids.**

Rank 4 of `7e988321` opens audibly on **"Yeah."** — a token in `_DISCOURSE_MARKERS`, so
`is_weak_opener("Yeah.")` is `True`. Issue 441 exists to walk such a start back, and here it never
ran.

**Root cause, reproduced against the real transcript.** `clip_engine/sentence_snap.py:200-215`:
when the start does not fall *inside* a sentence, `snap_start` treats it as a clean open and
returns it untouched — unless it coincides with a sentence start within
`_BOUNDARY_EPSILON_S = 0.05`. The shipped start `1306.43` sits in the pause `[1306.27, 1306.51]`,
**0.08 s** before the "Yeah." sentence — just outside the epsilon. So the weak-opener walk-back at
`:236-242` is never reached and the clip opens on the marker anyway.

Verified (`build_sentence_index` over the video's own 190 Deepgram segments, 607 sentences):
```
snap_start(1306.43) -> 1306.43   # untouched — opens on "Yeah."   (the shipped value)
snap_start(1306.48) -> 1301.87   # 0.03 s later: guard fires, walks back correctly
snap_start(1306.52) -> 1301.87
```
The walk-back was **not** budget-limited: the cost was 4.56 s against `SENTENCE_SNAP_MAX_S = 10.0`.
And `1306.43` is only producible by the untouched-pause return — had the guard chosen the "Yeah."
sentence, the lead-in floor would have yielded `1306.27`.

**Fix direction:** in the pause branch, when the start sits between two sentences, test the
sentence that *begins next* — that is what the clip will audibly open on — and apply the same
bounded walk-back. This is **not** the widening the code comment at `:206-208` warns against: that
warning is about snapping a pause start FORWARD to claim the next sentence's opener; this walks
BACKWARD, which only adds context.

**Also observed, same audit, different mechanism — do not fold it into this fix.** Rank 9 (not
rendered) opens on `"feel like Percy Butler is a starting free anything"`; the preceding word is
`"don't"`, so the cut **inverts the speaker's meaning**. `build_sentence_index` opened a sentence
span at "feel" because Deepgram ended an utterance there — the index trusts utterance boundaries
as grammatical ones. No closed word-class catches this, which is why 441 knowingly scoped it out
(the "the Terry thing, no." case is the same shape and appears here as rank 7). Logged in
`docs/OFF_COURSE_BUGS.md`; promote only with a real approach, not a longer word list.

**Status: DONE 2026-08-13 (same branch/commits as 456).** Pause branch now targets the first sentence at/after the start (what the clip audibly opens on): strong openers stay untouched (never snapped forward), weak openers take the existing 441 walk-back. The real 1306.43 case demonstrated failing first (returned untouched, opens on "Yeah.") then lands at 1301.87 (cost 4.56 s of the 10 s budget). All 441 pinned cases re-verified green.

**Acceptance**
- [x] A start landing in the pause before a weak-opening sentence walks back, bounded by the same
      step count and `max_snap_s` budget as the in-sentence path
- [x] The regression test is written against the real `1306.43` case and demonstrated **failing
      first**
- [x] `test_snap_start_clean_boundary_unchanged` and the coordinator/pronoun exclusions stay green
      — the pinned snap cases from 441 are not re-litigated
- [x] Eval scenarios stay at 100%; `SCENARIO_FLOOR` raised only if a fixture is added

### Issue 450: a two-shot with only one detected face track holds the crop on the silent person

**Severity: high — the creator dropped the clip for this reason, in their own words. It is the
first live rejection of a framing decision this project made deliberately.**

Rank 1 of `7e988321` frames the **non-speaking** participant for its entire 34.55 s. The creator's
verbatim feedback (`clip_feedback`, 2026-08-10 23:37 UTC, `downvote`):

> "When Rio is talking (the guy on the right), it is on the man on the left (who is not talking)"

**Confirmed on the media, not inferred.** The stored track holds `crop.x = 230` (width 309) inside
a 1704-wide region — the LEFT third — with **1 keyframe, 0 flips, 0.0 moves/s**. Source frames at
t = 758 / 768 / 780 s all show the right seat (Rio) mid-sentence, mouth open, leaning into the
mic, and the left seat (Carter) silent and looking down. Deepgram attributes **all 31.0 s** of
speech inside the window to a single speaker.

**SETTLED 2026-08-11 — `hold_seats` RAN, and the defect is its vote.** Measured in the app
container against the source before it purged; full numbers in
`tests/fixtures/reframe_seats/README.md`:

| Quantity | Value |
|---|---|
| `len(tracks)` | **2** — gate 1 passed, it did not bail |
| track `median_cx` | **381.2** and **1257.5** (region space, width 1704) |
| `crop_w` | 309 — seats 876 px apart, so no collapse to one framing |
| occupancy gate | passed (most sampled frames detect 2 faces) |
| `_seat_hold_plan` | **1 hold point at x = 381**, 0 cuts |
| `n_speakers` / `coverage` | **1** / **1.0** |
| `mapping.confidence` | **0.084** vs `REFRAME_MIN_MAPPING_CONFIDENCE = 0.2` |

**The defect is `Counter(_nearest_seat(obs[0].cx))`.** `obs[0]` is the **largest** detected face
(cf. `_raw_track_largest_face` directly below), so "dominant seat" means *the seat that was the
biggest face in the most samples* — uncorrelated with who is talking. It chose x = 381, the LEFT
seat, and the right seat is the one speaking. Whoever sits closer to their camera wins the clip.

**What was available and unused:** diarization had **full coverage (1.0) and exactly one speaker**
for the entire window. The engine knew someone was talking throughout; it could not map that
speaker to a face confidently, and fell back to a size vote rather than a speech-aware one.

**Issue 440 behaved exactly as designed** — it made this rung stop sweeping; it never claimed to
pick the right seat.

*(Two corrections closed here. The first revision asserted `hold_seats` bailed at
`len(tracks) < 2`, inferred from `speakers.count = 1` — which is the diarized-speaker count, not
the face-track count. That claim was withdrawn 2026-08-10 as unverified, and this measurement
confirms the withdrawal was right: there were two tracks all along.)*

**The accepted tradeoff is now falsified.** `reframe.py:900-904` justifies the hold with *"a still
frame on the wrong person is far cheaper than a cut to the wrong person, and stillness is what the
creator asked for."* The creator has now rejected precisely that outcome. Note the failure is
arguably worse post-440: a sweep was wrong half the time, a hold is wrong continuously.

**Do not fix this by reverting 440's hold** — the 343-keyframe sweep is not an improvement, and
DECISIONS records the speaker-following experiment on this rung being rejected at five flips in
ten seconds. The tractable direction is upstream: find out **why only one face track was built on
a two-shot** (Rio wears wraparound sunglasses and a bucket hat for the whole recording, which is a
standing condition for this creator, not a one-off), and if a second seat cannot be detected,
prefer a wider framing that contains both seats over a tight crop on an arbitrary one.

**Status: DONE (2026-08-12)** — backend **2975/0**, eval 25 scenarios / 100%, Layer 0 green,
`clip_engine` coverage **93.03** (floor 91.0). Ruling in `docs/DECISIONS.md` 2026-08-12.

**The approved split-screen approach was REVERSED before any of it was built**, on evidence: the
speaker mapping had already assigned the speaker to the correct (right) seat, and `hold_seats`
simply never consulted it. Building a new render mode to route around a signal we compute
correctly and then discard would have been the wrong fix. Full reasoning in DECISIONS.

**What shipped** — `speaker_map.speaking_track_for_span()` returns the face track of whoever talks
most inside a span, keyed by the same `shot_index_for` that `build_face_tracks` uses to stamp
`shot_idx`. `_seat_hold_plan` asks it first and falls back to the largest-face vote only when
there is no speech signal. ~50 lines, no wire-contract change, no frontend work.

**Acceptance**
- [x] **Establish which branch ran** — DONE 2026-08-11 in the app container before the source
      purged: `hold_seats` ran with 2 tracks and chose the wrong seat via the largest-face vote.
      Evidence and fixtures in `tests/fixtures/reframe_seats/`
- [x] Rank 1 of `7e988321` no longer frames the silent participant — **verified on production
      data**: the same probe over `[754.62, 789.17]` returns `BEFORE [381]` / `AFTER [1258]`, and
      rendered frames at t=768 s show the silent participant before and the speaking one after
- [x] The seat choice is justified in `docs/DECISIONS.md` against the alternatives, including why
      no `mapping.confidence` floor is applied (it is a MARGIN ratio, structurally small on exactly
      these layouts, so gating on it would restore the size vote where it is worst)
- [x] Issue 440's motion guarantees preserved — ONE static choice per span, held until a source
      shot change, so it cannot flip; `test_two_shot_face_pan_holds_seats_instead_of_sweeping`
      passes unchanged
- [x] Regression tests encode "holds the speaking seat" in `tests/test_reframe_planner.py`
      (`TestSpeakingSeatSelection`, built from the measured cx 381.2 / 1257.5 geometry and
      demonstrated failing first) and `tests/test_speaker_map.py` (`TestSpeakingTrackForSpan`) —
      **not** `tests/eval/scenarios/`, which covers clip-window geometry, not framing
- [x] The adversarial case is pinned: the wrong seat having the LARGER face in every sample must
      not win
- [x] No-transcript / unmapped / pruned-track spans still use the size vote, so a video without
      diarization behaves exactly as it does today

### Issue 451: a rendered clip has no re-render affordance, so render fixes can never reach it

**Severity: high — it makes every render-pipeline fix unverifiable on existing clips, and it cost
a live verification attempt on 2026-08-12.**

`ShortStage.tsx:116` is `{clip.render_uri ? <player/> : <StagePlaceholder/>}`, and the render
button lives inside `StagePlaceholder`. So the trigger exists **only for clips that have never
rendered**. Once a clip is `done` there is no path in the UI to render it again — the creator
looked for one and reported it missing.

The backend supports it fully. `POST /clips/{id}/render` treats a request on a `done` clip as an
explicit re-render (Issue 353): it resets `render_status`/`render_uri` in the same transaction,
snapshots the previous URI, and restores it if the enqueue throws (Issue 359c). Only the UI is
missing.

**Why it matters beyond convenience.** Issue 450 fixed the framing on a clip the creator had
already dropped. With no re-render affordance the fix could never reach that clip — the creator
could not have seen it no matter what they clicked. Verifying it required replicating the
endpoint's reset by hand against prod. Every future render-pipeline fix has the same problem:
**it only ever applies to clips rendered after the deploy.**

Note a celery-direct enqueue is NOT a workaround: the worker skips a clip that already has a
`render_uri` ("already rendered — skipping"), which is exactly why the endpoint owns the reset.

**Fix direction.** Surface a re-render control on a `done` clip. The state machine already exists
in `useClipRender` (`rendering`/`renderError`/`sourceExpired`/`triggerRender`) — the work is
placement and copy, not logic. Two things to decide in CHECK: where it lives so it is discoverable
without inviting accidental clicks (it blanks the player until the new render lands), and whether
it warns that the source expires at `SOURCE_MEDIA_RETENTION_HOURS` — after which it 409s
`source_expired` and the clip can never be re-rendered.

**Acceptance**
- [x] A `done` clip can be re-rendered from the UI, and the control is not confusable with the
      destructive actions around it — **placement decided 2026-08-12: the actions rail**, beside
      the existing "Apply trim & re-render", as `variant="secondary" size="sm"` with the same
      `RotateCcw` icon. Keep/Drop are `success`/`danger` and visually distinct. Chosen over the
      stage's meta row, which would have shrunk the player unless `STAGE_MEDIA_W`'s measured 7 rem
      chrome budget (`lib/toolLayout.ts:59`) were re-measured
- [x] The existing `useClipRender` ladder is reused, not reimplemented. The stage's instance and
      this one cannot contend: the control only renders while `render_uri` is set, which is exactly
      when the stage shows the player and ignores its own copy; once the endpoint clears
      `render_uri` the stage takes over and drives its spinner from server `render_status`
- [x] The player does not appear permanently broken while the re-render runs — the button reads
      "Re-rendering…" and the copy says the player comes back when it lands
      (`does not leave the player looking permanently broken while the re-render runs`)
- [x] A clip whose source has been purged explains that instead of offering a button that 409s —
      the `source_expired` branch replaces the control outright
      (`explains a purged source instead of offering a button that 409s`)
- [x] Regression test: a clip with `render_uri` set exposes the trigger and POSTs
      `/clips/{id}/render`; a never-rendered clip offers nothing (StagePlaceholder already owns
      that case, and a second trigger would be a duplicate affordance)

### Issue 453: every Stripe call fails — billing has never worked in production

**Severity: SEV1 — total revenue outage, live for 10 weeks. Found 2026-08-12.**

`billing/stripe_client.py:40` builds the module singleton's transport as
`stripe.HTTPXClient(timeout=settings.STRIPE_TIMEOUT_S)`. That constructor defaults
`allow_sync_methods=False` (`stripe/_http_client.py:1227`), which leaves the sync `httpx.Client`
unbuilt (`:1255`), so `HTTPXClient.request()` raises before any network call (`:1286`):

> `Stripe: HTTPXClient was initialized with allow_sync_methods=False, so it cannot be used for
> synchronous requests.`

**Both** of our Stripe call sites use the SYNC API, deliberately offloaded to a thread — 
`routers/billing.py:153` (`asyncio.to_thread`, Wave-3 Fix C) and `worker/tasks.py:4473`
(`run_in_executor`). So both fail 100 % of the time:

- `POST /billing/checkout` — three identical failures in the prod log at 2026-08-12T20:17:48–49Z
  (the owner's purchase attempts). `grep -c "billing checkout_session"` over 168 h of prod logs
  returns **0**: no Checkout Session has ever been created.
- `reconcile_stripe_ledger` — the beat task raises this same `RuntimeError` on every run
  (worker log, 2026-08-12T19:22:13Z).

**Introduced by `334d1f7` (2026-05-31, Issue 106)** — the commit that added the timeout. Before it
the client was `stripe.StripeClient(settings.STRIPE_SECRET_KEY)`, using the default transport,
which works. The timeout was correct to want; `HTTPXClient` was the wrong way to get it without
the flag.

**Why nothing caught it.** Every billing test mocks at or above `create_checkout_session`
(`tests/test_billing.py:604-620`), so no test in the repo exercises the transport.
`scripts/doctor.py:405` probes Stripe with a raw `httpx.get` rather than our client, so
`doctor.py --full` reported "stripe auth ok" on 2026-07-29 — which is what `docs/GO_LIVE.md:71`
cites as "Stripe live-verified" and why `:89` marks billing GREEN. Logged in
`docs/OFF_COURSE_BUGS.md` (2026-08-12).

**Approach.** Pass `allow_sync_methods=True`. This restores the architecture both call sites
already document; it does not change the concurrency model.

**Alternatives ruled out:** switching both call sites to Stripe's `*_async` methods and dropping
the thread offload — defensible and arguably cleaner, but it rewrites two money paths (one of them
a Celery beat task) during a live outage, and contradicts the documented Wave-3 Fix C design. Worth
revisiting deliberately, not under an outage. Reverting to the default transport would restore
billing but re-open Issue 106's 80 s-default-timeout finding.

**Acceptance**
- [x] `allow_sync_methods=True` on the `HTTPXClient` singleton, with a comment saying why it is
      load-bearing
- [x] Regression test pinning that the singleton's sync transport is built
      (`tests/test_sdk_timeouts.py::test_stripe_http_client_allows_sync_requests`) — demonstrated
      to discriminate: `HTTPXClient(timeout=10)._client` is `None`, with the flag it is a `Client`
- [x] `docs/DECISIONS.md` records the deviation and why the async rewrite was declined
- [ ] Live: a real purchase on prod creates a Checkout Session and credits minutes; the prod log
      shows `billing checkout_session` and no `allow_sync_methods` RuntimeError
- [ ] Live: `reconcile_stripe_ledger` completes without raising on its next beat
- [ ] `docs/GO_LIVE.md:89` corrected — billing cannot be GREEN until a real purchase has settled

### Issue 455: 453's fix was necessary but not sufficient — HTTPXClient's CA trust store is empty

**Severity: SEV1 — billing was STILL 100 % broken after 453 shipped and deployed clean.**

453 fixed `allow_sync_methods` and was verified green in CI and live in the prod container. Billing
still did not work. The first bug was masking a second, independent one in the same constructor.

`stripe.HTTPXClient.__init__` (stripe==11.4.0) builds its SSL context as
`ssl.create_default_context(capath=stripe.ca_bundle_path)`. `capath` takes a **directory** of hashed
CA files; `ca_bundle_path` is a **.pem file**. Measured:

```
ca_bundle_path = .../stripe/data/ca-certificates.crt   is_file: True   is_dir: False
CA certs via capath (what the SDK does): 0
CA certs via cafile (correct):         135
```

Zero CAs ⇒ every TLS handshake to Stripe fails, surfacing as a bare
`APIConnectionError("… A ConnectError was raised")` that reads like an outage or a firewall rather
than a client-config bug.

**How it was isolated.** From inside the prod app container, `socket.gethostbyname("api.stripe.com")`
resolved and plain `httpx.get("https://api.stripe.com/v1/balance")` returned **401** — reachable —
at the same moment the SDK client could not connect at all. That ruled out network/DNS/egress and
pointed at the client's own SSL context.

**Approach.** Drop `HTTPXClient`; use `stripe.RequestsClient(timeout=settings.STRIPE_TIMEOUT_S)`,
the SDK's default transport, which takes the timeout directly and passes the bundle as
`verify=<file>` (correct usage). **Verified against live Stripe from the prod container with a
read-only `checkout.sessions.list` BEFORE the change was written.**

**Alternatives ruled out:** a hand-built `httpx.Client` with a correct SSL context (re-implements
transport plumbing to route around an SDK bug, and still needs the sync flag);
`verify_ssl_certs=False` (never — disables TLS verification on a payment path); a `stripe`
major-version bump (during an outage, on no evidence it fixes this line).

**Acceptance**
- [x] `_STRIPE` uses `RequestsClient`; `HTTPXClient` is gone, with a comment naming BOTH defects so
      it cannot be "tidied" back
- [x] The regression test asserts the property that matters — the configured transport can issue a
      real request (not-HTTPXClient + non-empty trust store + timeout preserved) — rather than
      either individual bug, since pinning only the first is what let the second through
- [x] `requests` pinned in `requirements.txt`, now a direct dependency
- [x] `docs/DECISIONS.md` carries a CORRECTION on the 453 entry plus its own entry
- [ ] Live: a real purchase settles end to end and credits minutes
- [ ] Live: `reconcile_stripe_ledger` completes without raising on its next beat

### Issue 454: the checkout intent id is scoped to the browser tab, not the purchase

**Severity: high — it breaks the second checkout attempt in any tab. Masked until 453 is fixed.**

`frontend/src/pages/Pricing.tsx:35-43` caches one v4 UUID in
`sessionStorage['creatorclip_checkout_intent_id']` and never clears it — not after a successful
purchase (`:51-58` only strips query params), not when a different pack is clicked, not on reload.
`billing/stripe_client.py:127-130` derives Stripe's `Idempotency-Key` from it as
`checkout:{creator_id}:{intent_id}`, and the request params include `unit_amount`, which differs
per pack (`:82`).

Stripe errors when a key is replayed within its 24 h window with different parameters, so clicking
Starter then Creator in one tab sends the same key with a different `unit_amount` → 400
`idempotency_error` → `routers/billing.py:161` catch-all → 502 → one generic toast. Second
symptom: repeating the SAME pack replays Stripe's cached response, handing back an
already-consumed Session.

**The code contradicts its own documentation in three places** — `Pricing.tsx:33-34`,
`stripe_client.py:55-56` and `routers/billing.py:62-63` all claim a page load produces a fresh
intent. `sessionStorage` is scoped to the tab and survives reloads.

**Industry standard checked (Stripe docs, 2026-08-12):**
- [Idempotent requests](https://docs.stripe.com/api/idempotent_requests) — "The idempotency layer
  compares incoming parameters to those of the original request and **errors if they're not the
  same**."
- [Advanced error handling](https://docs.stripe.com/error-low-level#idempotency) — a key must
  "unambiguously identify a single operation within your account over the last 24 hours";
  "**Generate a fresh idempotency key when modifying the original request**"; "the safest strategy
  where `4xx` errors are concerned is to always generate a new idempotency key."
- [How Checkout works](https://docs.stripe.com/payments/checkout/how-checkout-works?payment-ui=stripe-hosted)
  — the application creates a **new** Checkout Session when the customer is ready to pay.

Our per-tab key contradicts all three. Issue 106's `creator_id` prefix is correct and stays; only
the lifetime of the client half was wrong.

**Approach.** Scope the id to one *purchase attempt*: an entry is created on the first click for a
pack and cleared when the request settles, so concurrent clicks within one attempt still share a
key (preserving Issue 106's double-click protection) while the next attempt gets a fresh one. Hold
it in a `useRef` map, not `sessionStorage` — surviving a page load was the only thing storage
bought, and that is the bug. Add the in-flight `disabled`/`aria-busy` guard the buy buttons have
never had (`:139-148`).

**Alternatives ruled out:** a key per pack in `sessionStorage` (fixes the cross-pack 502, not the
repeat purchase); clearing only after the redirect (fixes the repeat purchase, not the 502 — the
user can switch packs with no intervening navigation); a fresh UUID per click guarded only by a
disabled button (regresses Issue 106 — a disabled button is a UI affordance, not a concurrency
guarantee).

**Status: CODE-COMPLETE 2026-08-12** — attempt-scoped `useRef` map + synchronous in-flight latch
in `Pricing.tsx`; 409/502 `{code, message}` branches with scrubbed classification-field logging in
`routers/billing.py`. Backend 51/51 billing tests, frontend 662/662, tsc/eslint clean. The two
live boxes are proven structurally by tests; their live half rides the W0.6 real-purchase drill.

**Acceptance**
- [x] A different pack clicked in the same tab starts checkout instead of erroring — each settled
      attempt gets a fresh `intent_id` (pinned: cross-pack + same-pack test asserts 3 distinct
      UUIDs); live confirmation rides the W0.6 purchase drill
- [x] A repeat purchase of the same pack gets a new, payable Session — same test; the attempt key
      is deleted on settle, so no key ever meets Stripe twice across attempts
- [x] A double-click still issues exactly ONE `POST /billing/checkout` (Issue 106's guarantee),
      pinned by a test — synchronous `inFlightRef` latch (state alone can't beat two clicks in one
      tick), plus the shared per-attempt key as defense-in-depth
- [x] `routers/billing.py` maps `stripe.IdempotencyError` to 409 `checkout_conflict` and returns
      the project's `{code, message}` detail shape on every branch; the client shows a distinct
      conflict message and says nothing was charged on both failure paths
- [x] The Stripe failure log carries `error_class`/`stripe_type`/`stripe_code`/`stripe_request_id`
      and **never** the idempotency key or `intent_id` — pinned by two caplog tests (one plants
      the key in the exception message and asserts it never reaches the log)
- [x] The three lying comments are corrected (`Pricing.tsx` intent comment, `stripe_client.py`
      docstring, `routers/billing.py` `CheckoutRequest` comment)
- [x] `frontend/src/pages/Pricing.test.tsx` exercises `buyPack` — five tests: happy path,
      double-click, fresh-key-per-attempt, 409 conflict, generic failure + re-enable

### Issue 452: clip title and caption truncate in the focused review view

**Severity: medium — the creator hit it, and the fallback is also broken.**

`ClipMetadataPanel.tsx:247` renders the title with `truncate` plus a native `title={value}`
tooltip. Native tooltips clip long strings themselves, so the escape hatch fails the same way the
thing it is escaping does — the creator reported "you can hover, but the hover sometimes cuts off
too".

Issue 445 already fixed this **in the kept/dropped pile rows**, where title and caption wrap
(`components/review/ClipPiles.tsx`). This is the same fix for the focused review view, which 445
did not touch.

**Owner decision (2026-08-12):** expand to fit — wrap to multiple lines rather than clamping with
a toggle. The panel gets taller for long values; nothing is ever hidden.

**Acceptance**
- [x] Title and caption wrap to as many lines as they need in the focused review view —
      `truncate` → `break-words` in `ClipMetadataPanel.tsx` (both the value row and the `—`
      placeholder) and in `AppliedTitleField.tsx`, which had the identical pair
- [x] No native `title=` tooltip is relied on to reveal clipped text — removed from both files
- [x] The actions rail does not shift or overflow when a value is long — `items-center` →
      `items-start` on the metadata row and the applied-title row, so the label and the
      Applied/Suggested/Edit controls stay top-aligned against a multi-line value. The rail itself
      was already safe: `min-h-0 flex-col … lg:overflow-y-auto` in a `minmax(300px, 26rem)` grid
      cell with no fixed child heights, so growth is purely vertical and the stage cell's
      `100cqh`-derived player size is unaffected
- [x] Regression test with a title longer than the panel width asserts the full string is present,
      that no `.truncate` and no `[title]` survive in either row, and that the row is `items-start`
      (`ClipMetadataPanel.test.tsx` — the old "one truncating row each" assertion was **reversed in
      place**, naming the old behaviour and the DECISIONS date, per the repo's convention for a
      deliberate reversal)

---

## Lane L29 — Clipping-integrity audit (Issues 456–482)

**Filed 2026-08-12** from the four-dimension clipping-integrity audit — full report, per-dimension
verdicts, refutations, and the 88-row disposition table live in
**`docs/assessment/CLIPPING_INTEGRITY_2026-08-12.md`**. Baseline at `41012fc`: backend 2975/0,
eval 24 scenarios / 100 %, Layer 0 green. Method: 12-agent workflow (4 confirmers over the
exploration dossier, 4 hunters, 3 adversarial verifiers whose charter was to refute), every SEV1
re-adjudicated by hand with reruns of the repros. Every issue below is verified **new** — 6
already-tracked, 6 DECISIONS-accepted, and 2 refuted candidates were filtered out upstream.
Batches: **A** selection geometry 456–460 · **B** scoring & prompts 461–465 · **C** pipeline &
render 466–471 · **D** learning loop 472–475 · **E** eval & CI integrity 476–482.

### Batch A — Selection geometry (456–460)

### Issue 456: unbounded backward sentence-snap can ship a clip whose peak is outside the window

**Severity: SEV1 — the detected moment and its entire payoff can be silently absent from the
delivered clip; the degenerate case collapses a whole video's output to one clip.**

`snap_start`'s absolute rule ("never leave a start mid-sentence", `clip_engine/sentence_snap.py:191-193`)
takes the backward target **with no distance bound** when forward is unusable (`:226-229`). The 90 s
clamp's payoff guard (`:315-319`) assumes the snap moved the start ≤ ~10 s; its fallback
`new_end = setup_start_s + CLIP_TARGET_MAX_S` is itself ≤ peak whenever the start was dragged
further, and the result is **never re-checked against the peak**. Related: the same tail can end a
clip before its peak after a long run-on backward snap (same missing re-check).

**Repro (rerun 2026-08-12):** candidate `{setup 225, peak 300, end 320}` + a 30 s unpunctuated
utterance spanning the setup → shipped window `[204.7, 294.7]`, `peak_s=300` outside. Degenerate:
with the Deepgram no-utterance fallback (whole video = ONE unterminated sentence,
`ingestion/transcribe.py:248-259`), every candidate collapses to `[0, 90]`, all peaks excluded,
`suppress_contained` leaves ONE clip for the entire video.

**Root cause:** two rules with incompatible assumptions — the absolute never-mid-sentence rule vs
a clamp guard written for the 10 s budget — and no post-snap `peak inside [setup, end]` invariant.

**Fix direction (needs its own CHECK phase — do not build from this sketch):** add the missing
invariant (drop or re-anchor a candidate whose peak exits the window post-snap), and bound the
absolute rule or fall back to the raw start when the backward target is further than the peak
allows. The no-utterance degenerate case needs its own handling (see also Issue 481).

**Status: DONE 2026-08-13 (W2 Batch A, branch `fix/issue-456-449-sentence-snap-bounds`, commits 1c9bf72 red / 49cd314 fix).** Backward never-mid-sentence rule bounded by `backward_limit_s = peak−85 s` + `_RUN_ON_BACKWARD_CAP_S = 30 s` with raw-start fallback; post-snap invariant `setup+0.1 ≤ peak ≤ end−0.1` enforced repair-first (restore pre-snap raw end) then drop-with-WARNING; degenerate single-span index (≥80 % coverage) disables snapping with one WARNING per video; snap-kind eval runner now asserts peak-inside unconditionally. Red-first: pre-fix shipped `[204.7, 294.7]` vs peak 300; degenerate collapsed both candidates to `[0.2, 90.2]`. Amends DECISIONS 2026-08-05 r.(2) — entry added.

**Acceptance**
- [x] Post-snap invariant: `setup_start_s < peak_s < end_s` (with the 0.1 margin) holds for every
      persisted candidate, enforced in code, not only in tests
- [x] Eval fixture: run-on-utterance scenario modeled on the repro geometry, red before / green after
- [x] Eval fixture: no-utterance (single-segment) transcript does not collapse to one [0, 90] clip
- [x] `SCENARIO_FLOOR` raised only with the fixtures added

### Issue 457: laughter/energy double-count biases peak detection toward the aftermath

**Severity: SEV2 — systematic bias against the product's core "clip the setup" promise.**

A loud laugh satisfies both detectors in `ingestion/audio.py:127-133` (`rms >= 0.6` → energy_spike,
weight 1.5; `rms >= 0.3 & zcr >= 0.5` → laughter, weight 2.0), so overlapping events stack up to
3.5× value on the **same samples** in `clip_engine/window.py`. The loud reaction — the aftermath —
becomes the most peak-shaped thing in the timeline; the 75 s backward look is the only counterweight.

**Fix direction (CHECK phase):** de-overlap event classes at emission (a sample contributes to one
class) or cap the composite per-sample; re-run the eval geometry suite + a fixture where the setup
precedes a loud reaction and the peak must not land on the reaction alone.

**Status: DONE 2026-08-13 (W2 Batch A, branch `fix/issue-457-460-window-signals`, commit 734cb13).** One event class per sample at emission (laughter, the stricter detector, excludes its frames from the energy mask) + `np.maximum(laugh, energy)` at consumption as defense-in-depth for persisted timelines; retention stays additive, silence subtractive. Red-first fixture `setup_before_loud_reaction`: summed stacking put the peak at 67.0 on the reaction; max-capped keeps the punchline complex with the setup at the joke's lead-in. The three pre-existing laughter fixtures kept their pinned expectations unchanged.

**Acceptance**
- [x] One sample contributes to at most one event class (or a documented per-sample cap)
- [x] Fixture: laughter-after-joke timeline — peak lands within the joke+reaction complex and the
      setup window still opens at the joke's setup (red/green documented)
- [x] `docs/CLIPPING_PRINCIPLES.md` unchanged or updated to match the actual weighting story

### Issue 458: a silence-only timeline fabricates clip candidates

**Severity: SEV2 — phantom clips from videos with no positive signal at all.**

The composite array is 0 everywhere except −0.5 inside silences (`clip_engine/window.py:13`). The
flat 0 region **between** two silences is then a local maximum with prominence 0.5 —
`find_peaks(prominence=0.5)` emits it as a peak. Repro (rerun): a timeline containing only two
silences yields 2 candidates with peaks at 155 s and 255 s from literally nothing.

**Fix direction (CHECK phase):** require positive evidence for a peak (e.g. minimum absolute
height > 0, or skip-with-reason when the composite has no positive mass) — and make the skip
reason honest (`derive_skip_reason` currently cannot express "no positive signal").

**Status: DONE 2026-08-13 (same lane, commit 921fa3f).** `_PEAK_MIN_HEIGHT = 0.25` passed as `height=` alongside `prominence=0.5` (scipy ANDs absolute with relative); validated post-457 — weakest genuine fixture peak is 1.35. New truthful skip reason `no_positive_signal` checked before the retention branch (the old fallback claimed "analytics not yet available" — false). Red-first fixture `silence_only`: two silences fabricated a candidate at peak 155.0.

**Acceptance**
- [x] Silence-only (and all-zero) timelines produce zero candidates and a truthful skip reason
- [x] Eval fixture added; `SCENARIO_FLOOR` raised with it

### Issue 459: `find_peaks` cannot see a peak in the first or last signal sample

**Severity: SEV2 — a retention spike at the video's start/end (the ground-truth signal, weight
3.0) is invisible to candidate extraction.**

`scipy.signal.find_peaks` never reports array endpoints as peaks. A retention_spike inside the
first/last 0.5 s bucket (`clip_engine/window.py:42`; spikes have a ~2-sample footprint) produces
no candidate — cold-opens with instant retention pops are exactly the content this misses.

**Fix direction (CHECK phase):** pad the composite array (standard practice) or explicitly test
endpoints; verify interaction with the 75 s backward window at t≈0.

**Status: DONE 2026-08-13 (same lane, commit 70c1b72).** Composite padded one sample per side with `min(0, signal.min())` (never −inf — corrupts prominence ordering); endpoint peaks nudged one sample inward so `setup < peak < end` hold at t=0 and t=duration. Red-first fixtures `endpoint_spike_first`/`endpoint_spike_last`: both yielded zero candidates pre-fix.

**Acceptance**
- [x] Fixture: retention spike in the first sample and in the last sample each yield a candidate
- [x] No regression in the existing 24 scenarios

### Issue 460: setup fallback picks the EARLIEST energy spike in the window, docstring says "nearest"

**Severity: SEV2 — systematically over-long setups on silence-free segments; code contradicts its
own contract.**

`_find_setup_start` (`clip_engine/candidates.py:140-173`): silence fallback uses `max(...)` (most
recent — correct); the energy-spike fallback uses `min(...)` (`:170`) — the **earliest** spike in
the 75 s lookback — while the docstring (`:146`) promises the "nearest". One of the two is wrong;
behaviorally the earliest-spike choice drags setups toward the window edge on energetic segments.

**Fix direction (CHECK phase):** decide the intended semantics (most-recent spike before the peak
mirrors the silence rule), fix code or docstring accordingly, and pin with a unit test whose
timeline has two spikes in-window.

**Status: DONE 2026-08-13 (same lane, commit 42e9e3e).** `min()` → `max()` — the setup fallback takes the most recent in-window energy-spike start, mirroring the silence rule; settled 2-docstrings-vs-1-line (module contract + function docstring both said nearest/most-recent). Red-first: two-spike unit test obtained 20.0, expected 60.0. No existing fixture exercised the multi-spike energy fallback, so zero expectation churn.

**Acceptance**
- [x] Code and docstring agree; unit test with two in-window spikes pins the chosen semantics
- [x] Eval geometry suite green; any changed scenario expectations justified in the issue

### Batch B — Scoring & prompts (461–465)

### Issue 461: `score_candidates` trusts the model's response — unvalidated principle, unguarded floats, silent full-batch discard

**Severity: SEV2 — three response-handling defects in the one function that decides what ships.**

1. `clip_engine/scoring.py:505` writes the model-emitted `principle` with **no registry check**
   (`analysis/video_context.py:271` drops unknown principles; `knowledge/clip_explain.py:199`
   raises — the primary scorer alone trusts the model). CLAUDE.md requires every score to cite a
   named principle; here that is enforced only by the prompt.
2. `:502-504` — `float(hit.get("score", 0.5))` / `float(_dna)` are unguarded; a non-numeric value
   raises `ValueError` out of `score_candidates` and fails the **whole generation task**, unlike
   every other malformed-response path in the file which degrades gracefully.
3. A model that emits `"index": "0"` (string) fails the `item["index"]`-keyed lookup shape at
   `:491-495` — every LLM score is silently discarded and all clips fall back to signal-only,
   logged as a normal fallback.

**Fix direction (CHECK phase):** validate against `_PRINCIPLES` with a safe default, wrap numeric
coercion per-item (bad item → cold-start annotate that item only), coerce `index` via
`int(...)`-with-guard. Consider structured output (`output_config.format`) as video_context
already uses — that removes the whole class.

**Status: DONE 2026-08-13 (W3 Batch B, branch `fix/issue-461-462-scoring-hardening`, commits 57e1306 + 9ca402f).** Per-item validate-and-degrade layer (int()-guarded index, guarded float coercion → per-item cold-start, registry-checked principle → safe default + warning), THEN structured output adopted per owner `[DEC]` (supersedes the 2026-06-29 deferral): root-object `{"scores": [...]}`, principle enum BUILT FROM `_PRINCIPLES`, `additionalProperties: false`; extract_json_block + validator retained as defense-in-depth (refusal/max_tokens are not schema-guaranteed); numeric clamps stay in code. SDK `anthropic==0.105.2` supports `output_config` natively (signature-verified). Red-first: off-registry principle persisted verbatim; `ValueError: could not convert string to float: 'high'` killed the task; string `"0"` index silently discarded every LLM score.

**Acceptance**
- [x] Off-registry principle → replaced with a valid default + warning log (unit test)
- [x] Non-numeric score/dna_score on ONE item degrades that item only (unit test)
- [x] String `index` values are accepted (unit test)
- [x] No behavior change on well-formed responses (existing `test_scoring.py` green)

### Issue 462: scoring's `[BEFORE]` transcript context keeps the wrong end

**Severity: SEV2 — the LLM judges "does this start at the setup?" without the sentence adjacent
to the cut.**

`_transcript_context` (`clip_engine/scoring.py:235+`) caps `[BEFORE]` at 200 chars but truncates
keeping the **head** of the 60 s pre-window — i.e. text ~60 s away — and drops the text
immediately before the clip start, which is precisely what the setup judgment needs. Repro
(rerun): with a long pre-window, the emitted `[BEFORE]` contains t=0–15 s text and omits the
sentence at the cut.

**Fix direction (CHECK phase):** truncate keeping the tail for `[BEFORE]` (and audit `[AFTER]`
for the mirror-image bug: it should keep its head).

**Status: DONE 2026-08-13 (same branch, commit bd5a1a6).** `_gather` grew `keep='head'|'tail'`; `[BEFORE]` tail-keeps so the sentence adjacent to the cut reaches the model (red proof: head-keep emitted t≈0–15 s FAR_TEXT and dropped NEAR_CUT). **Audit finding: `[AFTER]` had NO mirror bug** — already head-keeps correctly; now pinned by regression test. Section caps 200/250/150 unchanged and pinned.

**Acceptance**
- [x] Unit test: `[BEFORE]` ends with the words nearest the cut; `[AFTER]` starts with the words
      nearest the clip end
- [x] Prompt-size caps unchanged

### Issue 463: `video_context` prompt-security diverges from the Issue-224 rules

**Severity: SEV2 — creator-authored and model-authored text reach trusted positions.**

(a) `analysis/video_context.py:321-330` places creator-authored identity text (niches, mission,
free-text) in a **system** block. `dna/brief.py:92-115` documents the opposite rule for the same
data ("stated_identity is creator-authored… must NOT go in the system role", Issue 224) and routes
it through `wrap_untrusted` in the user turn. (b) The context pass's model-authored summary —
derived from the untrusted transcript — is re-used downstream content-unwrapped (second-order
injection surface).

**Fix direction (CHECK phase):** align video_context with the brief's placement rules; wrap the
summary where it is re-consumed. Check DECISIONS Issue 224/371 rulings for the exact contract.

**Status: DONE 2026-08-13 (W3 Batch B, branch `fix/issue-463-prompt-placement`, commits ef41d0a red / e1e8b81 fix).** Creator identity moved to `wrap_untrusted('creator_stated_identity', …)` in the user turn; video_context block 2 is DNA-only via `dna_system_block` (PROMPT_VERSION 2→3); clip_metadata's system Block 3 deleted — the model-authored summary rides `wrap_untrusted('video_context_summary', …)` in the user turn. Structural tests mirror the 224 pattern; NEW byte-identity pins: system blocks identical with/without identity and with/without summary. Scoring leg verified already wrapped (scoring.py:347-350) — closed by verification. Issue-371 third block untouched.

**Acceptance**
- [x] Creator-authored text appears only via `wrap_untrusted` in the user turn (structural test,
      mirroring `tests/test_prompt_safety.py` patterns)
- [x] Model-authored summary wrapped at every downstream consumption site
- [x] Prompt-cache behavior unchanged (cache-floor gates still hit)

### Issue 464: Principle 10 "Native length" is computed, stored, surfaced — and never used

**Severity: SEV2 — a documented product promise ("the engine learns YOUR optimal length") is
structurally unimplemented.**

`dna/builder.py` computes `optimal_clip_len_s` + `best_source_region`; `models.py:633-634` stores
them; `routers/creators.py:83-84` surfaces them — and **no clip-geometry code reads either**.
Every creator gets the fixed 30/90 band (`MIN_CLIP_S`, `CLIP_TARGET_MAX_S`). The only consumer is
prose inside the DNA brief, so the scorer may cite Principle 10 while geometry cannot honor it.

**Fix direction (CHECK phase):** either wire `optimal_clip_len_s` into the target-length clamp
(bounded by platform limits) — or descope: remove the fields and the principle's "native length"
claim from docs/UI. An explicit `[DEC]` either way.

**Status: DONE 2026-08-13 (W3 Batch B, owner ruled OPTION A — wire; branch `fix/issue-464-native-length-wire`).** Estimator fixed to median watch duration over top SHORTS only (red proof: mixed pool yielded 167 s from a 42 s-Shorts creator); wiring: `effective_max_len_s = clamp(optimal × 1.25, 45 s, 90 s)` derived once in `score_and_rank` and threaded to the sentence-snap and LLM-window clamps; max-clamp ONLY, never stretches; MIN_CLIP_S/POST_PEAK_S untouched. Cold start byte-identical (all 31 pre-existing scenarios untouched and green). Stale prod rows clamp harmlessly to the 90 s constant until the next DNA build. New eval fixture `native_length_clamp` (kind: merge).

**Acceptance**
- [x] `[DEC]` recorded in `docs/DECISIONS.md`
- [x] If wired: geometry test showing a creator with `optimal_clip_len_s=45` gets ≈45 s targets;
      if descoped: fields removed + docs/PRINCIPLES updated

### Issue 465: preference rerank overwrites the persisted composite score

**Severity: SEV2 — the stored `score` silently changes meaning once personalization activates.**

`rerank_with_preference` writes the blend back into `clip.score` and commits
(`clip_engine/ranking.py:214`), so the persisted value is no longer the DNA/LLM composite that
`dna_match` was deliberately separated from (Issue 103 #5). Downstream, generate-more's recap
candidates read `Clip.score` as if it were the fit score (`routers/clips.py:2415`). Related
context (register, not re-filed): `append_ranked_clips` skips the blend by design, so one video
holds blended ranks 1–12 and raw ranks 13+ on the same column.

**Fix direction (CHECK phase):** persist the blend separately (e.g. `blended_score` or recompute
at read time) and keep `score` stable as the fit composite; migrate readers deliberately.

**Status: DONE 2026-08-13 (W3 Batch B, branch `fix/issue-465-blended-score`, commits d5ca655 red / 64985ee fix).** Migration 0059 adds nullable `clips.blended_score`; `clip.score` is now the immutable DNA/LLM fit composite (rerank writes/sorts by the new column; append path writes fit + NULL uniformly — the registered mixed-column behavior resolved). NO backfill: historical fits were destroyed by the overwrite. Shared `preference.model.blend_scores` helper = Issue 475's parity interface, pinned by test. Red proofs on main: rerank mutated fit 0.9→0.45; efficacy's `dna_composite` ingested the blend. Reader map verified by structural test — no reader reads `blended_score` today.

**Acceptance**
- [x] `clip.score` semantics documented and stable across the rerank
- [x] Recap/generate-more readers explicitly choose fit vs blended (test each)
- [x] Migration/backfill decision recorded if a column is added

### Batch C — Pipeline & render (466–471)

### Issue 466: overlay-band detection is broken on every source longer than ~500 s

**Severity: SEV1 — the Issue-448 fix cannot work on the livestream sources superchats occur on;
spans are computed over a scrambled, time-dilated timeline.**

Three compounding defects when `detect_overlay_spans` runs on a > 120 s source (which routes to
`_sample_by_seeking`): (1) frames are named `f'{i:03d}.png'` (`clip_engine/camera_region.py:568`)
and read back `sorted(glob('f*.png'))` (`:619`) — **lexicographic**, so past 999 samples the stack's
temporal order is scrambled (repro rerun: `f1000` sorts to index 101). At the 0.5 s cadence
(`overlay_bands.py:174`) any source > ~500 s crosses that line; the 1617 s drill video needs 3234.
(2) `step = duration_s/len(stack)` (`overlay_bands.py:214`) assumes a complete stack, but the
sampler silently skips failed frames (`camera_region.py:588-589`) and truncates at the 240 s budget
(`:556-566`, `overlay_bands.py:81`) — surviving indices map to **inflated times** (a real 300–330 s
banner maps to ~606–668 s) and the un-sampled tail is never scanned. (3) Every test monkeypatches
`_sample_gray_frames` with dense complete stacks, so nothing catches any of this (the real-frame
fixture tests cover only the pure band-detection functions).

**Root cause:** the seek-sampler was built for camera-region consensus (≤ 9 windows × few frames,
n always ≪ 999, complete-by-construction) and reused for whole-source scanning without revisiting
its assumptions.

**Fix direction (CHECK phase):** zero-pad names to the actual sample count (or sort numerically),
carry per-frame timestamps through the stack instead of inferring from index, and make truncation
explicit (either extend the budget for this caller or record the scanned range and mask only
within it). Then re-verify against the real 448 measurements if any drill artifact survives.

**Status: DONE 2026-08-13 (fast-tracked solo as PR #92, merged + deployed ahead of the W4 wave).** Timed-sampler redesign: `_sample_by_seeking` returns the captured ordered `[(t_s, path)]` list + `scanned_until_s` (glob rediscovery deleted — the captured list is the ordering authority); spans derive from real timestamps with runs split on >3×interval t-gaps; truncation explicit via `scanned_until_s` in the v2 doc; `OVERLAY_SPANS_VERSION=2` invalidates every v1 doc on read (prod had zero non-NULL rows); `OVERLAY_BAND_TIMEOUT_S=600` config. Camera-region consensus byte-identical via wrapper. Red proofs: capture #1000 read back at index 101; a real 300–330 s banner reported at 713.8 s. Integration test through the REAL sampler: 600 s testsrc + drawbox, 1200 seeks crossing the 999 boundary, both spans ±1 s (run live, 34.7 s). Live backfill drill against the 1617 s source pending the prod flag flip (operator).

**Acceptance**
- [x] Frame read-back order proven correct for n > 999 (unit test with synthetic names)
- [x] Span times derived from real timestamps, not `duration/len(stack)`; truncation is explicit
      in the stored spans (scanned-range field or log + metric)
- [x] An integration-shaped test drives `detect_overlay_spans` through the REAL sampler on a
      generated long synthetic source (testsrc + drawtext band), asserting span times ±1 s
- [x] `videos.overlay_spans_jsonb` version bumped so stale spans are invalidated on read

### Issue 467: the Punch-in toggle produces an invalid ffmpeg filter — every render fails while enabled

**Severity: SEV1 — a shipped UI feature bricks the creator's render loop with a generic error.**

`_punch_in_filter` (`clip_engine/render.py:453-461`) emits a time-dependent expression in crop's
`w`/`h`. ffmpeg evaluates crop `out_w`/`out_h` **once at filter-configuration time**, where `t` is
NaN → "Error when evaluating the expression … Error reinitializing filters!". Repro (rerun on
ffmpeg 8.1.2, production-shaped chain): rc=234; control with the same `t` expression in crop `x`/`y`
runs rc=0 — mechanism pinned, version-stable semantics. Reachability: `zoom_on_peak` flows from
`CaptionStylePanel.tsx` and the brand kit (`routers/creators.py:255-256`), the kit is seeded onto
every generated clip (`worker/tasks.py:3630-3638`) and merged on every render
(`routers/clips.py:915-917`); `render_clip` classifies the failure transient, burns 3 retries, and
the UI shows "Render failed." with nothing pointing at the checkbox. Only argv-string tests exist
(`tests/test_render.py:489-506`) — the filter has never been executed by any test. The feature
(Issue 184) has never worked.

**Fix direction (CHECK phase):** implement punch-in with config-time-legal parameters (e.g.
constant `w/h` + animated `x/y`, or `zoompan`/`scale` approaches), and add the real-ffmpeg
execution test that would have caught this (see Issue 478's lane).

**Status: DONE 2026-08-13 (W4 render lane, branch `fix/issue-467-469-478-render-lane`) — live worker-path render rides the W6 fresh upload.** Punch-in rebuilt as animated-`scale eval=frame` + constant centered crop (ffmpeg evaluates crop w/h ONCE at config — the Issue-184 chain therefore failed rc=234 with t=NaN on every render; doc-verified across ffmpeg 5.1→8.1). Executes rc=0 through the real `render_clip_file` with the zoom verified by SSIM against a static-source control (same-render SSIM on an animated source proves nothing — caught in build). Filter-config stderr signatures now classify as ValueError = terminal: no more 3-retry burn on deterministic argv failures.

**Acceptance**
- [x] Punch-in chain executes rc=0 through real ffmpeg in a test (testsrc source)
- [x] Visual behavior verified on one real render (frame-extract: zoom present around peak)
- [x] A render with `zoom_on_peak=true` completes end-to-end in the worker path

### Issue 468: `POST /clips/{id}/render` lacks the pending-edit 409 guard its siblings have

**Severity: SEV2 — re-render races the clean/edit artifact swap.**

The cuts/trim/clean endpoints all 409 when an edit job is pending; the plain re-render endpoint
(`routers/clips.py:844` area) does not, so a re-render fired mid-clean can interleave with
`/clean/confirm`'s `render_uri` swap and clobber or resurrect artifacts.

**Fix direction (CHECK phase):** add the same guard; audit the confirm path for a
compare-and-swap on the URI it replaces.

**Status: DONE 2026-08-13 (W4 clips-API lane, branch `fix/issue-468-447-clips-api`).** The exact sibling `pending_clean_or_edit` 409 added to plain re-render; `/clean/confirm` AND `/clean/discard` are now compare-and-swap conditional UPDATEs (rowcount-0 → 200 noop; discard's noop never touches storage). Newly found riding defect fixed: a double clean left `cleaned_render_uri == render_uri` and discard deleted the LIVE render — guarded + red-proven; the false 'R2 lifecycle prefix' docstring corrected. Orphaned-original cleanup hooked into the CAS'd confirm at integration (L143).

**Acceptance**
- [x] 409 test mirroring the existing sibling-endpoint tests
- [x] Confirm path verified race-safe (test or documented DB-level guarantee)

### Issue 469: two duration authorities — audio-derived clamp vs container-derived hard reject

**Severity: SEV2 — a duration mismatch becomes a PERMANENT render failure with no retry.**

`sentence_snap` clamps `end_s` to the librosa 16 kHz audio duration (`timeline.duration_s`,
capped at `AUDIO_ANALYSIS_MAX_DURATION_S=14400`); `clip_engine/render.py:517-526` hard-rejects
`end_s >` the ffprobe **container** duration with a `ValueError` that `render_clip` treats as
terminal. Any source where audio duration exceeds container duration (VFR, stream VODs, container
metadata quirks) yields clips that can never render. The dead word-level path documents exactly
this hazard (`candidates.py:357-363`); the live path doesn't handle it.

**Fix direction (CHECK phase):** pick ONE authority at candidate-persist time (ffprobe is what the
render enforces), clamp there, and keep the render check as a safety net that logs loudly instead
of failing permanently for sub-second overshoots.

**Status: DONE 2026-08-13 (same lane).** ONE duration authority: ingest ffprobe ALWAYS overwrites `video.duration_s` (the YouTube-API integer seed was never corrected before); `container_duration_s` threads generate_clips → score_and_rank → extract/snap with the clamp INSIDE the W2 invariant-repair tail (payoff-cutting clamps hit repair-or-drop); render's hard reject demoted to `_DURATION_OVERSHOOT_EPS_S = 1.0` (sub-second → clamp + warn; ≥1 s → still terminal). >14400 s sources documented at the constant. REST generate/regenerate threading added at wave integration.

**Acceptance**
- [x] Candidates clamped against the same duration the render enforces
- [x] Sub-second overshoot at render → clamped + logged, not permanent failure (unit test)
- [x] > 14400 s source behavior documented (analysis cap vs container)

### Issue 470: trim/clean leaves stale clip geometry behind the swapped artifact

**Severity: SEV2 — captions/crops/duration/transcript compute against a window that no longer
matches the delivered video.**

`_edit_clip_async` (`worker/tasks.py:2867,2901-2911`) writes only `cleaned_render_uri`;
`clip.start_s/end_s/setup_start_s` keep the pre-trim geometry. After `POST /clips/{id}/clean/confirm`
swaps it into `render_uri`, `_clip_duration_s` (`routers/clips.py:1578`), the `/transcript` origin,
and any re-render/caption pass still use the original window.

**Fix direction (CHECK phase):** persist the effective geometry (cut list is already stored —
derive and store effective duration + segment map at confirm time) and route every reader through
it. Interacts with Issue 465 (score semantics) and 468 (race guard) — sequence them.

**Status: DONE 2026-08-13 (W4 final lane, branch `fix/issue-470-effective-geometry`, built on the integrated wave as sequenced — needed 465's score semantics, 468's CAS, 446/471's erasure).** Effective geometry is PERSISTED, not derived (migration 0061, two nullable JSONB columns): `pending_geometry_jsonb` written by the worker in lockstep with `cleaned_render_uri` (the clean path's cut list never exists outside the task, and Issue 391's edit-document clear at confirm kills the edit path's copy — read-time derivation was impossible twice over), and `effective_geometry_jsonb` written at confirm INSIDE the 468 CAS, composed across repeated trims. Duration/transcript/trim-validators read the delivered record; re-render of a confirmed-clean clip stays available (it is the only style-change path) but clears the record and reports `discarded_edits: true`, with `has_baked_edits` on ClipOut so the UI can warn first — honest discard over silent resurrection. 14 unit + 2 integration-lane tests; red proofs: duration readers asserted 40.0 == 20.0 pre-fix.

**Acceptance**
- [x] After a trim+confirm, `/transcript`, duration, and captions all reflect the delivered video
      (integration-lane test)
- [x] Re-render of a confirmed-clean clip does not resurrect the pre-trim window

### Issue 471: right-to-erasure misses exports, extracted audio, and recap artifacts

**Severity: SEV2 — compliance-relevant storage survives account deletion (beyond the known
Issue-446 `clips/` gap).**

`erase_creator` (`routers/auth.py:486-495`) deletes known prefixes, but: GDPR export bundles,
extracted-audio WAVs (`audio_uri`), and recap render artifacts are written under keys/prefixes the
erasure never touches (audit each write site in `worker/tasks.py` / recap path). Issue 446 covers
`clips/{clip_id}.mp4`; this issue is the sweep for everything else.

**Fix direction (CHECK phase):** inventory every R2 write key in the codebase, converge on
creator-scoped prefixes, make `erase_creator` enumerate from the DB rather than prefix-guessing.
Fold into 446's build if that lands first.

**Status: DONE 2026-08-13 (folded into 446's build as directed).** The write-key inventory is CODE now: `worker/erasure.py` KEY_PATTERNS (10 grep-verified patterns) with a sync-gating test; `erase_creator` uses per-URI enumeration ∪ constructed keys (closing renders, `_clean`/`_edit` variants, extracted WAVs, recap renders, and the GDPR export bundle itself — plus a new `exports/{creator_id}/` sweep for superseded bundles) with the old dead `clips/{cid}/` prefix and its known-gap comment deleted. Integration-lane completeness test = the acceptance. `docs/COMPLIANCE.md` rewritten accordingly.

**Acceptance**
- [x] Written-key inventory in the issue (grep-verified)
- [x] Erasure test (integration lane, mocked storage) asserting every write site's key is covered
- [x] `docs/COMPLIANCE.md` updated

### Batch D — Learning loop (472–475)

### Issue 472: feedback `skip` silently retracts the creator's latest real label

**Severity: SEV1 — the shipped Trim→Skip UI flow erases keep labels from the training set while
the pile keeps the verdict; the 444 invariant ("rating and pile cannot disagree") is violated.**

`models.py:185-191` omits `skip` from `TRIAGE_BY_FEEDBACK_ACTION` ("skipping is not a verdict") so
`submit_feedback` leaves `clip.triage` unchanged on skip — but still inserts the
`ClipFeedback(action=skip)` row (`routers/review.py:223-244`). `preference/train.py:41` puts skip
inside `_VERDICT_ACTIONS`, so that row **wins the per-clip partition** and drops at the TRAINABLE
filter (`:126`) — retracting whatever label preceded it. The UI makes this the natural flow:
"Save trim" (a keep label) deliberately does not advance (`YourCall.tsx:132`); the always-visible
Skip button (`:204`) is the obvious next click. Result: feedback rows trim(t1)+skip(t2) → clip
contributes NO label, `triage` stays `kept`, UI flashes success. The skip-as-retraction design was
built for `PUT /triage`'s back-to-pending transition (`train.py:103-109` documents it) — the
feedback surface reuses the action with different intent.

**Fix direction (CHECK phase):** distinguish "advance past this clip" (UI navigation — should
write NO feedback row) from "retract my verdict" (the triage-pending transition). Either stop
POST /feedback accepting `skip`, or exclude feedback-surface skips from the verdict partition.

**Status: DONE 2026-08-13 (W5 learning-loop lane, branch `fix/issues-472-475-learning-loop`, red-first).** `POST /feedback action=skip` is now a pure acknowledged no-op (201, `id: null`, no row, no triage change, no retrain) — navigation and retraction are different verbs; retraction is exclusively `PUT /triage → pending`. UI Skip is pure `onAdvance()` (vitest pins zero network calls). Migration 0062 repairs history: deletes only MASKING skips (kept/dropped clip, no later trainable verdict), preserves superseded and pending-retraction skips, idempotent, would-delete count logged at WARNING pre-delete (drilled on a scratch cluster: 3 of 5 seeded skips deleted, second run 0). The 444 invariant is pinned end-to-end (pile state ⇒ training state).

**Acceptance**
- [x] Trim → Skip in the UI leaves the trim label trained (integration-lane test on the partition)
- [x] PUT /triage → pending still retracts (existing test stays green)
- [x] UI: skip after a same-session rating either warns or is a pure navigation no-op
- [x] The 444 docstring's invariant is true again (test that pile state implies training state)

### Issue 473: retrain debounce watermark is blind to retractions and outcome arrivals

**Severity: SEV2 — the model can keep training on labels the creator retracted.**

The debounce/watermark that decides "new labels since last train" counts only TRAINABLE feedback
rows. A pure skip retraction (which REMOVES a label from the effective set) and a
`performed_well` outcome arrival (which changes sample weights 3×) advance nothing, so no retrain
fires and `load_latest` keeps serving the stale model indefinitely.

**Fix direction (CHECK phase):** watermark on the full verdict-action set + outcome writes, or on
a monotonic feedback/outcome sequence id.

**Status: DONE 2026-08-13 (same lane).** Watermark widened to the full VERDICT_ACTIONS set OR judged outcome writes (`fetched_at > model.updated_at AND performed_well IS NOT NULL`), and — the leg the filed issue missed — `poll_clip_outcomes` now ENQUEUES the countdown-coalesced retrain per affected creator (there was no outcome enqueue site at all). Coalescing pinned; the common feedback path stays one COUNT query.

**Acceptance**
- [x] Skip-only retraction triggers a retrain (test at the debounce layer)
- [x] Outcome arrival triggers a retrain within the debounce window
- [x] No retrain storm: debounce still coalesces (existing behavior pinned)

### Issue 474: personalization threshold honesty — `active=true` at weight 0.0, and two different label counts

**Severity: SEV2 — the UI can claim personalization is on while the blend weight is zero; the
count shown is not the count trained on.**

`preference_weight(label_count)` returns 0.0 at exactly `PERSONALIZATION_THRESHOLD_LABELS` (the
ramp is `(n−T)/T`), while the status surface reports `active=true` at `n ≥ T`. Separately, the
surfaced label count is computed pre-dedup (raw feedback rows) while training counts post-dedup
(one per clip) — a creator who flip-flopped shows a count that can be far above what the model
actually saw. CLAUDE.md: "Personalization threshold is communicated honestly."

**Fix direction (CHECK phase):** one count (post-dedup) everywhere; `active` means `weight > 0`;
show the weight ramp if useful.

**Status: DONE 2026-08-13 (same lane).** `active := weight > 0.0` in the one producer (`_build_personalization_status`); at exactly T labels the API asserts weight 0.0 and active false. Count stays `scorer.label_count` (the fitted post-dedup n — the audit's 'pre-dedup surface' claim was stale, corrected in the brief); Review band copy now says 'clips rated'.

**Acceptance**
- [x] At exactly T labels the UI does not claim active personalization (API test)
- [x] Surfaced count == trained count basis (test)

### Issue 475: efficacy/lift harness diverges from what production trains and serves

**Severity: SEV2 — the offline numbers used to judge the personalization loop are computed on a
different dataset and a different geometry origin than production.**

(a) The efficacy eval set lets `performed_well=True` override skip/format retractions that
`latest_verdict_subquery` honors in training — the harness scores a model on labels production
would have discarded. (b) The Proof-of-Lift panel computes `duration_s`/`setup_lead_s` from
`start_s`, while every other surface uses the `setup_start_s ?? start_s` origin
(`routers/review.py:113-114`) — lift features mis-measure every clip with a distinct setup point.
Related register item: `efficacy.py` hand-copies the production blend formula (C2-13).

**Fix direction (CHECK phase):** share the exact training-set query (it already imports
`latest_verdict_subquery` — extend to the outcome-join semantics), share the origin helper, and
pin blend parity with a test.

**Status: DONE 2026-08-13 (same lane).** `preference.train.training_rows_select()` is the single shared select executed by BOTH `build_and_save` and `load_labeled_clips` (Google Rules of ML #32); `_relevance_for` is action-first so a skip/format retraction excludes the clip even with a good outcome; `LabeledClip.performed_well` carries the stored flag so harness sample weights match train.py exactly (closes register item C2-13's last leg); `clip_engine.edits.clip_origin_s` adopted by the lift query AND the originality fingerprint (sanctioned scope addition). Blend parity was already 465's shared helper — closed by verification citing `tests/eval/test_efficacy.py::test_blend_parity_with_shared_helper`.

**Acceptance**
- [x] Efficacy set == training set for identical inputs (test against the shared query)
- [x] Lift geometry uses the shared origin (test with `setup_start_s != start_s`)
- [x] Blend parity test between `efficacy._blend_scores` and `rerank_with_preference`

### Batch E — Eval & CI integrity (476–482)

### Issue 476: the LLM clip scorer is evaluated end-to-end nowhere

**Severity: SEV1 — the single decision-maker for what ships and which principle it cites has no
behavioral gate of any kind.**

`score_candidates` is the one call that turns candidates into ranked, principle-cited clips. Every
test patches `clip_engine.scoring._ANTHROPIC`; no eval scenario invokes the function; the nightly
live-LLM lane (`.github/workflows/llm-e2e-nightly.yml`) covers titles/hooks/DNA-brief/cache only;
mutmut touches scoring.py weekly and non-gating (`mutation.yml` — `mutmut run || true`). A prompt,
rubric, or model change that systematically prefers aftermath windows or ignores the DNA brief
passes 100 % of every gate in the repo. (This is simultaneously a limitation of this audit — there
was no harness to run.)

**Fix direction (CHECK phase):** a scored-fixture lane — recorded real responses (goldens) for
parser/pipeline regressions + a small nightly live set with behavioral assertions (setup-window
preference over aftermath-window on constructed pairs, principle ∈ registry, dna_score ordering on
a fixed brief). Research current LLM-eval practice (LLM-judge vs fixed assertions) in Phase 1.

**Status: DONE 2026-08-13 (W5 LLM-eval lane, branch `fix/issues-476-480-llm-eval`).** Two lanes per the tranche-2 `[DEC]`: (1) GOLDENS — real recorded Anthropic response bodies (happy-path + a real stop_reason=max_tokens truncation) replayed through `anthropic.types.Message.model_validate` into the REAL `score_candidates` parse/annotate path in the unit lane, each pinned to sha256 of the canonical `_OUTPUT_SCHEMA` + the scoring model id so a contract change reds CI until a ~$0.20 re-record; (2) NIGHTLY — code-graded behavioral lane (NO LLM judge; all criteria are objective orderings), majority-of-3, ~$1/night: setup-vs-aftermath ordering, strict shape/refusal detection, dna_score on/off-brief ordering. PROVEN LIVE pre-merge: 3/3 green with margins +0.84–0.89, and an inverted assertion demonstrated red. CI guard pins the nightly's pytest path.

**Acceptance**
- [x] Recorded-response goldens exercise the REAL `score_candidates` parse/annotate path in CI
- [x] A nightly behavioral eval hits the live model with ≥ 3 assertion classes and posts a status
- [x] `docs/DECISIONS.md` entry for the chosen eval design

### Issue 477: eval-runner assertion integrity — dead scenarios, vacuous minimums, permissive matching

**Severity: SEV2 — the "24 adversarial scenarios, all pass" gate materially overstates itself;
two scenarios assert nothing.**

Verified: (a) `injection_in_transcript.yaml` — its `expected` keys (`injection_window_score_max`,
`clean_window_score_gte_injected`) are read by **no code**; `input.injected_transcript_segment` is
never fed anywhere; no `min_candidates` → the geometry branch asserts nothing; it passes vacuously
and counts toward the floor and the landing-page claim. (b) `false_peak_single_spike.yaml` —
`min_candidates: 0` is `assert len >= 0`. (c) No upper-bound-on-count assertion exists in the
geometry branch — a spurious-clip flood is invisible to 13 of 15 geometry scenarios. (d) Expected
candidates match by nearest peak (`tests/test_clip_engine.py:575-580`) — two expectations can match
the same candidate; spurious extras are never rejected. (e) The core `all_setup_before_peak`
invariant is opt-in; 10 of 15 geometry scenarios don't opt in. (f) `SCENARIO_FLOOR` guards
deletion but nothing stops it being lowered in the same commit that deletes a fixture.

**Fix direction (CHECK phase):** make the runner REJECT unknown/unread expectation keys (that one
change would have caught (a) at authoring time); add `max_candidates` support and set it in the
flood-shaped scenarios; make setup-before-peak unconditional; one-to-one candidate matching;
either implement injection_in_transcript against the real scorer path (needs 476's lane) or
rewrite it to assert what the cold-start path CAN prove; pin the floor value in a second location
(e.g. transparency test) so lowering it requires two deliberate edits.

**Acceptance**
**Status: DONE 2026-08-13 (W1 of the beta close-out).** `_validate_expected_keys` registries
(top-level + per-candidate + window subkeys + unknown `kind`), `_match_unique` one-to-one
matching in all four kinds' matchers, unconditional setup-before-peak (the opt-in key is now
REJECTED — verified empirically that all 15 geometry scenarios already satisfied the invariant
via the `candidates.py:364` clamp, so zero fixture churn), and the floor pinned in a second file.

**Acceptance**
- [x] Runner fails on any expectation key it does not read — six proof tests: typo'd top-level
      key, typo'd candidate key, unknown kind, typo'd window subkey, opt-in-key rejection, and
      one-candidate-two-expectations double-satisfaction
- [x] Both dead scenarios assert something real (floor maintained at 23, 24 files):
      `injection_in_transcript` rewritten as `kind: snap` — instruction text spanning the setup
      demands start 0 / end 600, boundary must land at the sentence start [70.9, 71.5] chosen by
      TIMINGS (the deterministic path treats text as data; the LLM behavioural leg is 476's
      lane); `false_peak_single_spike` now asserts `max_candidates: 0` (was `min >= 0`, vacuous)
- [x] `max_candidates` asserted in 4 flood-shaped scenarios (false_peak 0, laughter 2,
      multi_peak 2, overlapping_peaks 1 — the last was previously blind outside its window);
      setup-before-peak unconditional
- [x] Landing-page claim re-verified by `test_eval_transparency.py`, which now also pins
      `SCENARIO_FLOOR == 23` so lowering the floor must touch two files in one diff

### Issue 478: no real-media verification lane — empty marker, no boundary ffprobe, orphaned real fixtures

**Severity: SEV2 — an off-by-N in the actual cut, seek, or filter chain is invisible to the whole
suite; the only real frames from a live defect are consumed by nothing.**

Verified: the `render-env` marker is registered (`pytest.ini:17`) but **zero tests carry it** —
the declared real-media lane is empty (measured: 3217 deselected / 0 selected). No test anywhere
ffprobes a rendered clip's duration/PTS against the requested `[start_s, end_s]` — `test_render.py`
asserts argv strings (one test asserts nothing at all); the integration-lane setup-start test mocks
`render_clip_file`. `tests/fixtures/reframe_seats/` (12 real frames from Issue 450 — the only
surviving repro once the 2026-08-13 source purge lands) is referenced by zero tests; the 450
regression test hand-supplies the `SpeakerMapping` the fixture exists to prove. Nothing in CI
asserts the ffmpeg-gated tests actually executed (the apt install is best-effort `|| warning`).

**Fix direction (CHECK phase):** populate the lane: (1) a testsrc-source render through the REAL
`render_clip_file` asserting ffprobe duration ≈ `end−start` ±1 frame and stream geometry; (2) the
punch-in and sendcmd chains executed for parse+init (catches Issue 467's class); (3) wire
`reframe_seats` through the real mouth-motion → mapping path; (4) a CI step that fails if the
ffmpeg-gated tests were skipped (count assertion or `--strict-markers`-style guard).

**Status: DONE 2026-08-13 (legs 1–2 W4 render lane; leg 3 W5 fixtures lane).** Marker renamed `render_env` (the hyphen could never be applied as a decorator — the root defect that kept the lane empty), addopts exclusion fixed, 3 real-media tests (missing ffmpeg FAILS, never skips), and a hard CI step (unconditional apt install + ≥3-collected floor) pinned in `test_ci_config.py`.

**Acceptance**
- [x] `pytest -m render-env` selects ≥ 3 tests locally and in CI
- [x] Boundary ffprobe test red/green demonstrated by mutating `-ss` handling
- [x] `reframe_seats` fixtures consumed by a test that computes the mapping from the frames — `tests/test_render_env_reframe.py` runs real BlazeFace → tracks → mouth energy → mapping → speaking-span over the 12 PNGs at production config (CI provisions mediapipe + the pinned model, hard-fail; render_env floor 3→7). Scope caveat: the two-seat hold + energy ordering are NOT provable from the 480 px fixture (the left face is undetectable at production confidence; lowering it produced phantom tracks) — covered by the synthetic Issue-450 tests; a FULL-RESOLUTION re-freeze is queued for the W6 fresh upload
- [x] CI fails when the ffmpeg lane silently skips

### Issue 479: the gates that never ran — per-module coverage floors, diff-cover, and the pre-push hook

**Severity: SEV2 — Issue 269's headline controls have enforced nothing in CI since 2026-06-23,
while printing "All runnable gates passed".**

`run_layer0.py:527` unconditionally deletes `docs/assessment/_coverage.xml` at the end of EVERY
invocation; `ci.yml` runs `--gates coverage --require-coverage` and
`--gates module_coverage,diff_cover` as **two invocations** (`:213-225`). Invocation 2 finds no
XML → both gates return "skipped" → exit 0. Confirmed in the live CI log of a run merged
2026-08-12 ("module_coverage skipped coverage.xml not found … All runnable gates passed"). The
global 83 % floor IS enforced (invocation 1) — what never ran is the clip_engine 91 / preference
88 floors and the 80 % changed-line patch gate. The 2026-07-20 fix (`2279720`) fixed only the
single-invocation local path. Compounding: `scripts/ci_local.sh` (the Layer-1 pre-push gate)
requires Postgres unnecessarily for the unit lane and the hook is not installed on this box, so
the local backstop doesn't run either.

**Fix direction (CHECK phase):** either single-invocation the CI job (`--gates
coverage,module_coverage,diff_cover --require-coverage`) or stop deleting the XML in main() (move
cleanup to the producing gate's start). Add `--require` semantics to module_coverage/diff_cover in
CI so "skipped" fails there. Fix ci_local.sh's precondition; install the hook.

**Status: DONE 2026-08-12 (PR #86, merged; drill PR #87 closed unmerged).** Fix took BOTH
directions: cleanup moved to the producing gate's start (order-independent) AND the CI job
single-invocationed with a new generic `--require coverage,module_coverage,diff_cover` flag.
`tests/test_ci_config.py` pins the shape. Rider: root `.nvmrc` (22.17.1) + PATH honor in
`ci_local.sh`, because the freshly-installed hook's first run reproduced the node-26 jsdom
phantom and would otherwise fail every push.

**Acceptance**
- [x] CI log shows module floors + diff-cover actually evaluating — PR #86's coverage job:
      `coverage ok 84.18 · module_coverage ok {clip_engine 93.03/91.0, preference 90.24/88.0,
      crypto/limiter/auth 100/99} · diff_cover ok`
- [x] A deliberate floor-violation branch fails the job — drill PR #87 (clip_engine floor 99.9):
      `module_coverage fail ['clip_engine: 93.0% < floor 99.9%'] → GATES FAILED → exit 1`,
      log excerpt recorded on #86
- [x] "skipped" is a failure in CI context for these two gates — `--require` flag; red-path
      proven locally (missing XML → exit 1 with explicit FAILs)
- [x] `ci_local.sh` runs the unit lane with Redis only (2977 green locally, no Postgres); hook
      installed via `scripts/setup_hooks.sh` (`core.hooksPath → .githooks`) and verified live —
      it blocked a real push before the node pin

### Issue 480: preference-model evaluation gap — the DNA fixture proves a sort, the rerank has no eval

**Severity: SEV2 — "ranking reflects DNA + preference" (a Phase-4 checklist line) is not proven by
any test.**

`rank_candidates` is a pure score sort — it never reads `dna_match`; the DNA→score coupling lives
inside the mocked LLM, so `ranking_dna_preferred_first.yaml` proves descending sort only.
`rerank_with_preference` is tested with stub scorers (blend math) but no eval fixture ever runs a
TRAINED model over a candidate set — a personalization regression that worsens rank 1 for a mature
creator is invisible.

**Fix direction (CHECK phase):** a deterministic trained-model fixture (fit on a fixed synthetic
label set, seeded) + an eval asserting the blend reorders a candidate set the right way at
threshold weights; rename or strengthen the DNA fixture to test what it names (needs 476 for the
LLM leg).

**Status: DONE 2026-08-13 (same lane).** Deterministic RNG-free 40-row label set (= 2×threshold → the LightGBM branch, weight exactly at PREFERENCE_WEIGHT_CAP) through the REAL `fit()` + `rerank_with_preference`: the blend flips an ordering at w=cap with the Issue-465 contract pinned (score unmutated, blended_score set, dense ranks) and a weight-0 control proving the test can fail. The misleading DNA fixture renamed `ranking_composite_sort_order` with an honest claim cross-referencing the 476 nightly's dna-ordering class.

**Acceptance**
- [x] Eval fixture with a real fitted scorer flips an ordering at w=cap (deterministic, seeded)
- [x] The DNA fixture either exercises real coupling or its name/claim is corrected

### Issue 481: transcription timing fidelity is untested — every boundary depends on it

**Severity: SEV2 — a systematic word-timestamp offset would shift every clip boundary and every
eval would stay green (they supply hand-typed timings).**

`tests/ingestion/test_transcribe.py` + `tests/test_ingest.py` verify normalizer JSON shape only.
No test checks Deepgram/AssemblyAI/WhisperX word timings against known speech; `sentence_snap`
anchors every clip opening on those timings. Also: the no-utterance degenerate fallback
(`transcribe.py:248-259`) silently produces one whole-video segment (see Issue 456's collapse) with
no warning log.

**Fix direction (CHECK phase):** a small recorded real-audio fixture (a few seconds of known
speech, checked-in WAV + expected word windows with tolerance) run through each normalizer's
parsing AND through one recorded provider response; add a WARNING log + metric on the one-segment
fallback so silent degradation becomes visible.

**Status: DONE 2026-08-13 (W5 fixtures lane, branch `fix/issues-481-478c-fixtures`).** LibriSpeech test-clean utterance (CC BY 4.0, provenance README) + a REAL recorded Deepgram nova-3 response (request options byte-matched to production; word-for-word equal to the corpus reference). Parse fidelity = float-EQUAL per backend (the AAI ms÷1000 class pinned; AAI fixture schema-derived — no key available — labeled honestly; WhisperX recorded leg descoped per `[DEC]`). Provider fidelity ±0.25 s (engineering-chosen — no vendor SLA exists). No-utterance fallback now WARNs + sets an additive `degraded` flag surfaced on the transcript endpoint and as a `transcript_degraded` SSE step. Owner-approved live nightly ASR leg rides the LLM nightly, proven green against real Deepgram.

**Acceptance**
- [x] Recorded-fixture test asserts word timings within tolerance for the default backend
- [x] One-segment fallback logs a warning and sets a video-visible degradation flag
- [x] Fixture documented in `tests/fixtures/` README (provenance, license)

### Issue 482: doc↔code accuracy sweep (roll-up)

**Severity: cleanup — none of these change behavior; all of them misdirect the next reader.**

- `docs/CLIPPING_PRINCIPLES.md` / `docs/SOT.md:140` / `docs/PIPELINE.md:81` / CLAUDE.md describe a
  "rolling 60–90 s context window"; code is a fixed `WINDOW_S = 75.0` backward look
- `docs/PIPELINE.md` omits `analyze_video_context` from the chain; `worker/tasks.py` line refs
  drift 65–855 lines (measured)
- CLAUDE.md says `SCENARIO_FLOOR=21`; code is 23 (`tests/test_clip_engine.py:204`); the ratchet
  comment omits the 21→23 raise
- `models.py` Clip.triage comment contradicts shipped Issue-444 behavior
- No test parses `docs/CLIPPING_PRINCIPLES.md` against the four code copies of the registry — add
  the cross-check test (registry is intentionally duplicated; the DOC is the uncovered copy)

**Status: DONE 2026-08-13 (W6 roll-up, branch `fix/issue-482-doc-code-sweep`).** All five legs: 60–90 s wording → the real fixed 75 s backward look (CLAUDE.md, SOT ×2, CLIPPING_PRINCIPLES, PIPELINE); PIPELINE chain gains `analyze_video_context` and its 8 drifted `worker/tasks.py:NNN` refs are de-numbered to symbols (line refs drifted 65–855 lines — numbers are the defect, not the values); CLAUDE.md `SCENARIO_FLOOR` 21 → 31 with a pointer to the in-code ratchet history; `models.py` triage comment rewritten to shipped 444/472 behavior (PUT /triage records the derived verdict + enqueues retrain; skip is an ack); `tests/test_principles_registry.py` parses the doc table and pins all four code copies + the 461 schema enum against it.

**Acceptance**
- [x] Each doc corrected in one pass; registry cross-check test added
- [x] `docs/SOT.md` window.py description matches reality

---

## Source index

Collected from the 2026-08-03 research pass. Cited inline above; listed here so a future pass can
re-verify or refresh them.

**Category / competitive**
- [Opus Clip vs Klap vs Submagic — Submagic](https://www.submagic.co/vs/opus-pro-vs-klap)
- [12 Best Opus Clip Alternatives for 2026 — Choppity](https://www.choppity.com/blog/best-opus-clip-alternatives/)
- [11 Best AI Clipping Tools in 2026 — Ssemble](https://www.ssemble.com/blog/best-ai-clipping-tools-2026)
- [AI clipping tools compared — Whipscribe](https://whipscribe.com/tools/clipping)
- [7 Best Opus Clip Alternatives — Ssemble](https://www.ssemble.com/blog/opus-clip-alternative-free-2026)
- [Opus Clip vs Klap — Butter](https://hellobutter.io/compare-tools/opus-clip-vs-klap)

**Opus Clip**
- [Opus Clip 2026 Complete Guide — AI Tools DevPro](https://aitoolsdevpro.com/ai-tools/opus-clip-guide/)
- [90 Days Deep in Opus Clip: A Full Review — SendShort](https://sendshort.ai/guides/opus-review/)
- [How to Add B-Roll to Opus Clip Videos 2026 — Edimakor](https://edimakor.hitpaw.com/video-editing-tips/opus-add-own-broll.html)
- [OpusClip product page](https://www.opus.pro/home-a-b)
- [OpusClip changelog](https://opusclip.canny.io/changelog)

**Descript**
- [Descript in 2026: Still the Best AI Video Editor?](https://www.fahimai.com/descript)
- [Descript Review 2026 — Filmora](https://filmora.wondershare.com/video-editor-review/descript-ai.html)
- [Descript Complete Guide 2026 — AI Tools DevPro](https://aitoolsdevpro.com/ai-tools/descript-guide/)

**Agentic / AI editing direction**
- [Agentic Video Editing for 2026 — ReelnReel](https://www.reelnreel.com/agentic-video-editing/)
- [What Is AI Video Editing in 2026 — Overlap](https://overlap.ai/blogs/how-do-agentic-tools-work)
- [What Is AI Video Editing and How It Works in 2026 — ChatCut](https://chatcut.io/blog/ai-video-editing)
- [AI Video Agent for Content Creators 2026 — Digen](https://resource.digen.ai/ai-video-agent-for-content-creators-2026/)
- [AI Video Tools in 2026 — Pixflow](https://pixflow.net/blog/ai-video-tools-in-2026/)

**Design / dark UI / components**
- [Dark Mode Design Systems: Patterns, Tokens, and Hierarchy — Muzli](https://muz.li/blog/dark-mode-design-systems-a-complete-guide-to-patterns-tokens-and-hierarchy/)
- [Dark Mode UI Design in 2026 — Tech-RZ](https://www.tech-rz.com/blog/dark-mode-ui-design-in-2026-user-experience-and-ai-powered-interfaces/)
- [AI and Dark Mode UI Design — Tech-RZ](https://www.tech-rz.com/blog/artificial-intelligence-and-dark-mode-ui-design-user-interfaces-in-2026/)
- [Overview of Elevation — Telerik Design System](https://www.telerik.com/design-system/docs/foundation/elevation/)
- [Radix Primitives — Introduction](https://www.radix-ui.com/primitives/docs/overview/introduction)
- [Radix Primitives — Accessibility](https://www.radix-ui.com/primitives/docs/overview/accessibility)
- [Radix Primitives — Select](https://www.radix-ui.com/primitives/docs/components/select)

**Timeline / NLE conventions**
- [Video Editing 101: J, K, and L Shortcuts — PremiumBeat](https://www.premiumbeat.com/blog/video-editing-j-k-l-shortcuts/)
- [Final Cut Pro Shortcuts — Frame.io](https://blog.frame.io/2018/09/17/fcpx-final-cut-pro-shortcuts/)
- [DaVinci Resolve Keyboard Shortcuts 2026 — Pixflow](https://pixflow.net/blog/davinci-resolve-keyboard-shortcuts/)
- [Timeline — EditMentor](https://help.editmentor.com/en/articles/4592281-timeline)
- [Editing — Kdenlive Manual](https://docs.kdenlive.org/en/cutting_and_assembling/editing.html)
- [Kdenlive Timeline/Editing — KDE UserBase](https://userbase.kde.org/Kdenlive/Manual/Timeline/Editing/en)

**Upload architecture**
- [Uppy — AWS S3](https://uppy.io/docs/aws-s3/)
- [Uppy — Choosing the uploader you need](https://uppy.io/docs/guides/choosing-uploader/)
- [Resumable uploads with S3 Multipart — transloadit/uppy#2121](https://github.com/transloadit/uppy/issues/2121)
- [uppy-s3_multipart — server endpoints](https://github.com/janko/uppy-s3_multipart)
- [Supabase — Resumable Uploads](https://supabase.com/docs/guides/storage/uploads/resumable-uploads)
- [Supabase Storage v3: 50 GB resumable uploads](https://supabase.com/blog/storage-v3-resumable-uploads)
- [File Upload Strategies with S3, Node, React, Uppy](https://www.fullstackfoundations.com/blog/javascript-upload-file-to-s3)

**Asset management**
- [Cloudinary — Video Asset Management](https://cloudinary.com/guides/digital-asset-management/video-asset-management)
- [Video Asset Management Software 2026 — Filestage](https://filestage.io/blog/video-asset-management-software/)
- [Best DAM Software for Video 2026 — The Digital Project Manager](https://thedigitalprojectmanager.com/tools/best-digital-asset-management-software-for-video/)
- [The Best DAM Software in 2026 — MediaValet](https://www.mediavalet.com/blog/best-digital-asset-management-platform)

---

## Conventions

- One issue at a time; Check → Approve → Build → Review & Assess (`CLAUDE.md`).
- Phase 1 research is mandatory; its source goes in the issue body, as above.
- Off-course bugs go to `docs/OFF_COURSE_BUGS.md`, not inline fixes.
- Close-out updates `docs/PROJECT_STATE.md`; deviations update `docs/DECISIONS.md`.
- Batch E requires an explicit `[DEC]` before any work begins.
### Issue 483: contain/fit export mode — letterboxed layout with a real background fill (gated on screen-share demand)

**Severity: enhancement — DO NOT BUILD until a beta creator actually uploads screen-share-heavy
sources or requests it.** Filed 2026-08-13 as the successor to Issue 442's removal.

Issue 442 removed the `background` style because every current render is a speaker-centric
crop→scale full-bleed at all three aspects — there is never a letterbox, so a fill can do
nothing. The legitimate use case competitors serve is CONTAIN layouts: screen-share /
gameplay / slide-heavy sources where cropping to a speaker destroys the content, and the
full frame is scaled INTO the vertical canvas with bars to fill (Opus "Fit" = opaque padding,
https://help.opus.pro/docs/article/layout-and-reframing; Descript horizontal-in-vertical
embedding, https://www.descript.com/blog/article/how-to-make-horizontal-video-vertical).

**Scope when demand appears:** (a) new layout mode `fit` alongside the speaker crop — ffmpeg
scale + pad (or scale2ref blur underlay), selectable per render and as a kit default; (b)
background fill becomes a scoped option OF the fit mode only (blur | black | brand color) —
never a global style key again; (c) auto-suggest fit when camera-region consensus (Issue 443)
detects a small/absent camera region, i.e. screen-share-dominant content; (d)
captions/caption_position must respect the padded safe area.

**Acceptance**
- [ ] Fit render green in the eval harness (setup-start unaffected)
- [ ] style_learn extended only if the mode key becomes a real kit field
- [ ] Kit + render API additions covered by the Issue-186/187 test patterns
- [ ] No virality promise anywhere
- [ ] Note: reuse of the JSONB key name `background` is acceptable only nested under the
      fit-mode config, not top-level (old rows carry dead top-level keys)

---

### Issue 484: clips must not open on a clause that inverts the speaker's meaning

- [ ] **Status:** open · **Size:** M · filed 2026-08-13 (promoted from `docs/OFF_COURSE_BUGS.md`
      2026-08-10, where it had sat open, unfiled and unowned) · **Lane:** L29 follow-up ·
      **BETA BLOCKER for a non-friend audience**

**Severity: SEV2 — a clip that states the inverse of the creator's actual opinion is the worst
output this system can produce.** It is worse than an awkward open: an awkward open is a quality
problem, this is a *misattribution* problem. Rank 9 is unrendered today, but the creator can render
it from the UI at any time, so the exposure is live.

**The defect.** `build_sentence_index` treats a Deepgram **utterance** boundary as a **grammatical
sentence** boundary. Deepgram ended an utterance between `"don't"` and `"feel"`, so the index opened
a sentence span at `"feel"`, and `snap_start` therefore considered it a clean standalone open —
`is_weak_opener("feel")` is correctly `False`, because "feel" is not a weak opener. The clip opens:

> *"feel like Percy Butler is a starting free anything"*

The word immediately before it is **"don't"**. The rendered claim is the **opposite** of what the
speaker said. Rank 7 (`"the Terry thing, no."`, preceded by `"but"`) is the same shape and was
knowingly scoped out of Issue 441 as "a fragment, not a closed grammatical class".

**Why the obvious fix is the wrong fix — do not do this.** *Do not extend the weak-opener word
list.* No closed class of words catches this: "feel" is a perfectly good sentence opener in general,
and the defect is not a property of the opening word at all — it is a property of the **boundary**
being fake. A longer list would add false positives without touching the real cause.

**Tractable signals** (from the 2026-08-10 root cause; settle the choice in this issue's own CHECK
phase, and prefer whichever is provable from the fixture):
- **(a)** the previous word carrying **no terminal punctuation** *and* a **sub-threshold pause** —
  this pair is what actually distinguishes a genuine sentence break from an utterance break, and it
  is available in the Deepgram word objects we already persist;
- **(b)** a **negation or auxiliary** immediately preceding the candidate start (`don't`, `won't`,
  `never`, `can't`, `isn't`, and the auxiliary class generally), which is the specific construction
  that inverts meaning rather than merely truncating it.

Signal (a) is the more general fix and (b) the narrower high-precision one; they compose.

**Also in scope — the Issue 441 residual** (`docs/issues.md:2487`). Issue 449 shipped a partial fix;
these survived it and belong with this work because they share the same boundary root cause:
- hedge opens outside the shipped list — rank 6 `"Like,"`, rank 12 `"maybe"`
- the fragment class — rank 7 `"the Terry thing, no."`

**Acceptance**
- [ ] An eval fixture built from **this exact case** — the `"don't" / "feel like Percy Butler…"`
      boundary — is added to `tests/eval/scenarios/` and fails before the fix, passes after
- [ ] A clip does not open at a sentence-index boundary that is an utterance boundary rather than a
      grammatical one, per the signal chosen in CHECK
- [ ] The weak-opener word list is **not** extended (regression guard: the fix must hold with the
      list unchanged)
- [ ] The Issue 441 residual hedge opens (`"Like,"`, `"maybe"`) and the fragment class no longer
      open a clip
- [ ] `SCENARIO_FLOOR` ratcheted to reflect the added fixture(s), in both pinned locations
      (`tests/test_clip_engine.py`, `tests/test_eval_transparency.py`)
- [ ] Setup-start geometry is unaffected — the existing geometry and snap scenarios stay green
- [ ] Scores still cite a named principle from `docs/CLIPPING_PRINCIPLES.md`
- [ ] `docs/OFF_COURSE_BUGS.md` 2026-08-10 entry flipped from 📋 Open to ✅ Fixed with the PR
- [ ] Verified on real output in the next fresh-upload session, not only on the fixture

---

### Issue 485: the Stripe webhook URL points at a 404 — no purchase has ever credited minutes

- [x] **Status:** **DONE 2026-08-14** · **Size:** S · filed 2026-08-13 (live defect, found by the
      first real purchase on prod) · **Lane:** L28 · was **BETA BLOCKER**

**Severity: SEV1 — every completed purchase takes the customer's money and grants nothing.**

> ### ⚠️ Root cause was TWO defects, and the one filed first was the lesser one
>
> This issue was originally filed naming the wrong-URL 404 as the root cause. That was
> **incomplete**: the operative blocker was at the Cloudflare edge, and the 404 sat behind it.
>
> **485a — Cloudflare's OWASP Core Ruleset blocked Stripe's POSTs before they reached the app.**
> Stripe's delivery attempt received a Cloudflare *"Sorry, you have been blocked"* page (Ray
> `a2acda78fc1e5509`) from source `54.187.205.235` — an address on
> [Stripe's published webhook IP list](https://docs.stripe.com/ips). Confirmed in Security Events,
> which named the OWASP Core Ruleset as the acting service. The payload is not malicious; this is
> the documented OWASP **anomaly-score** false positive on Stripe webhook JSON — the same failure
> Troy Hunt documented on Have I Been Pwned, resolved the same way.
> **Fix:** `stripe-webhook-skip-waf` managed-rules exception, path + Stripe-IP scoped, Skip all
> remaining rules, placed First. Recorded in `docs/EDGE_SECURITY.md` **Rule 2**.
>
> *A trap worth recording:* Bot Fight Mode is the intuitive suspect (it is ON, and
> `docs/EDGE_SECURITY.md` already noted it 403'd GitHub health checks). It was **not** the cause —
> and had it been, the fix above would not have worked, because Bot Fight Mode does not run on the
> Ruleset Engine and **cannot** be skipped by a WAF rule on the Free plan. Read Security Events
> before designing the fix.
>
> **485b — the endpoint URL 404**, described below, was real and would have bitten the instant the
> edge block lifted. Fixed by editing the endpoint in place (preserving the signing secret).
>
> **Verified 2026-08-14:** a real purchase logged `billing checkout_session` →
> `billing_webhook_received` → `billing grant … minutes=200 reason=purchase` →
> `billing_webhook_processed`, all under `request_id=48801afa…`. `billing_webhook_received` had
> never appeared in this app's history before that moment. `docs/GO_LIVE.md` billing row is GREEN.

**What happened.** The owner bought the `starter` pack on prod on 2026-08-13. Stripe session
`cs_live_a119Bph…` came back `status=complete`, `payment_status=paid`, `amount_total=1800`. The
minutes never appeared. `POST /billing/webhook` was never hit — `billing_webhook_received` does not
appear in the log at all, so Stripe never delivered rather than being rejected.

**Root cause.** The webhook endpoint registered in the Stripe account is
`https://autoclip.studio/webhooks/stripe`, but the app serves the handler at
**`/billing/webhook`** — `routers/billing.py:26` mounts the router at `prefix="/billing"` and
`:220` declares `@router.post("/webhook")`. Probed live:

| URL | Response |
|---|---|
| `https://autoclip.studio/webhooks/stripe` (registered in Stripe) | **404** |
| `https://autoclip.studio/billing/webhook` (actually served) | 400 — alive, correctly rejecting an unsigned probe |

**Why it hid for so long.** Issue 453's `HTTPXClient` outage meant no Checkout Session was ever
*created* between 2026-05-31 and 2026-08-12, so no webhook was ever *delivered*, so a wrong
delivery URL could not surface. Fixing 453 exposed the next defect in the same path. This is the
second consecutive billing failure where the layer under test passed while the feature stayed
broken — `POST /billing/checkout` returns a clean 200 and logs `billing checkout_session` even
though the customer receives nothing.

**Mitigation already in place (do not rebuild it).** `reconcile_stripe_ledger`
(`worker/tasks.py:1347`, Issue 205) exists precisely for "paid Checkout sessions that the webhook
never delivered" and runs every 24 h (`worker/schedule.py:88`), granting idempotently via
`UNIQUE(stripe_session_id)`. The 2026-08-13 purchase is deliberately being left to the scheduled
beat so that the reconciliation path gets proven on real data.

**The fix — Stripe Dashboard, not code.** Edit the **existing** endpoint's URL to
`https://autoclip.studio/billing/webhook`. ⚠️ **Edit it; do not create a new endpoint.** A new
endpoint is issued a new signing secret, which would no longer match `STRIPE_WEBHOOK_SECRET` on the
VM — turning a 404 into `billing_webhook_rejected reason=bad_signature`, i.e. the same broken
outcome with a different error.

**Acceptance**
- [x] Stripe's webhook IPs can reach `/billing/webhook` through the Cloudflare edge —
      `stripe-webhook-skip-waf` exception deployed 2026-08-14 (`docs/EDGE_SECURITY.md` Rule 2)
- [x] The registered endpoint URL is `https://autoclip.studio/billing/webhook`
- [x] `STRIPE_WEBHOOK_SECRET` on the VM still matches that endpoint's signing secret — confirmed by
      a real signed delivery being accepted, not by inspection (the endpoint was edited in place
      rather than recreated, so the secret never rotated)
- [x] A fresh live purchase logs `billing_webhook_received` → `billing_webhook_processed` and
      credits minutes **without** waiting for the reconcile sweep — 2026-08-14, 200 minutes,
      `request_id=48801afa…`
- [x] `docs/GO_LIVE.md` billing row updated with the evidence → **GREEN**
- [ ] **Follow-up, not blocking:** a test asserts the served webhook path matches the URL registered
      with Stripe, so a future router-prefix change cannot silently re-break delivery. Tracked as
      **Issue 487**.
- [ ] **Follow-up, not blocking:** confirm the 2026-08-13 `starter` purchase (the one that was
      blocked) also credited — it should have been swept up by `reconcile_stripe_ledger`, and
      confirming it closes an otherwise-unverified Issue 205 acceptance criterion.

---

### Issue 486: fix checkout branding — checkout showed another product's branding

- [x] **Status:** **DONE 2026-08-14 — resolved differently than filed.** · **Size:** M · filed
      2026-08-13 · **Lane:** L28

> ### ✅ Resolved by rebranding the shared account, NOT by splitting accounts
>
> Filed as "give AutoClip its own Stripe account". That is **no longer the right fix**, and the
> separate-account plan is **descoped** — see `docs/DECISIONS.md` 2026-08-14.
>
> **What changed:** wheretoliv.com turned out to be **dormant**, so the shared-account objection
> ("rebranding moves the mismatch onto the other product") evaporated. The owner also has a real
> legal entity — **Ludwick Solutions LLC** — which is the correct merchant of record for *both*
> products. Billing every product under the parent entity is the normal arrangement; splitting
> Stripe accounts would fragment revenue and payouts for no benefit.
>
> **Applied 2026-08-14, verified against the live account:**
>
> | Field | Before | After |
> |---|---|---|
> | `business_profile.name` | `Reese Ludwick` | `Ludwick Solutions LLC` |
> | `business_profile.url` | `wheretoliv.com` | `autoclip.studio` |
> | statement descriptor | `LUDWICK SOLUTIONS LLC` | `AUTOCLIP.STUDIO` |
> | card descriptor prefix | *(unset → truncated to `LUDWICK SO`)* | `AUTOCLIP` |
>
> **The statement descriptor was the real find here.** Stripe truncates an unset card prefix from
> the static descriptor to 10 characters, so card charges were going out as **`LUDWICK SO`** — an
> unrecognizable string on the statement of someone who bought "AutoClip". That is the textbook
> cause of avoidable disputes ([Stripe docs](https://docs.stripe.com/get-started/account/statement-descriptors)),
> and it was invisible until billing actually worked. Legitimate under Stripe's rule that the
> descriptor "reflects your DBA name" — AutoClip is the trade name Ludwick Solutions LLC sells under.
>
> **Residual (not blocking):** `business_profile.support_email` is still unset, so Stripe prints no
> support contact on receipts. Set it alongside Issue 488's contact-address work.
> The `support_address` is still the owner's **home address** — replace with the same PO box /
> CMRA mailbox that `MAILING_ADDRESS` (#246) needs.

**Severity: high — it lands on the card-entry page, the highest-trust surface in the product.**

**What's wrong.** One Stripe account currently serves both AutoClip and an unrelated product. The
account's registered webhook endpoints show both:

```
https://autoclip.studio/webhooks/stripe   → checkout.session.completed
https://wheretoliv.com/stripe/webhook     → checkout.session.completed, customer.subscription.*
```

Stripe Checkout renders the **account-level** business profile and offers no per-product override,
so AutoClip customers see the other product's name and domain at the moment they enter card
details. Legacy sessions in the same account carry foreign metadata shapes (`{"plan": "recruiter"}`
vs our `{"creator_id", "pack_id"}`), which also makes any ledger reconciliation noisier than it
needs to be.

**Decision (owner, 2026-08-13):** stand up a **separate Stripe account for AutoClip** rather than
rewriting the shared account's profile — the shared-profile edit is account-wide and would simply
move the branding mismatch onto the other product. A separate account also keeps revenue, payouts
and bookkeeping uncommingled.

**Acceptance** *(rewritten 2026-08-14 for the shared-account resolution; the original
separate-account criteria are descoped — no key rotation, no product recreation, no webhook
re-registration is needed, which is most of why this approach won)*
- [x] Checkout no longer shows an unrelated product's domain — `business_profile.url` is
      `autoclip.studio`
- [x] The merchant of record is the real legal entity — `business_profile.name` is
      `Ludwick Solutions LLC`
- [x] Card statements show a descriptor the customer recognizes — prefix `AUTOCLIP`,
      static `AUTOCLIP.STUDIO`, both verified against the live account
- [x] `docs/DECISIONS.md` records why the separate account was descoped
- [ ] **Residual:** `business_profile.support_email` set (currently unset → no support contact on
      receipts)
- [ ] **Residual:** `business_profile.support_address` moved off the owner's home address — same
      mailbox as `MAILING_ADDRESS` (#246); tracked with Issue 488

---

### Issue 487: pin the Stripe webhook path so a router change cannot silently kill revenue

- [ ] **Status:** open · **Size:** S · filed 2026-08-14 (follow-up to #485) · **Lane:** L28

**Severity: SEV3 — cheap insurance on a path that has already failed silently once.**

Issue 485 cost a 100%-silent revenue outage partly because the URL registered in Stripe
(`/webhooks/stripe`) and the URL the app serves (`/billing/webhook`) drifted apart with nothing
watching. The served path is assembled from two places — `APIRouter(prefix="/billing")` at
`routers/billing.py:26` and `@router.post("/webhook")` at `:220` — so a future prefix change would
move the endpoint with no test failing and no error anywhere. The failure mode is invisible: Stripe
retries into a 404 and the app logs nothing at all.

**Approach.** A unit test that reads the **app's own route table** (`app.routes`) and asserts the
Stripe webhook path equals a single documented constant, rather than hardcoding the string twice.
Same shape as the existing CI-config pins. Cheap, no network, no Stripe dependency.

Consider also asserting the path in `docs/EDGE_SECURITY.md` Rule 2's expression matches — the WAF
exception is scoped to the literal path, so a route change silently breaks the edge exception too,
re-creating the *other* half of #485.

**Acceptance**
- [ ] A test resolves the webhook route from the FastAPI app's route table and asserts it equals the
      documented path
- [ ] The test names #485 in a comment so the next reader knows why it exists
- [ ] Failing the test names the remediation (update the Stripe endpoint URL **and** the
      `docs/EDGE_SECURITY.md` Rule 2 expression), not just "paths differ"

---

### Issue 488: name the legal entity in the ToS and Privacy Policy — the operator is now an LLC

- [ ] **Status:** open · **Size:** S · filed 2026-08-14 · **Lane:** L28 ·
      **Required before non-friend users; also read by Google's OAuth review (#29)**

**Severity: medium — a compliance-accuracy gap, not a bug.**

**What changed.** As of 2026-08-14 the service bills under a real legal entity, **Ludwick Solutions
LLC** (Issue 486). The legal pages have not caught up: `static/privacy.html` and `static/tos.html`
still route the most serious contact channels to a **personal Gmail address** —

- `static/privacy.html`: *"Data breach queries and reports can be directed to
  `reesepludwick@gmail.com`"*
- `static/privacy.html`: the COPPA under-age escalation path, same address
- ToS carries the same address

**Why it matters, concretely:**
1. **GDPR/CCPA identify-the-controller.** A privacy policy is expected to name the **data
   controller** — now the LLC, not an individual. Issue 252 rewrote these pages for GDPR/CCPA when
   the operator *was* an individual; that premise has changed.
2. **Google's OAuth verification reads this page.** `docs/issues-archive-2026-08-03.md` records
   *"Privacy Policy inaccuracy … is a common Google rejection reason."* An entity mismatch between
   the billing merchant, the consent screen, and the privacy policy is exactly the class of
   inconsistency that review catches (#29).
3. **A personal Gmail as the breach-report channel** is a credibility problem the moment a user is
   not a friend — and a breach channel that depends on one person's personal inbox is a genuine
   operational weakness, not only a cosmetic one.
4. **The Stripe account has the same shape of gap** — `support_email` unset, `support_address` is
   the owner's home address. Both surface on customer receipts.

**Approach.** Introduce the entity name once, in config, and reference it from the templates rather
than hardcoding it in two HTML files — the same mistake as any duplicated constant. Then sweep every
user-facing legal surface for the personal address.

**Acceptance**
- [ ] `static/privacy.html` and `static/tos.html` identify **Ludwick Solutions LLC** as the operator
      and data controller
- [ ] Breach-report and COPPA contacts point to a role address on the domain (e.g.
      `privacy@autoclip.studio`), not a personal Gmail
- [ ] Stripe `business_profile.support_email` set to the same role address (closes an Issue 486
      residual)
- [ ] Stripe `support_address` + `MAILING_ADDRESS` (#246) both set to a PO box or CMRA mailbox —
      **not** the owner's home address, since both are printed publicly
- [ ] `docs/COMPLIANCE.md` names the controlling entity
- [ ] The existing structural doc tests still pass (`tests/test_static.py` pins these pages)
- [ ] No personal email address remains in any user-facing surface — grep clean

- Next free issue number: **489**.
