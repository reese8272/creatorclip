# Superchat-band fixtures (Issue 448)

Frozen 2026-08-11 from video `7e988321-2265-4e22-85bd-0e9ffd583f84` ("2026-08-05 07-59-55",
Backboard Media) **before its source media purged at 2026-08-13 19:23 UTC**. That source is gone
now — these frames are the only surviving reproduction of the defect, so do not delete them.

## What the defect is

A YouTube superchat banner is drawn by the streaming software **inside** the detected camera
region, so the camera-region crop (Issues 439/443) cannot exclude it and it renders burned into
the clip. The region itself is correct — it excludes the SUBSCRIBE button, the `@WSHCARTER`
socials strip and the logos. The superchat simply sits on top of the camera feed.

## Format

Grayscale PNGs, **480 px wide** — deliberately identical to `camera_region._ANALYSIS_WIDTH` and
its `format=gray` sampling, so a stack loaded with `cv2.imread(..., IMREAD_GRAYSCALE)` is the same
shape the real detector consumes (`_sample_gray_frames` returns a list of 2-D uint8 arrays).
Sampled at **1 Hz**; the trailing `_NN` is the frame index, so index *i* is `t0 + i` seconds in
SOURCE time.

| Set | Source span | Ground truth |
|---|---|---|
| `onset_879_890_band_from_885_*` | 879–890 s | clean for 6 frames, band from **885 s** |
| `offset_1000_1011_band_until_1006_*` | 1000–1011 s | band for 7 frames, clean from **1006 s** |
| `clean_1139_1150_*` | 1139–1150 s | no band at any frame |

## Measured ground truth (whole-source scan, 1 Hz)

Superchat present in **two spans — 885–914 s (30 s) and 930–1006 s (77 s) — 107 s of 1617 s, or
6.6 % of the runtime.** Two of the nine rendered clips carry one: rank 3 for **11.6 s (13.8 % of
the clip)** and rank 13 for **25.3 s (28.1 %)**.

## The feature that separates them

Row-mean brightness in the lower 40 % of the frame, counted against that frame's own lower-region
median (`rows where row_mean > median + 25`):

- clean frames: **14–20** bright rows
- band frames: **26–28** bright rows

A threshold of **24** classifies all 36 fixture frames correctly with margin on both sides.

**Do not measure this on a rendered clip.** The burned-in captions are themselves a bright
lower-frame band and produce false positives — verified 2026-08-11, when a rendered-clip scan
flagged ranks 1 and 6, both of which are visually clean. Detection must run on the source, before
captions and before the region pre-crop.
