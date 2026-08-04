// Waveform peak data (Issue 392).
//
// The server serves BBC `audiowaveform` JSON — the de-facto interchange format —
// from GET /videos/{id}/peaks. This module owns decoding it and reducing it to
// whatever bucket count a timeline needs.
//
// The ONE rule this file exists to enforce: when there are no peaks, callers get
// `null` and must draw an explicitly-labelled flat track. They must never fall
// back to generated amplitude. Drawing something plausible over a creator's own
// audio is the exact defect Issue 392 removes — the long-form timeline used to
// draw bars at `20 + ((i * 37) % 60)%` under a label saying "Source timeline".

/** BBC audiowaveform data format, version 1 (mono). */
export interface WaveformPeaks {
  version: number
  sample_rate: number
  /** Source samples per min/max pair — the resolution the file was built at. */
  samples_per_pixel: number
  /** 8 or 16. We always emit 8. */
  bits: number
  /** Number of min/max PAIRS; `data.length` is twice this. */
  length: number
  /** Interleaved min,max per index. */
  data: number[]
}

/** Full-scale magnitude for a given `bits`, used to normalise to 0..1. */
function fullScale(bits: number): number {
  return bits === 16 ? 32768 : 128
}

export function isWaveformPeaks(value: unknown): value is WaveformPeaks {
  if (typeof value !== 'object' || value === null) return false
  const p = value as Partial<WaveformPeaks>
  return (
    Array.isArray(p.data) &&
    typeof p.length === 'number' &&
    typeof p.samples_per_pixel === 'number' &&
    typeof p.sample_rate === 'number' &&
    p.sample_rate > 0 &&
    p.samples_per_pixel > 0 &&
    // The format's own invariant. A payload that fails it would silently render
    // half a waveform, so treat it as absent rather than draw a lie.
    p.data.length >= p.length * 2
  )
}

/** Seconds of audio covered by the whole file. */
export function peaksDurationS(peaks: WaveformPeaks): number {
  return (peaks.length * peaks.samples_per_pixel) / peaks.sample_rate
}

/**
 * Reduce `peaks` to `buckets` normalised 0..1 magnitudes over [startS, endS).
 *
 * Max-of-bucket, not mean: the correct level-of-detail reduction for an envelope
 * is the extreme, because averaging a waveform toward its mean flattens exactly
 * the transients a creator is scanning for.
 *
 * Returns null when the window has no data — the caller draws the flat track.
 */
export function peakEnvelope(
  peaks: WaveformPeaks | null | undefined,
  { startS, endS, buckets }: { startS: number; endS: number; buckets: number },
): Float32Array | null {
  if (!peaks || buckets <= 0 || !(endS > startS)) return null

  const perPair = peaks.samples_per_pixel / peaks.sample_rate // seconds per pair
  const scale = fullScale(peaks.bits)
  const firstPair = Math.max(0, Math.floor(startS / perPair))
  const lastPair = Math.min(peaks.length, Math.ceil(endS / perPair))
  if (lastPair <= firstPair) return null

  const out = new Float32Array(buckets)
  const pairsPerBucket = (lastPair - firstPair) / buckets

  for (let b = 0; b < buckets; b++) {
    const lo = firstPair + Math.floor(b * pairsPerBucket)
    // At least one pair per bucket even when zoomed in past the stored
    // resolution — BBC data cannot be zoomed in past what it was built at, so
    // neighbouring buckets legitimately repeat a value rather than interpolate.
    const hi = Math.max(lo + 1, firstPair + Math.floor((b + 1) * pairsPerBucket))
    let peak = 0
    for (let i = lo; i < hi && i < peaks.length; i++) {
      const min = Math.abs(peaks.data[i * 2] ?? 0)
      const max = Math.abs(peaks.data[i * 2 + 1] ?? 0)
      const amp = Math.max(min, max)
      if (amp > peak) peak = amp
    }
    out[b] = Math.min(1, peak / scale)
  }
  return out
}
