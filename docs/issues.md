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
- [ ] Review baselines regenerated via CI dispatch (`--update-snapshots=all`, ubuntu only)

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
- [ ] Editor + long-form baselines regenerated via CI dispatch

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
- [ ] Live: **cannot be verified on `3b6992fe`** — its source media was purged 2026-08-08 (72 h retention), so no clip of it can be re-rendered. Verify on the next fresh upload: render a clip BEYOND rank 8 via the UI button (the bodiless `POST` is the only path that exercises the fix) and confirm captions

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
- `detect_video_camera_region` samples 24 frames across the whole runtime. Sampling wide is
  itself part of the fix: an overlay on screen for part of a video contributes far less temporal
  variance across 24 frames spanning 27 minutes than across 10 frames inside the 84 seconds it
  happens to cover.
- Resolved during ingest inside the existing `alocal_path` block (the source is already local —
  a separate task would pay a second full download), with the poster's never-fails-ingest
  posture: `ingest_video` is a `RefundOnFailureTask`, so a propagating error would retry the
  ingest and then refund minutes for a transcription that succeeded.
- `render_clip_file` prefers the stored rect and falls back to per-clip detection when it is
  absent, when the source dimensions no longer match, or when **this clip's** face box sits
  outside it — the video-level rect carries no face-sanity check of its own.
- `backfill_video_camera_regions` Beat task, hourly, batch 10 (vs the poster sweep's 25 — this
  pass decodes 24 frames per video rather than one).

**Acceptance**
- [x] An animated overlay strip below the camera band is excluded, not unioned in (`test_animated_overlay_strip_below_the_camera_is_excluded`, demonstrated failing first at bottom edge 1079 vs the camera band's 800)
- [x] The side-by-side two-camera union the rule exists for still works (`test_side_by_side_second_camera_is_still_unioned`, green before and after)
- [x] Region is resolved per video, stored, and preferred by the render path; per-clip detection remains the fallback for older videos (`test_video_level_region_is_preferred_over_per_clip_detection`, `test_absent_video_region_still_detects_per_clip`) — **wiring verified, but the measurement it feeds is wrong: see Issue 443.** Stage 2 is disabled in data on prod (rects nulled, backfill markers set) pending that fix
- [x] A clip whose speaker falls outside the stored rect re-detects rather than cropping them away (`test_video_level_region_falls_back_when_the_face_is_outside_it`)
- [x] A rect measured against different source dimensions is distrusted (`test_video_region_rejected_when_source_dimensions_changed`)
- [x] Every clip of one source unpacks an identical rect (`test_video_region_is_shared_by_every_clip_of_one_source`)
- [x] Ingest and backfill safety contracts pinned (`tests/test_camera_region_ingest_safety.py`)
- [~] **A height ceiling was NOT added** — see `docs/DECISIONS.md` 2026-08-07. Building it showed the instrument cannot work: `test_detects_inner_camera_region` asserts a legitimate 0.648–0.815 height fraction and the defective region was 0.694, inside that band. Any ceiling catching the real failure would reject correct regions
- [ ] Live: **cannot be verified on `3b6992fe`** (source purged 2026-08-08). Verify Stage 1 on the next fresh upload — and note **Issue 443 must land first**, or Stage 2 will store a ~0.70 region at ingest and reintroduce this exact defect across every clip

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
- [ ] Live: **cannot be verified on `3b6992fe`** (source purged 2026-08-08). Verify on the next fresh upload — any `face_pan` clip must show few keyframes, ≤1 direction flip, and no frame resting on empty background

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
- [ ] Live: next fresh upload shows no verbatim duplicated speech between clips and no conjunction-initial opens — **cannot be verified on `3b6992fe`**, whose windows are already persisted; needs a new upload or a regeneration pass

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

**Acceptance**
- [ ] Decide: build a contain/letterbox mode where a background is meaningful, or remove `background` end to end (API field, `CreatorStyle` payload, brand-kit UI, docs)
- [ ] Whichever way, no code path accepts a style key it does not honor
- [ ] A creator setting a background either sees it in the render or cannot set it at all

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

**Current state:** both stored rects have been nulled on prod and the
`camera_region_backfill_failed:` markers re-set (7-day TTL) so the hourly beat cannot refill them.
The render path falls back to per-clip detection, i.e. Stage 1 behaviour, which is what the live
drill is verifying. Nothing is user-visibly broken — the column is simply unused.

**Fix direction:** run `detect_camera_region` over **N short windows** (each inside
`_LINEAR_DECODE_MAX_SPAN_S`, e.g. 60 s) spread across the runtime, discard the declines, and take
a component-wise **median** of the surviving rects. That is a genuine consensus: robust to a
window that happens to contain an overlay burst, and each detection runs on the stable-layout span
the detector was designed for. Reuses the existing per-clip path unchanged.

**Acceptance**
- [ ] The video-level rect for `3b6992fe` lands near the healthy per-clip height fraction (~0.51), not ~0.70
- [ ] A single overlay-heavy window cannot move the consensus (unit test: N clean windows + 1 poisoned → clean result)
- [ ] A rect that deviates materially from the per-clip detections is not stored at all — declining is correct, and the render already falls back
- [ ] Live: backfill `3b6992fe`, confirm the stored rect, then re-render rank 6 and confirm the overlay stays gone
- [ ] The failure marker is not left suppressing retries after a code fix (this cost a full cycle to notice)

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
- Next free issue number: **444**.
#JS9DAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWW     RRRRRRRRRRRRRRRRREEEEEEEEEEE[ -KQLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLL;]