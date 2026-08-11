# Two-shot seat-selection fixtures (Issue 450)

Frozen 2026-08-11 from video `7e988321-2265-4e22-85bd-0e9ffd583f84`, rank 1's window
**[754.62, 789.17]**, **before the source purged at 2026-08-13 19:23 UTC**. This is the clip the
creator dropped with:

> "When Rio is talking (the guy on the right), it is on the man on the left (who is not talking)"

## Format

Grayscale PNGs, 480 px wide, 12 frames evenly spaced across the 34.55 s window, already
**cropped to the camera region** `(x=169, y=326, w=1704, h=551)` — i.e. region space, the same
coordinate system `compute_dynamic_crop` works in when a region is active (Issue 433: the region
pre-crop makes every downstream coordinate region-relative by construction).

## Ground truth, measured in the app container 2026-08-11

The branch question the issue was filed with is **settled: `hold_seats` RAN.** It did not bail.

| Quantity | Value |
|---|---|
| `len(tracks)` | **2** — gate 1 (`< 2 → None`) passed |
| track `median_cx` | **381.2** and **1257.5** (region space, width 1704) |
| `crop_w` | 309 — seats are 876 px apart, so they do not collapse to one framing |
| simultaneous-occupancy gate | passed (most sampled frames detect 2 faces) |
| `_seat_hold_plan` result | **1 hold point at x = 381**, 0 cuts |
| `n_speakers` (diarized) | **1** |
| `diarization_coverage` | **1.0** |
| `mapping.confidence` | **0.084** (threshold `REFRAME_MIN_MAPPING_CONFIDENCE = 0.2`) |
| `shot_changes` | 0 |
| chosen mode | `face_pan` |

**So the defect is the vote, not a gate.** `hold_seats` picks the per-span seat with
`Counter(_nearest_seat(obs[0].cx))`, and `obs[0]` is the **largest** detected face (cf.
`_raw_track_largest_face`). "Dominant seat" therefore means *the seat that was the biggest face in
the most samples* — a quantity uncorrelated with who is speaking. It chose x=381, the LEFT seat
(region width 1704, so left half is < 852), and the right seat is the one talking.

Note what was available and unused: diarization had **full coverage and exactly one speaker** for
the entire window. The engine knew someone was talking the whole time; it just could not map that
speaker to a face with confidence, and fell back to a size vote instead of a speech-aware one.

## Why the earlier root-cause claim was wrong

The first revision of Issue 450 said `hold_seats` bailed at `len(tracks) < 2`. That was inferred
from `speakers.count = 1` in the stored track JSON — but that field is
`speaker_count = len({t.speaker for t in turns})`, the **diarized-speaker** count, not the
face-track count, which the track JSON never records. The claim was withdrawn before this
measurement was taken, and the measurement confirms the withdrawal was correct: there were two
tracks all along.
