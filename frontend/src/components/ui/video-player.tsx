import { useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
import type { Ref } from 'react'

import { cn } from '@/lib/utils'
import { isModalOpen, isTypingTarget } from '@/lib/keyboard'
import { Play, RectangleHorizontal, RotateCcw, Volume, VolumeX } from '@/components/ui/icon'
import { ICON_SIZE } from '@/components/ui/iconSizes'
import { Tooltip } from '@/components/ui/tooltip'

// VideoPlayer — the app's own transport (Issue 386).
//
// A video product whose player is the browser default is telling the creator it
// did not build a player. The native control bar also exposes a kebab menu whose
// "Download" sits next to a paid action on three of our surfaces.
//
// `controls` is not a prop and never will be. If a call site needs less chrome it
// takes `density="compact"`; if it needs different chrome, that is a new density.
//
// PLAYHEAD OWNERSHIP. The element's `currentTime` is the truth and lives in a
// ref, not in state. `timeupdate` fires ~4Hz; putting that in a page's state
// re-reconciles the transcript, the timeline and the whole rail four times a
// second, and #390 wants a rAF-smooth playhead, which would make it 60. Consumers
// pick a channel deliberately:
//   - handle.getCurrentTime()  — pull, for event handlers. Zero renders. Default.
//   - handle.subscribeTime(cb) — push, for a component that must animate.
//   - onTimeChange             — coalesced at the timeupdate rate, small consumers.
// #386 deliberately stops here: pushing the subscription down into Timeline is
// #390's job and would collide with its prop rewrite.

const SEEK_STEP_S = 5
const SHUTTLE_RATES = [1, 1.5, 2, 4] as const
// The unmute choice outlives one mount (Issue 434): Review remounts the player
// per clip (`key={clip.id}`), and re-muting on every advance made the queue
// effectively silent. Session-scoped on purpose — a fresh tab starts muted
// again, which is what the autoplay policy expects.
const MUTED_STORAGE_KEY = 'cc-player-muted'
/** Standard rates a measured fps is snapped to, when it can be measured at all. */
const COMMON_FPS = [23.976, 24, 25, 29.97, 30, 50, 59.94, 60]

export interface VideoPlayerHandle {
  play(): Promise<void>
  pause(): void
  /** Absolute seconds, clamped to [0, duration]. */
  seek(t: number): void
  seekBy(delta: number): void
  /** Reads the element — never stale, never triggers a render. */
  getCurrentTime(): number
  getDuration(): number
  setRate(rate: number): void
  /** Returns an unsubscribe function. */
  subscribeTime(cb: () => void): () => void
  readonly element: HTMLVideoElement | null
}

export interface VideoPlayerProps {
  src: string
  /** Forces a fresh media element when the artifact behind an identical URL changes. */
  mediaKey?: string
  aspect?: 'portrait' | 'landscape'
  /**
   * `full` — transport, scrub bar, time, speed, fullscreen, below the frame.
   * `compact` — play/pause + elapsed only, for players too small for a full bar.
   */
  density?: 'full' | 'compact'
  /** Accessible name. REQUIRED, so no player can ship unnamed. */
  label: string
  autoPlay?: boolean
  poster?: string
  /** Binds the editing transport keys. At most ONE player per page may set this. */
  transport?: boolean
  /** Assumed frame rate for `,` / `.`; refined at runtime where the browser allows. */
  fps?: number
  onTimeChange?: (t: number) => void
  onPlay?: () => void
  onEnded?: () => void
  onError?: () => void
  onDurationChange?: (d: number) => void
  className?: string
  ref?: Ref<VideoPlayerHandle>
}

function formatTime(s: number): string {
  if (!Number.isFinite(s) || s < 0) return '0:00'
  const m = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return `${m}:${String(sec).padStart(2, '0')}`
}

export function VideoPlayer({
  src,
  mediaKey,
  aspect = 'portrait',
  density = 'full',
  label,
  autoPlay,
  poster,
  transport,
  fps = 30,
  onTimeChange,
  onPlay,
  onEnded,
  onError,
  onDurationChange,
  className,
  ref,
}: VideoPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const timeRef = useRef(0)
  const subscribersRef = useRef(new Set<() => void>())
  const reverseRef = useRef<number | null>(null)

  const [duration, setDuration] = useState(0)
  const [paused, setPaused] = useState(true)
  const [rate, setRateState] = useState(1)
  // Only the scrub bar and the readout need to re-render on time; both live here,
  // so the cost is this component rather than the consuming page.
  const [displayTime, setDisplayTime] = useState(0)
  // State, not a ref: the transport tooltip renders this number, and reading a
  // ref during render is unsafe. It settles at most once per source.
  const [effectiveFps, setEffectiveFps] = useState(fps)
  // Muted is STATE seeded from autoPlay (Issue 434) — the old static
  // `muted={autoPlay}` could never be undone, so autoplaying surfaces (Review)
  // were permanently silent. Autoplay still starts muted (browser policy,
  // Issue 359d) unless this session already chose sound.
  const [muted, setMuted] = useState(() => {
    if (!autoPlay) return false
    try {
      return sessionStorage.getItem(MUTED_STORAGE_KEY) !== 'false'
    } catch {
      return true
    }
  })

  const toggleMuted = useCallback(() => {
    setMuted((m) => {
      const next = !m
      // The element property, not just the React prop: the prop only applies on
      // mount for a playing element in some browsers.
      if (videoRef.current) videoRef.current.muted = next
      try {
        sessionStorage.setItem(MUTED_STORAGE_KEY, String(next))
      } catch {
        // Storage unavailable (private mode) — the toggle still works this mount.
      }
      return next
    })
  }, [])

  const emit = useCallback(() => {
    for (const cb of subscribersRef.current) cb()
  }, [])

  const stopReverse = useCallback(() => {
    if (reverseRef.current !== null) {
      cancelAnimationFrame(reverseRef.current)
      reverseRef.current = null
    }
  }, [])

  const seek = useCallback(
    (t: number) => {
      const el = videoRef.current
      if (!el) return
      // Only clamp the upper bound once a real duration is known. Before
      // metadata loads the element reports NaN, and a live/unknown-length stream
      // can report 0 — clamping against either would silently pin every seek to
      // the start, which is indistinguishable from "seeking is broken".
      const known = Number.isFinite(el.duration) && el.duration > 0
      const next = Math.max(0, known ? Math.min(t, el.duration) : t)
      el.currentTime = next
      timeRef.current = next
      setDisplayTime(next)
      emit()
    },
    [emit],
  )

  useImperativeHandle(
    ref,
    (): VideoPlayerHandle => ({
      play: async () => {
        await videoRef.current?.play()
      },
      pause: () => videoRef.current?.pause(),
      seek,
      seekBy: (delta) => seek(timeRef.current + delta),
      getCurrentTime: () => videoRef.current?.currentTime ?? timeRef.current,
      getDuration: () => videoRef.current?.duration ?? 0,
      setRate: (r) => {
        if (videoRef.current) videoRef.current.playbackRate = r
        setRateState(r)
      },
      subscribeTime: (cb) => {
        subscribersRef.current.add(cb)
        return () => subscribersRef.current.delete(cb)
      },
      get element() {
        return videoRef.current
      },
    }),
    [seek],
  )

  const togglePlay = useCallback(() => {
    const el = videoRef.current
    if (!el) return
    stopReverse()
    if (el.paused) void el.play().catch(() => {})
    else el.pause()
  }, [stopReverse])

  // Step one frame. There is no frame-accurate seek on the web and the render
  // pipeline pins no output rate (clip_engine/render.py has no -r), so the clip's
  // fps is genuinely unknown to us. We assume `fps`, refine it opportunistically
  // below, and SAY which number we used in the tooltip rather than implying
  // accuracy we do not have.
  const stepFrame = useCallback(
    (direction: 1 | -1) => {
      const el = videoRef.current
      if (!el) return
      stopReverse()
      el.pause()
      seek(timeRef.current + direction / effectiveFps)
    },
    [effectiveFps, seek, stopReverse],
  )

  const shuttle = useCallback(
    (direction: 1 | -1) => {
      const el = videoRef.current
      if (!el) return
      const idx = SHUTTLE_RATES.indexOf(rate as (typeof SHUTTLE_RATES)[number])
      const next = SHUTTLE_RATES[Math.min(idx + 1, SHUTTLE_RATES.length - 1)] ?? 1

      if (direction === 1) {
        stopReverse()
        el.playbackRate = next
        setRateState(next)
        void el.play().catch(() => {})
        return
      }

      // J — reverse shuttle. Browsers reject a negative playbackRate, so run the
      // playhead backwards on a rAF loop. Silent, which matches NLE behaviour at
      // shuttle speed anyway.
      el.pause()
      setRateState(next)
      stopReverse()
      let last = performance.now()
      const tick = (now: number) => {
        const dt = (now - last) / 1000
        last = now
        const nextTime = timeRef.current - next * dt
        if (nextTime <= 0) {
          seek(0)
          reverseRef.current = null
          return
        }
        seek(nextTime)
        reverseRef.current = requestAnimationFrame(tick)
      }
      reverseRef.current = requestAnimationFrame(tick)
    },
    [rate, seek, stopReverse],
  )

  const handleKey = useCallback(
    (e: KeyboardEvent | React.KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return false
      switch (e.key) {
        case ' ':
          togglePlay()
          return true
        case 'ArrowLeft':
          seek(timeRef.current - SEEK_STEP_S)
          return true
        case 'ArrowRight':
          seek(timeRef.current + SEEK_STEP_S)
          return true
        case ',':
          stepFrame(-1)
          return true
        case '.':
          stepFrame(1)
          return true
        case 'j':
        case 'J':
          shuttle(-1)
          return true
        case 'k':
        case 'K': {
          const el = videoRef.current
          stopReverse()
          el?.pause()
          if (el) el.playbackRate = 1
          setRateState(1)
          return true
        }
        case 'l':
        case 'L':
          shuttle(1)
          return true
        default:
          return false
      }
    },
    [seek, shuttle, stepFrame, stopReverse, togglePlay],
  )

  // Layer 2 of the keyboard model. Layer 1 (the root's own onKeyDown) is always
  // on and is safe by construction — it only fires when focus is inside the
  // player. This document listener exists so the editing keys work while the
  // creator's attention is on the timeline, and it bails on anything that could
  // belong to a text surface or a dialog. Consequence worth stating: space does
  // NOT play/pause while focus is in the transcript (role="textbox"), because
  // there the user is selecting words. That is the right trade, not an accident.
  useEffect(() => {
    if (!transport) return
    const onKeyDown = (e: KeyboardEvent) => {
      // Yield to anything that already claimed this key (Issue 390). Two things
      // depend on it: the timeline's shortcut bus runs in the CAPTURE phase and
      // marks what it handled, so its keys never double-fire here; and the
      // ScrubBar's own ±1s arrows call preventDefault, which previously did NOT
      // stop this listener — so an arrow key on a focused ScrubBar seeked TWICE,
      // ±1s from the bar and another ±5s from here.
      if (e.defaultPrevented) return
      if (isTypingTarget(e.target)) return
      if (isModalOpen()) return
      if (handleKey(e)) e.preventDefault()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [transport, handleKey])

  useEffect(() => stopReverse, [stopReverse])

  // Opportunistically measure the real frame rate. Absent in Firefox and jsdom,
  // so this is a refinement, never a requirement.
  useEffect(() => {
    const el = videoRef.current as (HTMLVideoElement & {
      requestVideoFrameCallback?: (cb: (now: number, meta: VideoFrameMeta) => void) => number
    }) | null
    if (!el?.requestVideoFrameCallback) return
    let first: VideoFrameMeta | null = null
    const sample = (_now: number, meta: VideoFrameMeta) => {
      if (!first) {
        first = meta
        el.requestVideoFrameCallback?.(sample)
        return
      }
      const dFrames = meta.presentedFrames - first.presentedFrames
      const dTime = meta.mediaTime - first.mediaTime
      if (dTime > 0.5 && dFrames > 0) {
        const measured = dFrames / dTime
        const nearest = COMMON_FPS.reduce((a, b) =>
          Math.abs(b - measured) < Math.abs(a - measured) ? b : a,
        )
        if (Math.abs(nearest - measured) / nearest < 0.02) setEffectiveFps(nearest)
        return
      }
      el.requestVideoFrameCallback?.(sample)
    }
    el.requestVideoFrameCallback(sample)
  }, [src])

  const compact = density === 'compact'

  return (
    <div
      role="group"
      aria-label={label}
      tabIndex={0}
      onKeyDown={(e) => {
        // Same bail as the document listener: yield to anything nearer the
        // target that already claimed this key — notably the ScrubBar's ±1s
        // arrows, which would otherwise be followed by another ±5s from here.
        if (e.defaultPrevented) return
        if (isTypingTarget(e.target)) return
        if (handleKey(e)) e.preventDefault()
      }}
      className={cn(
        'group relative flex flex-col overflow-hidden rounded-xl border border-default bg-black focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-border',
        className,
      )}
    >
      <video
        key={mediaKey ? `${mediaKey}:${src}` : src}
        ref={videoRef}
        src={src}
        poster={poster}
        playsInline
        // autoPlay starts muted: browsers block unmuted autoplay, and the
        // element then sits paused with no visible transport (Issue 359d).
        // The chrome's audio toggle (Issue 434) is the way back to sound.
        autoPlay={autoPlay}
        muted={muted}
        preload={autoPlay ? 'auto' : 'metadata'}
        onTimeUpdate={(e) => {
          const t = e.currentTarget.currentTime
          timeRef.current = t
          setDisplayTime(t)
          emit()
          onTimeChange?.(t)
        }}
        onDurationChange={(e) => {
          const d = e.currentTarget.duration
          setDuration(Number.isFinite(d) ? d : 0)
          onDurationChange?.(d)
        }}
        onPlay={() => {
          setPaused(false)
          onPlay?.()
        }}
        onPause={() => setPaused(true)}
        onEnded={() => {
          setPaused(true)
          onEnded?.()
        }}
        onError={onError}
        className={cn('w-full bg-black', aspect === 'portrait' ? 'aspect-[9/16]' : 'aspect-video')}
      />

      {/* Chrome is ALWAYS visible, never hover-revealed: hover-only fails
          keyboard and touch outright, and makes the visual-regression
          screenshots non-deterministic. */}
      <div
        className={cn(
          'flex items-center gap-2 border-t border-default bg-surface px-2',
          compact ? 'py-1' : 'py-1.5',
        )}
      >
        <button
          type="button"
          onClick={togglePlay}
          aria-label={paused ? 'Play' : 'Pause'}
          className="inline-flex size-7 shrink-0 items-center justify-center rounded-sm text-muted hover:bg-elevated hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-border"
        >
          {paused ? (
            <Play className={ICON_SIZE.sm} aria-hidden="true" />
          ) : (
            <PauseGlyph className={ICON_SIZE.sm} />
          )}
        </button>

        <ScrubBar
          currentTime={displayTime}
          duration={duration}
          onSeek={seek}
        />

        <span className="shrink-0 font-mono text-mono text-muted tabular-nums" aria-hidden="true">
          {formatTime(displayTime)} / {formatTime(duration)}
        </span>

        {/* In BOTH densities: Review's compact player is exactly where the
            missing sound was reported (Issue 434). */}
        <button
          type="button"
          onClick={toggleMuted}
          aria-label={muted ? 'Unmute' : 'Mute'}
          className="inline-flex size-7 shrink-0 items-center justify-center rounded-sm text-muted hover:bg-elevated hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-border"
        >
          {muted ? (
            <VolumeX className={ICON_SIZE.sm} aria-hidden="true" />
          ) : (
            <Volume className={ICON_SIZE.sm} aria-hidden="true" />
          )}
        </button>

        {!compact && (
          <>
            <Tooltip content={`Step back one frame (${Math.round(effectiveFps)} fps)`}>
              <button
                type="button"
                onClick={() => stepFrame(-1)}
                aria-label="Step back one frame"
                className="inline-flex size-7 shrink-0 items-center justify-center rounded-sm text-muted hover:bg-elevated hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-border"
              >
                <RotateCcw className={ICON_SIZE.xs} aria-hidden="true" />
              </button>
            </Tooltip>
            <span className="shrink-0 font-mono text-label text-subtle" aria-hidden="true">
              {rate}×
            </span>
            <button
              type="button"
              onClick={() => videoRef.current?.requestFullscreen?.()}
              aria-label="Full screen"
              className="inline-flex size-7 shrink-0 items-center justify-center rounded-sm text-muted hover:bg-elevated hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-border"
            >
              <RectangleHorizontal className={ICON_SIZE.xs} aria-hidden="true" />
            </button>
          </>
        )}
      </div>
    </div>
  )
}

interface VideoFrameMeta {
  mediaTime: number
  presentedFrames: number
}

// lucide has no "pause" in our allow-list and a pause bar is two rectangles —
// cheaper as a shape than as another icon export.
function PauseGlyph({ className }: { className?: string }) {
  return (
    <span aria-hidden="true" className={cn('inline-flex items-center gap-[3px]', className)}>
      <span className="h-3 w-[3px] rounded-xs bg-current" />
      <span className="h-3 w-[3px] rounded-xs bg-current" />
    </span>
  )
}

interface ScrubBarProps {
  currentTime: number
  duration: number
  onSeek: (t: number) => void
}

// Hand-rolled rather than the Radix Slider from #385: scrubbing needs pointer
// capture across the whole track, pause-on-drag, and (in #390) a buffered-ranges
// underlay — all of which fight a controlled-value model.
function ScrubBar({ currentTime, duration, onSeek }: ScrubBarProps) {
  const trackRef = useRef<HTMLDivElement>(null)
  const pct = duration > 0 ? Math.min(100, (currentTime / duration) * 100) : 0

  const seekFromPointer = (clientX: number) => {
    const rect = trackRef.current?.getBoundingClientRect()
    if (!rect || rect.width === 0 || duration <= 0) return
    onSeek(((clientX - rect.left) / rect.width) * duration)
  }

  return (
    <div
      ref={trackRef}
      role="slider"
      aria-label="Seek"
      aria-valuemin={0}
      aria-valuemax={Math.max(0, Math.round(duration))}
      aria-valuenow={Math.round(currentTime)}
      aria-valuetext={`${formatTime(currentTime)} of ${formatTime(duration)}`}
      tabIndex={0}
      // preventDefault is the ONLY signal needed to stop the outer layers
      // double-seeking; both of them bail on `defaultPrevented`. This used to be
      // paired with an onKeyDownCapture that called stopPropagation, which — in
      // the capture phase — also prevented THIS handler from ever running, so
      // the scrub bar's arrow keys did nothing at all (Issue 390).
      onKeyDown={(e) => {
        if (e.key === 'ArrowLeft') onSeek(currentTime - 1)
        else if (e.key === 'ArrowRight') onSeek(currentTime + 1)
        else return
        e.preventDefault()
      }}
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture?.(e.pointerId)
        seekFromPointer(e.clientX)
      }}
      onPointerMove={(e) => {
        if (e.currentTarget.hasPointerCapture?.(e.pointerId)) seekFromPointer(e.clientX)
      }}
      className="relative h-1.5 min-w-0 flex-1 cursor-pointer rounded-full bg-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-border"
    >
      <div
        className="pointer-events-none absolute inset-y-0 left-0 rounded-full bg-accent"
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}
