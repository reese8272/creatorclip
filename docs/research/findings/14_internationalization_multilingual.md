# Research Brief 14 — Internationalization & Multilingual Support (Issue 179)

**Author:** read-only research agent · **Date:** 2026-06-22
**Drives:** Issue 179 (Phase 1 CHECK) → sub-issues below
**Scope:** Two axes — (A) **multilingual content processing** (transcription, alignment, LLM
analysis, captions for non-English videos) and (B) **product i18n** (translating the UI).
**Method:** current industry standard researched first (links inline); every repo claim cited
`file_path:line`. Where I could not verify a claim from the repo or a source, I say so.

> Guardrails this brief respects: the honesty constraint (`CLAUDE.md` — a clip the pipeline can't
> handle well must degrade honestly, never silently mis-cut/mis-caption); the setup-not-aftermath
> guarantee, which depends on **accurate word-level timestamps in the source language**
> (`clip_engine/window.py`, captions at `clip_engine/captions.py:11-14`); per-creator isolation;
> no virality promise.

---

## 1. Executive summary — lead with conclusions

**The must-have is correct multilingual *content* handling; full UI translation is the later.** A
Spanish/Hindi/Portuguese creator can sign in today (Google OAuth is language-neutral) and the UI is
usable in English — but the moment their video enters the pipeline, **four content stages silently
assume English or silently lose fidelity**, and the failures are invisible to the creator. That is
the real risk, and it is a direct honesty-constraint violation.

The five headline findings, worst-first:

1. **Transcription defaults to an English-only configuration.** The default backend is Deepgram
   (`config.py:84` `TRANSCRIPTION_BACKEND = "deepgram"`) and the call hardcodes
   `PrerecordedOptions(model="nova-3", …)` with **no `language` and no `detect_language` option**
   (`ingestion/transcribe.py:108`). Deepgram's `nova-3` (monolingual) defaults to English; to
   transcribe other languages you must pass either an explicit `language=` or `language="multi"`
   (Nova-3 Multilingual) ([Deepgram models/languages](https://developers.deepgram.com/docs/models-languages-overview),
   [Nova-3 multilingual](https://deepgram.com/learn/nova-3-multilingual-major-wer-improvements-across-languages)).
   As shipped, a Spanish video is transcribed *as if it were English* — garbage text, garbage
   word timings, and therefore a mis-cut, mis-captioned clip. This is the single biggest gap.

2. **The detected/source language is never captured or persisted.** WhisperX auto-detects language
   and uses it for alignment (`ingestion/transcribe.py:252`, `result["language"]`), but the
   normalized transcript schema is `{"source", "segments"}` only (`ingestion/transcribe.py:9-12`,
   `:270`) — the language is **discarded**. Deepgram and AssemblyAI normalizers never read or store
   language at all (`:123-176`, `:204-221`). There is no `language` column anywhere
   (grep of `models.py`, `alembic/versions/` returns nothing). Without a stored language, the
   product cannot route the caption font, the prompt language, or honest support messaging — every
   downstream fix depends on this one missing field.

3. **Captions cannot render non-Latin scripts — wrong font, no fallback shipped.** The caption
   renderer hardcodes `fontname="Anton"` (`clip_engine/captions.py:45,130`), and the only fonts in
   the image are Anton + `fonts-open-sans` + `fonts-dejavu-core` (`Dockerfile:13-19`) — **all
   Latin/Cyrillic/Greek; none cover CJK, Arabic, or Devanagari.** libass *can* shape complex
   scripts and RTL via HarfBuzz, but only if a covering font is installed and selected
   ([HarfBuzz/libass](https://en.wikipedia.org/wiki/HarfBuzz),
   [SubtitlesOctopus CJK fallback](https://github.com/libass/JavascriptSubtitlesOctopus)). Today a
   Japanese or Arabic clip renders as tofu (□□□) boxes — a visibly broken deliverable.

4. **Every LLM prompt is English-only with no language instruction.** The Creator Brief
   (`dna/brief.py:35-57`), titles (`knowledge/titles.py`), hooks (`knowledge/hooks.py`), chat
   (`chat/prompt.py`), and scoring (`clip_engine/scoring.py`) all instruct in English with no "respond
   in the creator's language" directive. Claude *can* operate multilingually, but the 2025 standard
   is explicit: **match output language to content language** — keep instructions in English, direct
   the model to respond in the target language, and never machine-translate the prompt itself
   ([ACL 2025 study, 35 languages](https://aclanthology.org/2025.naacl-short.55.pdf),
   [arXiv 2502.09331](https://arxiv.org/html/2502.09331v1)). Today a German creator gets
   German titles suggested only by luck, and the brief may come back in English.

5. **Product UI i18n: none exists, and that is acceptable for a closed beta.** No i18n library is
   installed (`frontend/package.json` has no `i18next`/`react-intl`), strings are hardcoded English
   across `frontend/src/pages/*.tsx`, and the font stack (Geist/Inter, `docs/UI.md:114-116`) is
   Latin-only. This is the **defer** bucket — recommend the architecture now, build it when a target
   market is chosen.

**Biggest current risk:** #1 + #2 together. A non-English creator's clip is produced *confidently
and wrongly*, with no signal to the creator that the pipeline didn't understand their audio — the
exact silent-mis-cut failure the honesty constraint forbids.

**Doc discrepancy to flag:** `CLAUDE.md` ("Frontend: vanilla HTML/CSS/JS") is stale — the frontend
is a React + TypeScript + Vite SPA (`frontend/`, confirmed `docs/UI.md:3`). i18n recommendations
below assume React reality, not the CLAUDE.md description.

---

## 2. Language-support matrix

Pipeline stage × current capability (cited) × gap × fix. "Must-have" = needed for non-English
creators' *content* now; "Later" = product translation.

| Stage | Current capability (`file_path:line`) | Gap | Fix | Bucket |
|---|---|---|---|---|
| **Transcription (Deepgram, default)** | `nova-3`, no `language`/`detect_language` (`ingestion/transcribe.py:108`); backend default `deepgram` (`config.py:84`) | English-only; non-English audio transcribed as English → wrong text + timings | Pass `language="multi"` (Nova-3 Multilingual) or per-job detected language; persist result | **Must-have** |
| **Transcription (AssemblyAI fallback)** | `Transcriber().transcribe(audio_path)` no language config (`ingestion/transcribe.py:200`) | Defaults to English/auto per SDK; language not pinned or stored | Enable language detection; store detected language | Must-have |
| **Transcription (WhisperX fallback)** | Auto-detects language, used for alignment (`:251-252`); `WHISPER_MODEL=large-v3` (`config.py:100`) | Detection works but **language is discarded** (`:270`); CPU `large-v3` is slow | Surface + persist `result["language"]`; document perf | Must-have (capture) |
| **Forced alignment (word timing)** | WhisperX `load_align_model(result["language"])` (`:239,252`) | Default phoneme models exist for only ~20 langs `{en,fr,de,es,it,ja,zh,nl,uk,pt}`; outside that list **alignment fails → only segment-level timing** ([m-bain/whisperX](https://github.com/m-bain/whisperX), [WhisperX paper](https://www.robots.ox.ac.uk/~vgg/publications/2023/Bain23/bain23.pdf)) | No fidelity tier / honest degrade for unaligned languages | Define a supported-language tier; below it, fall back to phrase-level captions + warn | Must-have |
| **Audio heuristics (laughter/energy)** | RMS/ZCR thresholds, no language (`ingestion/audio.py:15-60`) | **Largely language-neutral** (acoustic, not lexical). Laughter ZCR heuristic is culturally rough but not English-biased | None required now; note as a known approximation | OK / note |
| **LLM — Creator Brief** | English prompt, no lang directive (`dna/brief.py:35-57`) | Output language not pinned to creator's language | Add "respond in {language}" derived from stored language; keep instructions in English | Must-have |
| **LLM — titles / hooks / chat / scoring rationale** | English-only (`knowledge/titles.py`, `knowledge/hooks.py`, `chat/prompt.py`, `clip_engine/scoring.py`) | Titles/hooks should be in the creator's language; rationale should match UI later | Same pattern: target-language output directive, instructions stay English | Must-have (titles/hooks); Later (rationale follows UI) |
| **Captions — font** | `fontname="Anton"` (`clip_engine/captions.py:45`); only Latin fonts in image (`Dockerfile:13-19`) | CJK/Arabic/Devanagari render as tofu | Ship Noto CJK/Arabic/Devanagari; select font by detected script | Must-have |
| **Captions — shaping/RTL** | libass via `subtitles=…:fontsdir=` (`clip_engine/render.py:219`); HarfBuzz handles BiDi/shaping if font present | RTL (Arabic/Hebrew) BiDi works in libass but untested here; alignment `an2/an5` may need review | Verify RTL render once a covering font ships; add an eval scenario | Must-have (verify) |
| **YouTube language signal** | playlistItems `part=snippet` (`youtube/data_api.py:166`); videos `part=contentDetails` only (`:202`) | `snippet.defaultAudioLanguage` (a free language hint) never requested/stored ([Videos: Data API](https://developers.google.com/youtube/v3/docs/videos)) | Request `part=snippet` on videos; capture `defaultAudioLanguage` as a detection prior | Must-have (cheap) |
| **Product UI strings** | Hardcoded English, no i18n lib (`frontend/src/pages/*.tsx`, `frontend/package.json`) | No translation, no locale detection | react-i18next + ICU (see §3.2) | **Later** |
| **Product UI typography / RTL layout** | Geist/Inter Latin-only (`docs/UI.md:114-116`); no `dir`/logical CSS props | Non-Latin UI text + RTL layout unsupported | Noto UI fonts + `dir="rtl"` + logical CSS properties | Later |

---

## 3. Recommendations

### 3.1 Content pipeline (priority — do these for the beta)

1. **Make transcription language-aware and capture the language (foundation for everything
   else).** Switch the Deepgram default call to detect/handle multiple languages
   (`language="multi"` for Nova-3 Multilingual, which covers en/es/fr/de/hi/it/ja/nl/ru/pt with a
   ~34% batch WER reduction and strong code-switching —
   [Nova-3 multilingual](https://deepgram.com/learn/nova-3-multilingual-major-wer-improvements-across-languages)),
   read the per-result detected language, and **persist a `language` (BCP-47) field on the
   transcript**. Add `language` to the normalized schema (`ingestion/transcribe.py:9-12`) so all
   three backends emit it. **DECISIONS entry required:** `nova-3` monolingual→`multi`, or
   detect-then-pin, is a model-config change with cost/accuracy tradeoffs (single-language `nova-2`
   can beat `nova-3 multi` on a known single language —
   [Deegram discussion #1206](https://github.com/orgs/deepgram/discussions/1206)).

2. **Define a supported-language tier and degrade honestly below it.** Word-level alignment (the
   setup-not-aftermath foundation) is only reliable for languages with a phoneme alignment model
   (~20 for WhisperX; Deepgram word timings track its own supported set). Publish a tier:
   **Tier 1** (full word-level captions + analysis) for confirmed languages; **Tier 2** (transcribe
   + analyze, phrase-level captions only, with a visible notice); **Unsupported** (decline or warn).
   The caption renderer already falls back to phrase-level `minimal` when word timing is absent
   (`clip_engine/captions.py:175-176,261-284`) — wire the tier to surface that honestly instead of
   silently. **DECISIONS entry:** the tier list and the honest-degrade UX copy.

3. **Ship script-covering fonts and select by detected script.** Add Noto Sans CJK, Noto Naskh
   Arabic, and Noto Sans Devanagari to the `Dockerfile` font install (`Dockerfile:13-19`) and choose
   the caption `fontname` (`clip_engine/captions.py:45`) by the detected language/script. Anton stays
   the Latin default. Add an eval scenario per script (incl. one RTL) under `tests/eval/scenarios/`
   per the project's caption-eval rule. **DECISIONS entry:** font choices (license + per-style
   substitution, since Anton's display weight has no CJK equivalent).

4. **Pin LLM output to the creator's language.** Add a single directive to the brief/titles/hooks
   prompts: keep instructions in English, append "Write your output in {language_name}." derived from
   the stored language ([ACL 2025](https://aclanthology.org/2025.naacl-short.55.pdf)). Do **not**
   translate the prompts themselves. The honesty hedge language must survive translation — verify the
   structural no-virality test (Issue 53) still passes against translated rationale. **DECISIONS
   entry:** prompt-language policy.

5. **Capture the free YouTube language hint.** Request `part=snippet` on the videos endpoint
   (`youtube/data_api.py:202`) and store `defaultAudioLanguage` as a *prior* for detection (not a
   substitute — creators mis-tag it). Cheap, ToS-clean, reduces detection error.

### 3.2 Product UI i18n (defer — recommend architecture now, build when a market is chosen)

- **Library: react-i18next + `i18next-icu`.** It is the industry-leading React i18n stack (~8M
  weekly downloads vs react-intl's ~4M, ~15KB vs ~45KB gzipped), has first-class
  `i18next-browser-languagedetector` for locale detection, lazy namespace loading, and ICU plural
  support via the `i18next-icu` plugin ([Locize comparison](https://www.locize.com/blog/react-intl-vs-react-i18next/),
  [PkgPulse 2026](https://www.pkgpulse.com/blog/best-i18n-libraries-react-2026)). react-intl/FormatJS
  is the alternative if maximal ICU-standard formatting is the priority; for this app react-i18next's
  ecosystem + detection wins.
- **Architecture:** extract strings to `locales/{lang}/common.json`; ICU message format for
  plurals/dates/numbers; `i18next-browser-languagedetector` (querystring → cookie → `navigator`);
  default + fallback `en`. For RTL, set `dir="rtl"` on `<html>` and migrate spacing to **CSS logical
  properties** (`margin-inline`, `padding-inline`) — retrofitting RTL later is expensive, so adopt
  logical properties opportunistically even before translating
  ([2025 i18n best practices](https://www.smartling.com/blog/i18n)). **DECISIONS entry** when adopted.
- **Worth it now?** No, for a closed English-speaking beta. The content fixes (§3.1) unblock
  non-English *creators* regardless of UI language; UI translation is gated on a chosen target
  market (see §5).

---

## 4. Proposed issues (dependency-ordered, `docs/issues.md` house style)

> Each is small and independently shippable. Numbering continues the backlog (last is Issue 165);
> Issue 179 is the tracking umbrella in the research prompt — these are its children. Confirm exact
> numbers against `docs/issues.md` at filing time.

### Issue 179a: Capture + persist detected source language on every transcript
**Severity:** SEV-2 — foundation; all multilingual routing depends on it
**Depends on:** nothing
**What:** Add a `language` (BCP-47) field to the normalized transcript schema
(`ingestion/transcribe.py:9-12`) and a `language` column on the transcript model + Alembic
migration. WhisperX already detects it (`:252`) — stop discarding it; enable + read detection on
Deepgram/AssemblyAI normalizers.
**Acceptance criteria:**
- [ ] Phase 1 research confirmed (this brief) — links cited
- [ ] All three backends populate `language`; `None`/`"unknown"` when undetected
- [ ] Migration adds nullable `language` column; per-creator isolation unaffected
- [ ] Tests: each backend normalizer returns the detected language (recorded fixtures, no live API)
- [ ] **`docs/DECISIONS.md`** entry: schema addition + BCP-47 normalization choice

### Issue 179b: Language-aware Deepgram/AssemblyAI transcription
**Severity:** SEV-1 — non-English audio currently transcribed as English
**Depends on:** 179a
**What:** Stop hardcoding English-defaulting config (`ingestion/transcribe.py:108`). Use
`language="multi"` (Nova-3 Multilingual) or detect-then-pin; enable AssemblyAI language detection.
**Acceptance criteria:**
- [ ] Phase 1 research: cost/accuracy of `nova-3 multi` vs detect-then-`nova-2`, cited
- [ ] Non-English fixture transcribes in-language with sane word timings (recorded fixture)
- [ ] English regression unchanged (existing eval green)
- [ ] **`docs/DECISIONS.md`** entry: model-config change + rationale

### Issue 179c: Supported-language tier + honest degradation
**Severity:** SEV-1 — honesty-constraint compliance for unsupported languages
**Depends on:** 179a, 179b
**What:** Publish Tier 1 / Tier 2 / Unsupported tiers keyed on alignment availability; surface a
visible notice when a clip is phrase-level-only or the language is unsupported; never silently
mis-cut. Wire to the existing phrase-level caption fallback (`clip_engine/captions.py:175-176`).
**Acceptance criteria:**
- [ ] Tier list defined and cited; UX copy honest (no virality, no false confidence)
- [ ] Tier-2 video produces phrase-level captions + a surfaced notice
- [ ] Unsupported video declines or warns rather than producing a confident wrong clip
- [ ] Eval scenario per tier under `tests/eval/scenarios/`
- [ ] **`docs/DECISIONS.md`** entry: tier definitions + degrade policy

### Issue 179d: Multilingual caption fonts + script-based font selection
**Severity:** SEV-1 — non-Latin captions render as tofu today
**Depends on:** 179a
**What:** Add Noto CJK/Arabic/Devanagari to `Dockerfile:13-19`; select `fontname`
(`clip_engine/captions.py:45`) by detected script; keep Anton for Latin. Verify RTL render.
**Acceptance criteria:**
- [ ] Phase 1 research: font coverage + licenses (SIL OFL), cited
- [ ] CJK / Arabic (RTL) / Devanagari clips render correctly (eval scenarios, incl. one RTL)
- [ ] Latin output byte-identical to current (no regression)
- [ ] **`docs/DECISIONS.md`** entry: font choices + per-script substitution

### Issue 179e: Pin LLM output language to the creator's content language
**Severity:** SEV-2 — analysis/titles/hooks should be in-language
**Depends on:** 179a
**What:** Add a target-language output directive to brief/titles/hooks/chat prompts
(`dna/brief.py:35-57`, `knowledge/titles.py`, `knowledge/hooks.py`, `chat/prompt.py`); keep
instructions in English; do not translate prompts.
**Acceptance criteria:**
- [ ] Brief/titles/hooks respond in the creator's language given a non-English transcript
- [ ] Honesty/hedge language preserved; no-virality structural test (Issue 53) still green
- [ ] Prompt-cache breakpoints unchanged (no new per-call variance before the breakpoint)
- [ ] **`docs/DECISIONS.md`** entry: prompt-language policy

### Issue 179f: Capture YouTube `defaultAudioLanguage` as a detection prior
**Severity:** SEV-3 — cheap accuracy boost
**Depends on:** 179a
**What:** Request `part=snippet` on the videos endpoint (`youtube/data_api.py:202`); store
`defaultAudioLanguage`; use as a prior (not override) for detection.
**Acceptance criteria:**
- [ ] `defaultAudioLanguage` captured where present; absence handled gracefully
- [ ] Used as a prior, not a hard override (creators mis-tag); test with recorded fixture
- [ ] ToS/quota: no new restricted scope; quota cost noted

### Issue 179g (LATER): Product UI i18n scaffold (react-i18next + ICU)
**Severity:** SEV-4 — deferred until a target market is chosen
**Depends on:** market decision (see §5)
**What:** Install react-i18next + `i18next-icu` + browser language detector; extract strings to
`locales/{lang}/common.json`; default+fallback `en`; adopt CSS logical properties for future RTL.
**Acceptance criteria:**
- [ ] Phase 1 research re-confirmed at build time (library currency)
- [ ] English strings extracted, zero visible change at `en`
- [ ] One non-English locale loads (proof of pipeline)
- [ ] RTL smoke: `dir="rtl"` flips layout via logical properties
- [ ] **`docs/DECISIONS.md`** entry: library + architecture; **and** flag the stale `CLAUDE.md`
      "vanilla HTML/CSS/JS" line for correction

---

## 5. Open questions for the human (one-line answers)

1. **Target languages for the beta?** (e.g. "es + pt only" lets us scope Tier 1 tightly and skip
   CJK/RTL font work for now.)
2. **Deepgram `nova-3 multi` for all jobs, or detect-then-pin a single-language model per video?**
   (Cost vs accuracy tradeoff — affects 179b.)
3. **Translate the product UI now, or stay English-only until a non-English market is targeted?**
   (Gates 179g; default recommendation: defer.)
4. **For an unsupported language, decline the job or produce a clearly-flagged best-effort clip?**
   (Sets the 179c degrade policy.)
5. **Is the `CLAUDE.md` "vanilla HTML/CSS/JS" line going to be corrected to React/TS now, or tracked
   separately?** (Doc-accuracy flag, independent of this work.)
