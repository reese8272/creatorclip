/**
 * CropTrackOverlay (L26 Issue 426) — the framing mini-map.
 *
 * 80/20: the honest labels, the pointer-events guarantee, the cut ticks, the
 * transform being written through the imperative handle (never React state),
 * and the structural no-setState/no-canvas invariant asserted against the
 * component source itself.
 */
import { render, screen } from '@testing-library/react'
import { createRef } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { CropTrackOverlay } from './CropTrackOverlay'
// Vite raw import: the structural invariant is about the SOURCE, not the DOM.
import overlaySource from './CropTrackOverlay.tsx?raw'
import type { CropTrack } from '@/types'
import type { VideoPlayerHandle } from '@/components/ui/video-player'

const TRACK: CropTrack = {
  version: 1,
  mode: 'speaker_cut',
  source: { width: 1920, height: 1080 },
  crop: { width: 608, height: 1080 },
  origin_s: 410,
  duration_s: 38,
  keyframes: [
    { t: 0, x: 120 },
    { t: 18, x: 240 },
    { t: 18.4, x: 960 },
    { t: 38, x: 960 },
  ],
  cuts: [{ t: 18.4, from_x: 240, to_x: 960, speaker: 1 }],
  shots: [{ t: 18.4 }],
  speakers: { count: 2, mapping_confidence: 0.74 },
  meta: { sample_fps: 5, fallback: false },
}

function fakeHandle(currentTime: number): VideoPlayerHandle {
  return {
    play: async () => {},
    pause: () => {},
    seek: () => {},
    seekBy: () => {},
    getCurrentTime: () => currentTime,
    getDuration: () => 38,
    setRate: () => {},
    subscribeTime: vi.fn(() => () => {}),
    element: null,
  }
}

function renderOverlay(track: CropTrack, t = 0) {
  const handleRef = createRef<VideoPlayerHandle | null>()
  ;(handleRef as { current: VideoPlayerHandle | null }).current = fakeHandle(t)
  return render(<CropTrackOverlay track={track} handleRef={handleRef} />)
}

describe('CropTrackOverlay', () => {
  it('labels AI framing, and Framing: centered on the fallback rung', () => {
    renderOverlay(TRACK)
    expect(screen.getByText('AI framing')).toBeInTheDocument()

    renderOverlay({ ...TRACK, mode: 'static', meta: { sample_fps: 5, fallback: true } })
    expect(screen.getByText('Framing: centered')).toBeInTheDocument()
  })

  it('never intercepts pointers', () => {
    renderOverlay(TRACK)
    expect(screen.getByTestId('crop-track-overlay').className).toContain('pointer-events-none')
  })

  it('draws one tick per cut on the progress line', () => {
    renderOverlay(TRACK)
    expect(screen.getAllByTestId('crop-cut-tick')).toHaveLength(1)
  })

  it('writes the initial window position from the handle, and subscribes for updates', () => {
    const handleRef = createRef<VideoPlayerHandle | null>()
    const handle = fakeHandle(18.4) // at the cut → x = 960
    ;(handleRef as { current: VideoPlayerHandle | null }).current = handle
    const { container } = render(<CropTrackOverlay track={TRACK} handleRef={handleRef} />)

    // x=960 of maxX=1312 over the map's travel range: transform is written
    // directly on the element — no state, no re-render.
    const win = container.querySelector('[style*="translateX"]') as HTMLElement
    expect(win).not.toBeNull()
    expect(win.style.transform).toMatch(/translateX\(\d+(\.\d+)?px\)/)
    expect(handle.subscribeTime).toHaveBeenCalledTimes(1)
  })

  it('structural: no React state and no canvas in the animation path', () => {
    expect(overlaySource).not.toMatch(/useState|useReducer/)
    expect(overlaySource).not.toContain('<canvas')
  })
})
