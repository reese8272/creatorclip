import { useEffect, useRef, type RefObject } from 'react'
import { maxCropX, sampleCropX } from '@/lib/cropTrack'
import type { CropTrack } from '@/types'
import type { VideoPlayerHandle } from '@/components/ui/video-player'

// CropTrackOverlay (L26 Issue 426) — the honest framing mini-map over the
// rendered 9:16: the 16:9 source rect, the accent-bordered crop window at its
// exact sendcmd position, cut ticks on a progress line, and a label that says
// what the framing IS (`AI framing`, or `Framing: centered` when the pipeline
// fell back to a static crop). No track → the stage renders nothing at all —
// absence is the honest launch norm, never a fake preview.
//
// Rendering rules (all load-bearing):
// - pointer-events-none on the root: the map may sit over the player but must
//   never intercept a click meant for it.
// - Pure divs, no canvas — the structural gates stay clean by construction.
// - Animation goes through `subscribeTime` + one rAF-coalesced write of
//   style.transform on refs. ZERO React state in the animation path: a 4Hz
//   timeupdate through setState would re-render the whole stage subtree four
//   times a second for a 112px map.

const MAP_W = 112

export function CropTrackOverlay({
  track,
  handleRef,
}: {
  track: CropTrack
  /** The stage player's imperative handle — read-only time source. */
  handleRef: RefObject<VideoPlayerHandle | null>
}) {
  const windowRef = useRef<HTMLDivElement>(null)
  const playheadRef = useRef<HTMLDivElement>(null)

  const mapH = Math.round((MAP_W * track.source.height) / track.source.width)
  const winW = Math.max(2, Math.round((MAP_W * track.crop.width) / track.source.width))
  // translateX range: the window's left edge maps [0, maxCropX] → [0, MAP_W - winW].
  const xScale = maxCropX(track) > 0 ? (MAP_W - winW) / maxCropX(track) : 0

  useEffect(() => {
    const handle = handleRef.current
    if (!handle) return
    let raf: number | null = null
    const write = () => {
      raf = null
      const t = handle.getCurrentTime()
      const win = windowRef.current
      if (win) win.style.transform = `translateX(${sampleCropX(track, t) * xScale}px)`
      const head = playheadRef.current
      if (head) {
        const frac = track.duration_s > 0 ? Math.min(t / track.duration_s, 1) : 0
        head.style.transform = `translateX(${frac * MAP_W}px)`
      }
    }
    const schedule = () => {
      if (raf === null) raf = requestAnimationFrame(write)
    }
    write() // paint the initial position without waiting for a timeupdate
    const unsubscribe = handle.subscribeTime(schedule)
    return () => {
      unsubscribe()
      if (raf !== null) cancelAnimationFrame(raf)
    }
  }, [track, handleRef, xScale])

  return (
    <div
      data-testid="crop-track-overlay"
      className="pointer-events-none absolute left-2 top-2 flex flex-col gap-1"
    >
      {/* Source rect + crop window */}
      <div
        className="relative overflow-hidden rounded-sm border border-default bg-black/60"
        style={{ width: MAP_W, height: mapH }}
      >
        <div
          ref={windowRef}
          className="absolute inset-y-0 left-0 rounded-xs border border-accent-border bg-accent-soft/50"
          style={{ width: winW }}
        />
      </div>
      {/* Progress line: playhead + cut ticks */}
      <div className="relative h-1 rounded-full bg-black/60" style={{ width: MAP_W }}>
        {track.cuts.map((cut, i) => (
          <span
            key={i}
            data-testid="crop-cut-tick"
            className="absolute inset-y-0 w-px bg-accent-text"
            style={{
              left: track.duration_s > 0 ? (Math.min(cut.t / track.duration_s, 1) * MAP_W) : 0,
            }}
          />
        ))}
        <div ref={playheadRef} className="absolute inset-y-0 left-0 w-0.5 rounded-full bg-fg" />
      </div>
      <span className="w-fit rounded-xs bg-black/60 px-1.5 py-0.5 text-label text-muted">
        {track.meta.fallback ? 'Framing: centered' : 'AI framing'}
      </span>
    </div>
  )
}
