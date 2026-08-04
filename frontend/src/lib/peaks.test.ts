import { describe, expect, it } from 'vitest'
import { isWaveformPeaks, peakEnvelope, peaksDurationS, type WaveformPeaks } from './peaks'

// 16 kHz / 512 spp = 31.25 pairs per second, i.e. 0.032 s per pair.
function peaks(pairs: Array<[number, number]>): WaveformPeaks {
  return {
    version: 1,
    sample_rate: 16000,
    samples_per_pixel: 512,
    bits: 8,
    length: pairs.length,
    data: pairs.flat(),
  }
}

describe('peakEnvelope', () => {
  it('normalises 8-bit magnitudes to 0..1 using the larger of |min| and |max|', () => {
    // Full scale is 128 for 8-bit data.
    const env = peakEnvelope(peaks([[-128, 64]]), { startS: 0, endS: 1, buckets: 1 })
    expect(env).not.toBeNull()
    expect(env![0]).toBeCloseTo(1, 5)

    const half = peakEnvelope(peaks([[-16, 64]]), { startS: 0, endS: 1, buckets: 1 })
    expect(half![0]).toBeCloseTo(0.5, 5)
  })

  it('reduces by MAX, not mean — a transient must survive being zoomed out', () => {
    // One loud pair among quiet ones. Averaging would bury it; the whole point of
    // a waveform is finding exactly this.
    const p = peaks([
      [-2, 2],
      [-2, 2],
      [-128, 128],
      [-2, 2],
    ])
    const env = peakEnvelope(p, { startS: 0, endS: peaksDurationS(p), buckets: 1 })
    expect(env![0]).toBeCloseTo(1, 5)
  })

  it('windows to a sub-range — the clip timeline reads the source artifact', () => {
    // Four pairs: quiet, quiet, LOUD, quiet. Each pair is 0.032s.
    const p = peaks([
      [0, 0],
      [0, 0],
      [-128, 128],
      [0, 0],
    ])
    const perPair = 512 / 16000

    // A window over the first two pairs sees silence...
    const quiet = peakEnvelope(p, { startS: 0, endS: perPair * 2, buckets: 1 })
    expect(quiet![0]).toBe(0)

    // ...and a window over the third sees the transient.
    const loud = peakEnvelope(p, { startS: perPair * 2, endS: perPair * 3, buckets: 1 })
    expect(loud![0]).toBeCloseTo(1, 5)
  })

  it('fills every bucket when zoomed in past the stored resolution', () => {
    // BBC data cannot be zoomed in past what it was built at, so neighbouring
    // buckets repeat rather than interpolate — but none may be left undefined.
    const p = peaks([[-64, 64]])
    const env = peakEnvelope(p, { startS: 0, endS: peaksDurationS(p), buckets: 8 })
    expect(env).toHaveLength(8)
    expect([...env!].every((v) => v > 0)).toBe(true)
  })

  it('returns null rather than a flat zero array when there is nothing to draw', () => {
    // The distinction matters: null means "no data, say so", whereas an array of
    // zeros is a real measurement of silence.
    expect(peakEnvelope(null, { startS: 0, endS: 10, buckets: 4 })).toBeNull()
    expect(peakEnvelope(peaks([[0, 0]]), { startS: 0, endS: 0, buckets: 4 })).toBeNull()
    expect(peakEnvelope(peaks([[0, 0]]), { startS: 0, endS: 1, buckets: 0 })).toBeNull()
    // A window entirely past the end of the data.
    expect(peakEnvelope(peaks([[0, 0]]), { startS: 99, endS: 100, buckets: 4 })).toBeNull()
  })

  it('honours 16-bit full scale when a file declares it', () => {
    const p: WaveformPeaks = { ...peaks([[-32768, 16384]]), bits: 16 }
    const env = peakEnvelope(p, { startS: 0, endS: 1, buckets: 1 })
    expect(env![0]).toBeCloseTo(1, 5)
  })
})

describe('isWaveformPeaks', () => {
  it('rejects a payload whose data is shorter than length implies', () => {
    // Would silently render half a waveform — treat as absent instead.
    expect(isWaveformPeaks({ ...peaks([[-1, 1]]), length: 5 })).toBe(false)
  })

  it('rejects non-objects and missing fields', () => {
    expect(isWaveformPeaks(null)).toBe(false)
    expect(isWaveformPeaks('nope')).toBe(false)
    expect(isWaveformPeaks({ data: [1, 2] })).toBe(false)
    expect(isWaveformPeaks({ ...peaks([[-1, 1]]), sample_rate: 0 })).toBe(false)
  })

  it('accepts a well-formed payload', () => {
    expect(isWaveformPeaks(peaks([[-1, 1]]))).toBe(true)
  })
})
