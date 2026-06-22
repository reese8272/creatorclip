# Research Brief 03 — Editing Capabilities & a Modern Editorial Tool (Issue 168)

**Author:** read-only research agent · **Date:** 2026-06-22
**Drives:** Issue 168 (Phase 1 CHECK) → proposed sub-issues below
**Scope:** Map the gap between CreatorClip's current "cut + caption + reframe + clean + text-cut"
render path and the editing capabilities of modern short-form software (Descript, Opus Clip,
CapCut, Submagic, Riverside), then define what an in-product editorial tool should do — judged by
whether it deepens the channel-knowledge loop, not by feature parity.
**Method:** current industry standard researched first (links inline); every repo claim cited
`file_path:line`. Where I could not verify something, I say so explicitly.

> Guardrails this brief respects throughout: **no virality promise** anywhere in proposed UX (the
> honesty constraint, `CLAUDE.md`; structural test must stay green); **no YouTube-hosted media
> download** — source is always creator-uploaded (`docs/PRD.md:93`, re-confirmed
> `docs/OFF_COURSE_BUGS.md:26`); every clip-affecting capability must still cite a named principle
> from `docs/CLIPPING_PRINCIPLES.md`.

---

## 1. Executive summary — the 7 highest-leverage capabilities

Ordered by leverage (impact on the North Star ÷ build cost). Detail and citations follow in §2–§4.

1. **Audio leveling on every render (loudnorm two-pass).** Today **nothing normalizes loudness on
   either analysis or render.** `pyloudnorm==0.1.1` is pinned in `requirements.txt:` but has **zero
   imports in the entire repo** (`grep -rn pyloudnorm` → requirements only); audio analysis uses
   librosa **RMS**, not LUFS (`ingestion/audio.py:45`). The render path has no `-af loudnorm` at all
   (`clip_engine/render.py:224-254`). Every other tool ships consistent loudness; uneven volume is
   the most-noticed quality defect in a silent-autoplay feed. A single `-af loudnorm=I=-14:TP=-1.5`
   on the existing ffmpeg call is the cheapest credibility win available, and it directly serves
   Principle 5 (*Dead-air elimination → momentum is retention*). **Cheap win.** *(Also corrects a
   stale SOT claim — see §5.)*

2. **Promote the transcript editor + trim sliders into a single timeline-with-waveform scrubber
   with candidate markers.** We already have two disconnected editing surfaces — drag-trim sliders
   in `ClipPlayer.tsx:99-132` and a Descript-style transcript strikethrough editor in
   `TranscriptEditor.tsx` — plus a clean-pass panel. None of them shows *where on the clip* an edit
   lands. A `grep` for `timeline|waveform|scrubber|marker` across `frontend/src` returns no editing
   timeline (cross-confirmed in brief 01). The modern minimum is a transcript ↔ waveform ↔ playhead
   that stay in sync (Descript's model). This is the structural backbone every other capability
   hangs off. **Heavy lift, but unlocks the rest.**

3. **Real active-speaker reframe (replace the single-keyframe Haar crop).** Current reframe detects
   **one face on one keyframe at the clip midpoint** and uses a **static** crop x-offset for the
   whole clip (`render.py:179-197`). The standard (Opus ReframeAnything, Google AutoFlip) is
   *per-frame* salient-subject tracking that pans the crop and switches between speakers. Our
   approach visibly fails on any movement, multi-speaker, or B-roll segment. This is the largest
   single quality gap vs. the market. **Heavy lift** (MediaPipe/AutoFlip tracking pipeline, or a
   hosted reframe API — build-vs-buy in §2).

4. **Keyword / emoji highlighting on captions.** Our three ASS styles
   (`captions.py:170-258`) animate but treat every word identically. The 2026 table-stakes caption
   is white text with the *key* word color-highlighted (yellow/red) — it is the single most-cited
   short-form caption pattern. We already emit per-word ASS events with color overrides
   (`captions.py:213-215` proves the `\c` mechanism works); highlighting select keywords is an
   incremental ASS-tag change, and *which* words to highlight can be a DNA signal. **Cheap-ish win,
   serves the loop.**

5. **A Brand Kit (saved caption style + colors + font, applied by default).** Style choices today
   live only in a per-clip `style_preset` JSONB (`clips.style_preset`, `docs/SOT.md:375`) re-picked
   every render via dropdowns (`CaptionStylePanel.tsx`). Every competitor persists a creator-level
   brand kit. For CreatorClip this is not cosmetic: a saved style the creator keeps choosing **is**
   channel knowledge — it should be *learned* and pre-applied, which no competitor does. **Medium,
   high North-Star value.**

6. **Auto-zoom / punch-in on peaks (reuse the existing signal timeline).** CapCut/Submagic auto-zoom
   on emphasis. We already compute energy spikes + retention spikes + the `peak_s` per clip
   (`signals.py`, `candidates.py:185-203`). A scale-keyframe punch-in at `peak_s` is a pure ffmpeg
   `zoompan`/`scale` change driven by data we already have — and it literally enacts Principle 4
   (*Pattern interrupt*). **Cheap win once the timeline exists.**

7. **Aspect-ratio export beyond 9:16 (1:1 and the 16:9 stream-recap).** Output is hardcoded
   1080×1920 (`render.py:34-35`); `ClipFormat.horizontal` is an enum value that **nothing renders**
   (`models.py:87`, confirmed in brief 01). 1:1 is a trivial crop param; 16:9 horizontal is the
   recap surface that brief 01's stream-summary proposal needs. **1:1 is a cheap win; 16:9
   multi-segment is a scope-expansion heavy lift requiring a `DECISIONS.md` entry.**

**What to consciously NOT build (anti-bloat):** a full multi-track NLE timeline, real-time GPU
preview, generative B-roll/text-to-video, AI voice/dubbing, transitions/effects libraries. These
are CapCut/Premiere's job; they do not deepen channel knowledge and they explode scope. The North
Star is "AI does the first pass, you tweak a little," not "be Premiere." (Market context:
`docs/COMPETITIVE_RESEARCH.md:36-40` — the field's weakness is *weak editors used as correction
surfaces*; we win by making the AI first pass good and the tweak loop tight, not by out-featuring.)

---

## 2. Capability matrix

Gap size: ◐ small · ◑ medium · ● large. "Serves DNA loop?" judges the North Star, not polish.

| Capability | CreatorClip today (`file:line`) | Modern standard | Gap | Build approach | Serves DNA loop? |
|---|---|---|---|---|---|
| **Transcript-driven editing** | Drag-select words → strikethrough → cut-list → re-render (`TranscriptEditor.tsx:69-244`); validated server-side (`edits.py`); applied via `render_cleaned_clip_file` (`render.py:270`) | Descript: delete text = delete media, word-level, instant, timeline stays in sync ([Descript review 2026](https://www.vidmetoo.com/descript-review/)) | ◑ | Have the core. Gap = no live preview, no sync to a playhead/waveform, edits are localStorage + full re-render (`TranscriptEditor.tsx:81-87`), not instant | Y — the edit surface *is* where taste is expressed |
| **Timeline / waveform scrubber + markers** | None. Two disjoint surfaces (trim sliders `ClipPlayer.tsx:99-132`; transcript `TranscriptEditor.tsx`); no waveform, no playhead, no candidate markers (`grep` finds none) | Waveform + playhead + trim handles + ripple delete; candidate/clip markers on the bar (Descript, Opus, Riverside) | ● | New: render waveform (ffmpeg `showwavespic` or WebAudio), HTML5 `timeupdate` playhead, map word/cut indices to time. Backend already has all timestamps | Y — turns "one clip at a time" into "see where clips came from" (also brief 01 gap 4) |
| **Auto-reframe / active-speaker** | One Haar face on ONE midpoint keyframe, **static** crop for whole clip; falls back to center (`render.py:99-122,179-197`) | Per-frame salient-subject tracking, pans + switches speakers; AutoFlip/ReframeAnything ([Opus AI Reframe](https://www.opus.pro/ai-reframe); [Google AutoFlip](https://research.google/blog/autoflip-an-open-source-framework-for-intelligent-video-reframing/)) | ● | Build: MediaPipe face/AutoFlip per-frame track → time-varying crop in ffmpeg `sendcmd`/`crop` expr. Buy: hosted reframe API (cost + ToS review). MediaPipe is the documented OSS path; depends on ffmpeg (`mediapipe` already feasible — `opencv-python` is in `requirements.txt`) | Partly — quality, not channel-specific. Table-stakes correctness |
| **Filler / silence removal** | Tier-1 unconditional + Tier-2 pause-flanked fillers + 800ms silence w/150ms tail (`filler.py`); preview + apply UI (`CleanPassPanel.tsx`) | Same idea, one-click; CapCut/Submagic bundle it ([Submagic 2026](https://www.toolsforhumans.ai/ai-tools/submagic)) | ◐ | **At parity.** Gap = it's a separate panel, not folded into the timeline; no waveform to see what's removed | Y (Principle 5) |
| **Auto-captions + animated styles** | 3 ASS styles (bold_pop / gradient_slide / minimal), word-level, lower-third safe zone, Anton font (`captions.py`) | Word-by-word + **keyword/emoji highlight**, 100+ templates, auto-emoji ([TikTok caption styles 2026](https://blitzcutai.com/blog/best-caption-style-tiktok); [text-animation packs](https://www.opus.pro/blog/best-text-animation-packs-captions-titles)) | ◑ | Have animation; missing keyword highlight (incremental `\c` per-word — mechanism proven `captions.py:213`), emoji, more templates. No translation (out of scope, see brief 14) | Y if highlight word choice is a DNA signal |
| **Keyword / emoji highlight** | None (every word styled identically) | The dominant 2026 caption look — key word in yellow/red ([2026 caption styles](https://blitzcutai.com/blog/best-caption-style-tiktok)) | ◐ | ASS `\c` override on selected tokens; keyword set from transcript salience or DNA | Y |
| **Brand kit / saved style** | Per-clip `style_preset` JSONB only, re-picked each render (`CaptionStylePanel.tsx:11-13`, `clips.style_preset` `SOT.md:375`) | Creator-level saved logo/colors/fonts/intro-outro, one-click apply ([OpusClip brand kit](https://www.opus.pro/captions)) | ◑ | New `creator_style` table or fold into `creator_dna`; default the render preset from it; **learn** it from repeated choices | **Y — strongest** (a saved style the creator keeps choosing IS channel knowledge; learning it is unique to us) |
| **Auto-zoom / punch-in** | None | Auto-zoom on emphasis/talking-head (CapCut AI Auto-Edit; Submagic) ([CapCut 2026](https://flowith.io/blog/capcut-ai-auto-edit-effect-engine-define-video-production/)) | ◑ | ffmpeg scale/`zoompan` keyframe at `peak_s` (already computed `candidates.py:186`) | Y (Principle 4 pattern-interrupt; data-driven) |
| **B-roll / overlay suggestion** | None | Transcript-keyword → stock-footage auto-insert (Submagic, Opus AI B-roll — "not yet polished") ([Submagic](https://www.toolsforhumans.ai/ai-tools/submagic)) | ● | Buy: stock API (Pexels/Storyblocks) + licensing/ToS; heavy overlay compositing | **N for v1** — generic stock dilutes "knows your channel"; defer |
| **Scene / shot detection** | None (peaks are audio/retention, not visual cuts) | PySceneDetect-class shot boundaries | ◑ | OSS `PySceneDetect`; feeds reframe + zoom, not a user feature itself | Partly (improves cut quality) |
| **Audio: loudness normalization** | **None on render**; `pyloudnorm` pinned but **unused** (`grep`); analysis is RMS not LUFS (`audio.py:45`) | Two-pass `loudnorm` to −14 LUFS for YouTube ([ffmpeg loudnorm guide](https://mitz17.com/en/blog/ffmpeg-loudnorm-guide/)) | ◐ | One `-af loudnorm=I=-14:TP=-1.5:LRA=11` (single-pass acceptable for short clips; two-pass for the recap) | Y (Principle 5; credibility) |
| **Audio: noise reduction** | None | RNNoise / `arnndn`, applied cautiously ([loudnorm guide](https://32blog.com/en/ffmpeg/ffmpeg-audio-normalization-loudnorm)) | ◑ | ffmpeg `arnndn` (needs a model file) or `afftdn`; opt-in, after loudnorm | Partly (quality) |
| **Audio: music bed + ducking** | None | Background music + sidechain duck under speech | ● | ffmpeg `sidechaincompress`; needs licensed music library | **N for v1** — licensing + not channel-knowledge; defer |
| **Export: aspect ratios** | 9:16 only, hardcoded (`render.py:34-35`); `horizontal` enum unrendered (`models.py:87`) | 9:16 / 1:1 / 16:9 presets (every tool) | ◑ | 1:1 = trivial crop param (cheap). 16:9 multi-segment recap = scope expansion (brief 01) → `DECISIONS.md` | Y (meets creator where their audience is) |
| **Export: resolution/codec presets** | Fixed libx264 crf23 fast, 1080p, aac128 (`render.py:238-249`) | Quality/size presets | ◐ | Parameterize crf/preset/resolution | N (operational) |
| **Platform-specific export / publish** | None (recommend + export only, by design `PRD.md:100`) | Direct publish/schedule to Shorts/TikTok/Reels | ● | **Out of scope v1** → owned by brief 13 (`13_multiplatform_distribution_publishing.md`); needs `DECISIONS.md` | N here |

---

## 3. Target editor UX

**Recommendation: keep the player-first Review page as the *triage* surface; add a deeper Editor
behind it. Do not bloat Review into an NLE.**

The current `Review.tsx` is explicitly the "feels like scrolling" triage loop the PRD asks for
(`PRD.md:61-63`) — single player, Keep/Drop/Skip/Trim, every action a training label
(`ClipPlayer.tsx:31-47`). That loop is correct and should stay lean. The editing tools bolted onto
it as collapsible panels (`Review.tsx:74-92`: transcript, caption, clean) are the source of the
known "empty bottom-right quadrant" balance bug (`OFF_COURSE_BUGS.md:36`, since patched in Issue
163) and conflate two jobs: *judging* a clip vs. *finishing* a clip.

**Proposed split:**

- **Review (triage)** — unchanged in spirit. Watch → Keep/Drop/Skip → Next. The fast scroll loop.
  Add one affordance: a **"Refine →"** button on a kept clip that opens the Editor. Keep the trim
  *sliders* here (a 5-second tweak), but move transcript/caption/clean into the Editor.

- **Editor (finish)** — a focused single-clip workspace, AI-first:
  1. **Top:** the 9:16 (or chosen ratio) preview player.
  2. **Center:** the **timeline-with-waveform** (capability §2 row 2) — playhead synced to the
     player, with the transcript rendered *under* the waveform so a word and its waveform position
     line up (Descript's core move). Drag on either to select; selection = a cut or a trim.
  3. **The AI has already done a first pass:** captions on (creator's brand-kit style), filler +
     silence pre-removed (shown as already-struck, reversible), reframe applied, loudness
     normalized. The human *subtracts/tweaks*, never starts from a blank timeline.
  4. **Right rail (progressive disclosure):** Caption style + keyword-highlight, Reframe
     (auto/manual nudge), Zoom/punch-in toggles, Aspect ratio, Brand kit. Defaults that "just
     work," deeper controls tucked behind (the COMPETITIVE_RESEARCH "magical but not confusing"
     lesson, `docs/COMPETITIVE_RESEARCH.md:46`).
  5. **One commit action** re-renders and swaps the main render (the existing
     `clean/confirm` swap pattern, `clips.py:436`, generalizes to all edits).

This reconciles the two stories: Review stays the scrolling triage; the Editor is where "AI did
the first pass, you tweak a little" lives. It also fixes the structural conflation behind the
quadrant bug by giving the editing tools their own page instead of cramming them beside the player.

**Honesty note:** the Editor shows the **Fit tier badge** (already the honest signal,
`ClipPlayer.tsx:96`, `docs/UI.md`), never a virality number.

---

## 4. Proposed issues (dependency-ordered)

House style mirrors `docs/issues.md` (What / Acceptance criteria / Depends on). These slot under
the Issue 168 umbrella. Issue numbers TBD at triage (next free number is past 165). Grouped
cheap-wins → heavy-lifts. **Bold = needs a `docs/DECISIONS.md` entry.**

### Group A — Cheap wins (existing path + modest work)

**A1 — Loudness normalization on every render**
*Depends on:* none.
**What:** Add `-af loudnorm=I=-14:TP=-1.5:LRA=11` to `render_clip_file` and
`render_cleaned_clip_file` (`render.py:224,328`). Single-pass is acceptable for ≤90s clips; document
the choice. Remove or actually use the dead `pyloudnorm` pin. Correct `docs/SOT.md:19`.
**Acceptance criteria:**
- [ ] Rendered clips measure −14 ±1 LUFS (verify with `ffmpeg ... -af ebur128`).
- [ ] No audible pumping on a quiet-then-loud test clip.
- [ ] `pyloudnorm` either imported-and-used or removed from `requirements.txt`; SOT updated.
- [ ] Cites Principle 5 in the render docstring.

**A2 — Keyword / emoji highlight in captions**
*Depends on:* none.
**What:** Extend `captions.py` so selected tokens render with a highlight color (`\c` override,
mechanism already proven `captions.py:213-215`). Keyword set from transcript salience for v1
(DNA-driven selection is a later follow-up).
**Acceptance criteria:**
- [ ] At least one new style (e.g. `bold_pop_highlight`) emits ≥1 colored keyword per phrase.
- [ ] Falls back to plain when no keywords found; existing styles unchanged.
- [ ] `VALID_STYLES` + `CaptionStylePanel.tsx` dropdown updated; eval/unit test added.

**A3 — 1:1 (square) export option**
*Depends on:* A-none (independent of 16:9 recap).
**What:** Parameterize output dimensions in `render.py` (`_OUTPUT_W/_OUTPUT_H`) and add a `square`
choice to the format selector; crop math reused.
**Acceptance criteria:**
- [ ] `square` renders 1080×1080 centered on the detected face.
- [ ] 9:16 path byte-identical to today when format unchanged.
- [ ] *(No DECISIONS entry — 1:1 is within "Shorts export"; only 16:9 expands PRD scope.)*

**A4 — Auto-zoom / punch-in at peak (opt-in toggle)**
*Depends on:* none for a basic version; nicer with A5 timeline.
**What:** Add a `zoom_on_peak` style flag that applies a brief scale punch-in centered at `peak_s`
(already on every clip, `candidates.py:186`) via ffmpeg scale keyframes.
**Acceptance criteria:**
- [ ] Toggle produces a visible ~5–10% punch-in at the peak, returning to 100%.
- [ ] Off by default; cites Principle 4.

### Group B — Brand kit (medium, high North-Star value)

**B1 — Creator-level Brand Kit (saved style, applied by default)**
*Depends on:* A2 (so highlight color is part of the kit).
**What:** Persist a creator-level style (caption style, highlight color, font, background) — new
small table or a field on `creator_dna`. New render defaults the preset from it instead of empty
dropdowns (`CaptionStylePanel.tsx:11-13`). Surfaced in Profile + the Editor right rail.
**Acceptance criteria:**
- [ ] A creator can save a brand kit; new clips render with it by default.
- [ ] Per-creator isolation enforced on the kit query.
- [ ] Existing per-clip `style_preset` still overrides the kit for one-off renders.

**B2 — *Learn* the brand kit from repeated choices** *(stretch, the moat)*
*Depends on:* B1 + enough feedback rows.
**What:** When a creator repeatedly picks the same style, surface "make this your default?" — turn
a manual kit into a learned one. Ties caption/style choice into the channel-knowledge loop (the one
thing no competitor does, `docs/COMPETITIVE_RESEARCH.md:104`).
**Acceptance criteria:**
- [ ] After N consistent choices, the UI proposes defaulting to that style.
- [ ] Honest framing; no virality claim. *(Consider a `DECISIONS.md` note: style is now a learned
  DNA dimension.)*

### Group C — Heavy lifts (sequence after the timeline exists)

**C1 — Timeline-with-waveform editor surface (the backbone)**
*Depends on:* none structurally, but is the foundation for C2/C4 and the §3 Editor.
**What:** A new Editor page: waveform (ffmpeg `showwavespic` or WebAudio) + synced playhead +
transcript aligned under it; selection drives trims/cuts through the *existing*
validate-cuts → `render_cleaned_clip_file` path (`edits.py`, `render.py:270`). No new render
primitive — just a real editing UI over the timestamps the backend already has.
**Acceptance criteria:**
- [ ] Waveform + playhead stay in sync with playback.
- [ ] Word selection and waveform selection both produce a cut, validated server-side.
- [ ] Review's transcript/caption/clean panels move here; Review keeps trim + triage.
- [ ] Fixes the editing-tools-beside-player conflation (`OFF_COURSE_BUGS.md:36`).

**C2 — Real active-speaker reframe (per-frame tracking)**
*Depends on:* a build-vs-buy decision (**DECISIONS entry required**).
**What:** Replace the single-keyframe static crop (`render.py:179-197`) with per-frame
salient-subject tracking (MediaPipe/AutoFlip → time-varying ffmpeg crop, or a hosted reframe API).
**Acceptance criteria:**
- [ ] On a moving/two-speaker test clip, the crop follows the active speaker (vs. today's static
  midpoint crop).
- [ ] Graceful fallback to center on detection failure (preserve current behavior).
- [ ] **`DECISIONS.md`:** record the build (MediaPipe/AutoFlip) vs. buy (hosted API) call with cost
  + ToS + latency evidence. This is the single biggest market-quality gap.

**C3 — [SCOPE EXPANSION] 16:9 multi-segment stream recap**
*Depends on:* A3 (ratio param), brief 01's stream-summary work (shared owner).
**What:** Render the unrendered `ClipFormat.horizontal` (`models.py:87`) as a duration-budgeted,
narrative-ordered 16:9 montage from a creator-uploaded VOD. Co-owned with brief 01 (do not
duplicate).
**Acceptance criteria:**
- [ ] **`DECISIONS.md` first** — `docs/PRD.md:101` lists live-stream/recap out of v1; this moves
  the boundary. ToS-clean framing: creator uploads the VOD file (`origin=upload`), never a YouTube
  download.
- [ ] Produces a single 16:9 mp4 from multiple selected segments; loudness-normalized (A1).
- [ ] Reuses transcription, signal timeline, peak detection, DNA-fit scoring.

**C4 — Noise reduction (opt-in)**
*Depends on:* A1 (apply after loudnorm).
**What:** Optional `arnndn`/`afftdn` denoise pass, off by default, applied before loudnorm.
**Acceptance criteria:**
- [ ] Opt-in toggle; audible hiss reduced on a noisy test clip without speech artifacts.
- [ ] Off by default (over-aggressive denoise harms voice — the documented caution).

### Explicitly deferred (logged, not proposed): generative/AI B-roll, music beds + ducking, AI
dubbing/translation (→ brief 14), transitions/effects libraries, direct publishing (→ brief 13),
full multi-track NLE, real-time GPU preview. Each is either out-of-loop, licensing-heavy, or owned
by another brief.

---

## 5. Stale / contradictory docs found (flagged, not fixed — read-only)

- **`docs/SOT.md:19`** says audio analysis is "librosa + **pyloudnorm** (Energy, silence, volume
  spikes…)". `pyloudnorm` is **not imported anywhere** (`grep -rn pyloudnorm` → only
  `requirements.txt`); `ingestion/audio.py:45` uses **librosa RMS**, not LUFS. SOT overstates the
  audio stack and implies a loudness capability that does not exist. A1 above corrects it.
- **`requirements.txt`** pins `pyloudnorm==0.1.1` as a **dead dependency** (zero imports). Either
  wire it into A1's normalization or drop it.
- **`models.py:87`** `ClipFormat.horizontal` is an **enum value nothing renders** (also flagged in
  brief 01) — a latent stub the matrix and C3 depend on.

---

## 6. Open questions for the human (one-line answers)

1. **Editor depth:** "AI does it, you tweak a little" (my recommendation — keep Review lean, add a
   focused Editor) **or** a deeper timeline tool users finish in? *(Drives whether C1 is foundation
   or overkill.)*
2. **Reframe build-vs-buy:** OK to spend on a hosted reframe API for C2, or build on
   MediaPipe/AutoFlip to avoid per-render cost + ToS review? *(Cost vs. control.)*
3. **Brand kit = DNA dimension?** Should saved caption/style become a *learned* part of Creator DNA
   (B2), or stay a manual setting (B1 only)? *(This is the differentiating call.)*
4. **16:9 recap scope:** Approve the `DECISIONS.md` entry to expand v1 past "Shorts only" for the
   stream recap (C3, shared with brief 01), or hold it for v2?
5. **B-roll/music:** Confirm we deliberately skip generic stock B-roll + music beds for v1 (they
   dilute "knows *your* channel" and add licensing risk)?

---

*Cross-references: brief 01 (stream→summary §C3, per-video timeline/markers §2, "done-but-not-
visible" discipline), brief 13 (publishing — out of scope here), brief 14 (caption translation/
i18n — out of scope here), brief 08 (whether learned style/DNA actually improves clip quality —
owns the efficacy eval for B2).*
