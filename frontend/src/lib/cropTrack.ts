import type { CropTrack } from '@/types'

// Pure crop-track sampling (L26 Issue 426). One geometry definition shared
// with the render: `x` values are the exact clamped LEFT-edge sendcmd values
// in source pixels. Interpolation rule from the cross-track contract: lerp
// between keyframes; SNAP at cuts — a cut is a speaker-change jump, and
// lerping across it would show the crop window gliding through a hard cut
// that the render performs instantly.

/** Max left edge the crop window can take without leaving the source frame. */
export function maxCropX(track: CropTrack): number {
  return Math.max(0, track.source.width - track.crop.width)
}

/** Crop-window left edge (source px) at clip-relative time `t`. */
export function sampleCropX(track: CropTrack, t: number): number {
  const kfs = track.keyframes
  const hi = maxCropX(track)
  const clamp = (x: number) => Math.min(Math.max(x, 0), hi)
  if (kfs.length === 0) return 0
  if (t <= kfs[0].t) return clamp(kfs[0].x)
  const last = kfs[kfs.length - 1]
  if (t >= last.t) return clamp(last.x)

  let i = 0
  while (i < kfs.length - 1 && kfs[i + 1].t <= t) i++
  const k0 = kfs[i]
  const k1 = kfs[i + 1]

  // Snap at cuts: never lerp across a jump marker inside this span.
  const cut = track.cuts.find((c) => c.t > k0.t && c.t <= k1.t)
  if (cut) return clamp(t < cut.t ? cut.from_x : cut.to_x)

  const f = (t - k0.t) / (k1.t - k0.t)
  return clamp(k0.x + f * (k1.x - k0.x))
}

/** Minimal shape guard — a malformed body (e.g. a catch-all `{}`) renders nothing. */
export function isCropTrack(value: unknown): value is CropTrack {
  const t = value as CropTrack | null
  return Boolean(
    t &&
      Array.isArray(t.keyframes) &&
      t.keyframes.length > 0 &&
      Array.isArray(t.cuts) &&
      t.source != null &&
      t.source.width > 0 &&
      t.source.height > 0 &&
      t.crop != null &&
      t.crop.width > 0 &&
      t.duration_s > 0 &&
      t.meta != null,
  )
}
