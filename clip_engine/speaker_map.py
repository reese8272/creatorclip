"""
Speaker→face mapping for the speaker-aware reframe (Issue 420).

PURE MODULE: no mediapipe, no cv2, no subprocess — every function is
synthetic-testable (mediapipe is not installed on the dev box and stays a
lazy import inside clip_engine/reframe.py's detection pass). The only
optional dependency is numpy for the mouth-patch diffs, imported lazily.

Pipeline position: reframe.py's detection pass produces per-sample
:class:`FaceObs` lists; this module clusters them into per-shot
:class:`FaceTrack`s, extracts diarized speaker :class:`Turn`s from the
transcript, votes speakers onto tracks, and chooses the reframe mode.

Mapping approach (DECISIONS 2026-08-04, decision 6): co-occurrence +
largest-during-turn + mouth-motion-energy voting with margin-ratio
confidence — NOT a TalkNet-class audio-visual model, which was explicitly
rejected as overkill for the ≤100-user beta.

Fallback ladder (the single source of truth, applied in
:func:`choose_reframe_mode`):
  Rung A ``speaker_cut`` — ≥2 tracks, ≥2 speakers, ≥60% diarization
      coverage, mapping confidence ≥0.3, cuts enabled.
  Rung B ``face_pan``   — ≥1 face track (largest-face EMA pan).
  Rung C ``static``     — no faces, or nothing to pan (crop ≥ frame width).
Every ambiguity rung degrades TOWARD today's behavior, never past it; an
off-screen speaker holds the current framing (never cut to nothing).

Cites Principle 4 (Pattern interrupt — framing follows the conversation) and
Principle 11 (Audience-fit — per-creator craft, not a generic auto-crop).
"""

from __future__ import annotations

import statistics
from bisect import bisect_right
from dataclasses import dataclass, field

# ── Ladder thresholds (rung A gates; see choose_reframe_mode) ────────────────
MIN_DIARIZATION_COVERAGE = 0.6
MIN_MAPPING_CONFIDENCE = 0.3

# Greedy nearest-neighbor face-track association: a detection joins an open
# track only when its center moved less than this fraction of the frame width
# between consecutive samples (talking heads drift slowly; a bigger jump is a
# different face or a missed shot boundary).
TRACK_MATCH_MAX_FRAC = 0.15
# Tracks observed in fewer than this fraction of their shot's sampled frames
# are discarded as noise (background faces, false positives).
TRACK_MIN_COVERAGE = 0.20

# Turn shaping (industry-standard diarization post-processing figures):
# same-speaker gaps under 0.4s are breaths, not turn changes; a foreign turn
# under 0.8s sandwiched inside one speaker's flow is a backchannel ("mm-hm")
# and must not trigger a camera cut.
TURN_GAP_MERGE_S = 0.4
BACKCHANNEL_MAX_S = 0.8

# Vote weights: mouth motion is the most discriminative visual speech cue we
# have without an AV model, so it carries double weight.
_W_COOCCURRENCE = 1.0
_W_LARGEST = 1.0
_W_MOUTH = 2.0


# ── Data types ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FaceObs:
    """One face detection at one sampled frame.

    ``keypoints`` are the six BlazeFace landmarks in pixel coordinates
    (right eye, left eye, nose tip, MOUTH CENTER, right tragion, left
    tragion). ``mouth_patch`` is a small fixed-size grayscale array cut
    around the mouth keypoint at detection time — consecutive-patch diffs
    are the mouth-motion-energy signal. Both optional: synthetic tests and
    degraded detections omit them.
    """

    t: float
    cx: float
    cy: float
    w: float
    h: float
    keypoints: tuple[tuple[float, float], ...] = ()
    mouth_patch: object | None = None

    @property
    def area(self) -> float:
        return self.w * self.h


@dataclass
class FaceTrack:
    """A face followed through consecutive samples within ONE shot.

    Tracks never span a shot boundary (clip_engine/shots.py contract).
    """

    track_id: int
    shot_idx: int
    obs: list[FaceObs] = field(default_factory=list)

    @property
    def start_t(self) -> float:
        return self.obs[0].t

    @property
    def end_t(self) -> float:
        return self.obs[-1].t

    @property
    def mean_cx(self) -> float:
        return sum(o.cx for o in self.obs) / len(self.obs)

    @property
    def mean_area(self) -> float:
        return sum(o.area for o in self.obs) / len(self.obs)

    def median_cx(self, start_t: float | None = None, end_t: float | None = None) -> float:
        """Median center-x over observations in ``[start_t, end_t)`` — the
        virtual-tripod hold position (Issue 436). Median, not mean: a single
        detector outlier in a ~6-obs segment window would drag a mean but
        leaves the median untouched. An empty window falls back to the
        whole-track median (a segment can open before its track's first obs)."""
        xs = [
            o.cx
            for o in self.obs
            if (start_t is None or o.t >= start_t) and (end_t is None or o.t < end_t)
        ]
        if not xs:
            xs = [o.cx for o in self.obs]
        return float(statistics.median(xs))


@dataclass(frozen=True)
class Turn:
    """A contiguous span of one diarized speaker talking (source-relative s)."""

    speaker: int
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class SpeakerMapping:
    """Speaker→track assignments, keyed per shot (tracks are per-shot).

    ``confidence`` is the duration-weighted mean margin ratio
    ``(best − second_best) / best`` across all assignments — 1.0 when every
    speaker had one unambiguous candidate, 0.0 when nothing could be mapped.
    """

    assignments: dict[tuple[int, int], int]  # (shot_idx, speaker) -> track_id
    confidence: float

    def track_for(self, speaker: int, shot_idx: int) -> int | None:
        return self.assignments.get((shot_idx, speaker))


# ── Face tracks ──────────────────────────────────────────────────────────────


def shot_index_for(t: float, shot_changes: list[float]) -> int:
    """0-based shot index of time ``t`` given sorted shot-change times."""
    return bisect_right(shot_changes, t)


def build_face_tracks(
    obs_frames: list[tuple[float, list[FaceObs]]],
    frame_w: int,
    shot_changes: list[float],
    *,
    max_match_frac: float = TRACK_MATCH_MAX_FRAC,
    min_coverage: float = TRACK_MIN_COVERAGE,
) -> list[FaceTrack]:
    """Cluster per-sample detections into per-shot tracks (greedy NN on Δcx).

    ``obs_frames`` is the chronological detection output: one
    ``(sample_t, [FaceObs, …])`` entry per SAMPLED frame (empty list = no
    faces in that frame). Association is greedy nearest-neighbor on
    ``|Δcx| < max_match_frac·frame_w`` against each open track's last
    observation; tracks close at shot boundaries (never span them) and
    tracks covering under ``min_coverage`` of their shot's samples are
    discarded as noise.
    """
    max_delta = max_match_frac * frame_w
    tracks: list[FaceTrack] = []
    open_tracks: list[FaceTrack] = []
    current_shot = -1
    samples_per_shot: dict[int, int] = {}
    next_id = 0

    for sample_t, obs_list in obs_frames:
        shot_idx = shot_index_for(sample_t, shot_changes)
        if shot_idx != current_shot:
            open_tracks = []  # tracks never span a shot boundary
            current_shot = shot_idx
        samples_per_shot[shot_idx] = samples_per_shot.get(shot_idx, 0) + 1

        # Greedy best-pair matching: smallest |Δcx| first, each track and each
        # obs used at most once per frame.
        pairs: list[tuple[float, int, int]] = []
        for oi, obs in enumerate(obs_list):
            for ti, track in enumerate(open_tracks):
                delta = abs(obs.cx - track.obs[-1].cx)
                if delta < max_delta:
                    pairs.append((delta, oi, ti))
        pairs.sort()
        used_obs: set[int] = set()
        used_tracks: set[int] = set()
        for _, oi, ti in pairs:
            if oi in used_obs or ti in used_tracks:
                continue
            open_tracks[ti].obs.append(obs_list[oi])
            used_obs.add(oi)
            used_tracks.add(ti)
        for oi, obs in enumerate(obs_list):
            if oi in used_obs:
                continue
            track = FaceTrack(track_id=next_id, shot_idx=shot_idx, obs=[obs])
            next_id += 1
            tracks.append(track)
            open_tracks.append(track)

    kept = [
        t
        for t in tracks
        if t.obs and len(t.obs) / max(1, samples_per_shot.get(t.shot_idx, 1)) >= min_coverage
    ]
    return kept


# ── Speaker turns ────────────────────────────────────────────────────────────


def _speaker_words(
    segments: list[dict], start_s: float, end_s: float
) -> list[tuple[int, float, float]]:
    """Flatten transcript segments into speaker-labeled word spans in-window.

    Word-level ``speaker`` wins; a word without one inherits its segment's
    ``speaker``; words with neither are skipped (WhisperX / old transcripts).
    """
    out: list[tuple[int, float, float]] = []
    for seg in segments:
        seg_speaker = seg.get("speaker")
        for w in seg.get("words") or []:
            w_start, w_end = w.get("start"), w.get("end")
            if w_start is None or w_end is None:
                continue
            if w_end <= start_s or w_start >= end_s:
                continue
            speaker = w.get("speaker", seg_speaker)
            if speaker is None:
                continue
            out.append((int(speaker), float(w_start), float(w_end)))
    out.sort(key=lambda x: x[1])
    return out


def _merge_same_speaker_gaps(turns: list[Turn], gap_merge_s: float) -> list[Turn]:
    merged: list[Turn] = []
    for turn in turns:
        if (
            merged
            and merged[-1].speaker == turn.speaker
            and turn.start - merged[-1].end < gap_merge_s
        ):
            merged[-1] = Turn(turn.speaker, merged[-1].start, max(merged[-1].end, turn.end))
        else:
            merged.append(turn)
    return merged


def extract_speaker_turns(
    segments: list[dict],
    start_s: float,
    end_s: float,
    *,
    gap_merge_s: float = TURN_GAP_MERGE_S,
    backchannel_max_s: float = BACKCHANNEL_MAX_S,
) -> list[Turn]:
    """Diarized words → clean speaker turns for the cut planner.

    Three shaping passes: (1) group consecutive same-speaker words,
    (2) merge same-speaker turns separated by under ``gap_merge_s``,
    (3) ABSORB backchannels — a foreign turn shorter than
    ``backchannel_max_s`` sandwiched between two turns of the same other
    speaker is dropped and its flanks merged, so an "mm-hm" never triggers
    a camera cut. Returns ``[]`` when the transcript carries no speaker
    labels (a defined ladder input → face_pan/static).
    """
    words = _speaker_words(segments, start_s, end_s)
    if not words:
        return []

    turns: list[Turn] = []
    for speaker, w_start, w_end in words:
        if turns and turns[-1].speaker == speaker and w_start - turns[-1].end < gap_merge_s:
            turns[-1] = Turn(speaker, turns[-1].start, max(turns[-1].end, w_end))
        else:
            turns.append(Turn(speaker, w_start, w_end))

    # Backchannel absorption (repeat until stable — nested backchannels are
    # rare but cheap to handle).
    changed = True
    while changed:
        changed = False
        for i in range(1, len(turns) - 1):
            prev, cur, nxt = turns[i - 1], turns[i], turns[i + 1]
            if (
                cur.duration < backchannel_max_s
                and prev.speaker == nxt.speaker
                and cur.speaker != prev.speaker
            ):
                turns[i - 1 : i + 2] = [Turn(prev.speaker, prev.start, nxt.end)]
                changed = True
                break

    return _merge_same_speaker_gaps(turns, gap_merge_s)


def diarization_coverage(segments: list[dict], start_s: float, end_s: float) -> float:
    """Fraction of in-window SPEECH time carrying a speaker label ∈ [0, 1].

    Word-duration based (not wall-clock) so dead air doesn't dilute the
    figure. 0.0 when the window has no timed words at all.
    """
    labeled = 0.0
    total = 0.0
    for seg in segments:
        seg_speaker = seg.get("speaker")
        for w in seg.get("words") or []:
            w_start, w_end = w.get("start"), w.get("end")
            if w_start is None or w_end is None:
                continue
            overlap = min(float(w_end), end_s) - max(float(w_start), start_s)
            if overlap <= 0:
                continue
            total += overlap
            if w.get("speaker", seg_speaker) is not None:
                labeled += overlap
    return labeled / total if total > 0 else 0.0


# ── Mouth-motion energy ──────────────────────────────────────────────────────


def mouth_motion_energy(obs: list[FaceObs], start_s: float, end_s: float) -> float:
    """Mean absolute inter-patch difference over ``[start_s, end_s]`` ∈ [0, 1].

    The patches are small fixed-size grayscale arrays cut around BlazeFace's
    mouth keypoint on frames the detection pass already decoded — a talking
    mouth churns pixel values, a listening mouth doesn't. Returns 0.0 when
    fewer than two patched observations fall in the window (no evidence,
    never an error).
    """
    patches = [o.mouth_patch for o in obs if start_s <= o.t <= end_s and o.mouth_patch is not None]
    if len(patches) < 2:
        return 0.0
    try:
        import numpy as np

        diffs = [
            float(
                np.mean(np.abs(np.asarray(a, dtype=np.float64) - np.asarray(b, dtype=np.float64)))
            )
            for a, b in zip(patches, patches[1:], strict=False)
            if np.asarray(a).shape == np.asarray(b).shape
        ]
        if not diffs:
            return 0.0
        return min(1.0, (sum(diffs) / len(diffs)) / 255.0)
    except Exception:
        return 0.0


# ── Speaker → track voting ───────────────────────────────────────────────────


def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def map_speakers_to_tracks(
    tracks: list[FaceTrack],
    turns: list[Turn],
    *,
    w_cooccurrence: float = _W_COOCCURRENCE,
    w_largest: float = _W_LARGEST,
    w_mouth: float = _W_MOUTH,
) -> SpeakerMapping:
    """Vote each speaker onto a face track, per shot (tracks are per-shot).

    Per (speaker, track) score, summed over the speaker's turns weighted by
    turn duration:
      - CO-OCCURRENCE: fraction of the turn the track is on screen;
      - LARGEST-DURING-TURN: 1.0 for the biggest on-screen track (camera
        framing convention: the active speaker gets the tighter shot);
      - MOUTH MOTION: the track's mouth energy normalized by the max among
        candidates in that turn (double weight — the most discriminative cue).
    Greedy assignment per shot, speakers in descending speech time, each
    track used once. Confidence = duration-weighted mean margin ratio
    ``(best − second)/best``.
    """
    if not tracks or not turns:
        return SpeakerMapping(assignments={}, confidence=0.0)

    assignments: dict[tuple[int, int], int] = {}
    margin_weight_sum = 0.0
    weighted_margin = 0.0

    shot_indices = sorted({t.shot_idx for t in tracks})
    for shot_idx in shot_indices:
        shot_tracks = [t for t in tracks if t.shot_idx == shot_idx]
        # Speakers active during this shot's track extent.
        shot_start = min(t.start_t for t in shot_tracks)
        shot_end = max(t.end_t for t in shot_tracks)
        speech_time: dict[int, float] = {}
        for turn in turns:
            ov = _overlap(turn.start, turn.end, shot_start, shot_end)
            if ov > 0:
                speech_time[turn.speaker] = speech_time.get(turn.speaker, 0.0) + ov
        if not speech_time:
            continue

        scores: dict[int, dict[int, float]] = {spk: {} for spk in speech_time}
        for turn in turns:
            span = _overlap(turn.start, turn.end, shot_start, shot_end)
            if span <= 0:
                continue
            energies = {
                t.track_id: mouth_motion_energy(t.obs, turn.start, turn.end) for t in shot_tracks
            }
            max_energy = max(energies.values(), default=0.0)
            on_screen = [
                t for t in shot_tracks if _overlap(t.start_t, t.end_t, turn.start, turn.end) > 0
            ]
            largest_id = max(on_screen, key=lambda t: t.mean_area).track_id if on_screen else None
            for t in shot_tracks:
                cooccur = _overlap(t.start_t, t.end_t, turn.start, turn.end) / max(
                    turn.duration, 1e-9
                )
                largest = 1.0 if t.track_id == largest_id else 0.0
                mouth = energies[t.track_id] / max_energy if max_energy > 0 else 0.0
                vote = w_cooccurrence * cooccur + w_largest * largest + w_mouth * mouth
                scores[turn.speaker][t.track_id] = (
                    scores[turn.speaker].get(t.track_id, 0.0) + vote * span
                )

        taken: set[int] = set()
        for speaker in sorted(speech_time, key=lambda s: -speech_time[s]):
            candidates = {
                tid: sc for tid, sc in scores.get(speaker, {}).items() if tid not in taken
            }
            if not candidates:
                continue
            ranked = sorted(candidates.items(), key=lambda kv: -kv[1])
            best_id, best_score = ranked[0]
            if best_score <= 0:
                continue
            second_score = ranked[1][1] if len(ranked) > 1 else 0.0
            margin = (best_score - second_score) / best_score
            assignments[(shot_idx, speaker)] = best_id
            taken.add(best_id)
            weighted_margin += margin * speech_time[speaker]
            margin_weight_sum += speech_time[speaker]

    confidence = weighted_margin / margin_weight_sum if margin_weight_sum > 0 else 0.0
    return SpeakerMapping(assignments=assignments, confidence=confidence)


# ── Fallback ladder ──────────────────────────────────────────────────────────


def choose_reframe_mode(
    *,
    n_tracks: int,
    n_speakers: int,
    coverage: float,
    mapping_confidence: float,
    cuts_enabled: bool,
    frame_w: int,
    crop_w: int,
    min_mapping_confidence: float = MIN_MAPPING_CONFIDENCE,
) -> str:
    """The fallback ladder — the single source of truth for reframe depth.

    Rung A ``speaker_cut``: cuts enabled AND ≥2 tracks AND ≥2 speakers AND
        diarization coverage ≥ MIN_DIARIZATION_COVERAGE AND mapping
        confidence ≥ ``min_mapping_confidence`` (default the module floor;
        the render pipeline passes ``settings.REFRAME_MIN_MAPPING_CONFIDENCE``
        — the 2026-08-05 staging drill measured 0.248 on a real side-by-side
        two-camera podcast where the mapping was visibly CORRECT: co-occurrence
        and size votes carry no signal when both faces are always on screen,
        so honest confidences run lower on exactly the layouts that most need
        cuts. The floor is env-tunable so staging evidence can calibrate it
        without a code change.)
    Rung B ``face_pan``: at least one face track (largest-face EMA pan —
        today's designed behavior, improved by per-shot smoothing resets).
    Rung C ``static``: no faces, or nothing to pan (crop as wide as the
        frame — vertical/square source).

    Bad inputs degrade DOWN the ladder, never sideways: a wrong speaker map
    can at worst produce Rung B behavior, which is today's flag-on design.
    """
    if crop_w >= frame_w:
        return "static"
    if (
        cuts_enabled
        and n_tracks >= 2
        and n_speakers >= 2
        and coverage >= MIN_DIARIZATION_COVERAGE
        and mapping_confidence >= min_mapping_confidence
    ):
        return "speaker_cut"
    if n_tracks >= 1:
        return "face_pan"
    return "static"
