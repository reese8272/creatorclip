import { describe, expect, it } from 'vitest'
import { isCropTrack, maxCropX, sampleCropX } from './cropTrack'
import type { CropTrack } from '@/types'

// Mirrors the e2e fixture's shape: 1920×1080 source, 608-wide crop
// (max left edge 1312), a pan span and one speaker-change cut at t=18.4.
const TRACK: CropTrack = {
  version: 1,
  mode: 'speaker_cut',
  source: { width: 1920, height: 1080 },
  crop: { width: 608, height: 1080 },
  origin_s: 410,
  duration_s: 38,
  keyframes: [
    { t: 0, x: 120 },
    { t: 8, x: 120 },
    { t: 12, x: 240 },
    { t: 18, x: 240 },
    { t: 18.4, x: 960 },
    { t: 38, x: 960 },
  ],
  cuts: [{ t: 18.4, from_x: 240, to_x: 960, speaker: 1 }],
  shots: [{ t: 18.4 }],
  speakers: { count: 2, mapping_confidence: 0.74 },
  meta: { sample_fps: 5, fallback: false },
}

describe('sampleCropX', () => {
  it('holds the first/last keyframe value outside the track', () => {
    expect(sampleCropX(TRACK, -1)).toBe(120)
    expect(sampleCropX(TRACK, 0)).toBe(120)
    expect(sampleCropX(TRACK, 38)).toBe(960)
    expect(sampleCropX(TRACK, 99)).toBe(960)
  })

  it('lerps between keyframes inside a pan span', () => {
    // Between {8,120} and {12,240}: halfway at t=10 → 180.
    expect(sampleCropX(TRACK, 10)).toBe(180)
    // Flat span holds its value.
    expect(sampleCropX(TRACK, 4)).toBe(120)
  })

  it('SNAPS at a cut — never lerps across the jump', () => {
    // The {18,240}→{18.4,960} span contains the cut marker: before the cut
    // time the window holds from_x, at/after it jumps to to_x.
    expect(sampleCropX(TRACK, 18.2)).toBe(240)
    expect(sampleCropX(TRACK, 18.4)).toBe(960)
    // And immediately after, plain keyframes carry on.
    expect(sampleCropX(TRACK, 20)).toBe(960)
  })

  it('clamps to the source frame', () => {
    const wild: CropTrack = {
      ...TRACK,
      keyframes: [
        { t: 0, x: -50 },
        { t: 10, x: 5000 },
      ],
      cuts: [],
    }
    expect(maxCropX(wild)).toBe(1312)
    expect(sampleCropX(wild, 0)).toBe(0)
    expect(sampleCropX(wild, 10)).toBe(1312)
  })

  it('returns 0 for an empty keyframe list', () => {
    expect(sampleCropX({ ...TRACK, keyframes: [], cuts: [] }, 5)).toBe(0)
  })
})

describe('isCropTrack', () => {
  it('accepts the contract shape and rejects a catch-all body', () => {
    expect(isCropTrack(TRACK)).toBe(true)
    expect(isCropTrack({})).toBe(false)
    expect(isCropTrack(null)).toBe(false)
    expect(isCropTrack({ ...TRACK, keyframes: [] })).toBe(false)
  })
})
