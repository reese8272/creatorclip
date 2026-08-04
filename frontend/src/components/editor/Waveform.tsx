import { useEffect, useRef, useState } from 'react'
import { peakEnvelope, type WaveformPeaks } from '@/lib/peaks'
import { cn } from '@/lib/utils'

// Bar width + gap in CSS px. Bars rather than a filled path because the timelines
// overlay cut/candidate regions on top, and a bar field stays readable underneath
// a translucent overlay where a solid waveform turns to mud.
const BAR_W = 2
const BAR_GAP = 1

/**
 * Waveform — draws REAL audio peaks, or an honest flat track (Issue 392).
 *
 * There is deliberately no third path. Before this component, the long-form
 * "Source timeline" drew 48 bars at `20 + ((i * 37) % 60)%` — decoration that a
 * creator reads as loud moments and navigates by, on the surface whose entire
 * job is helping them find moments. When `peaks` is null this renders a flat
 * line and the caller labels it "Waveform unavailable"; it never invents
 * amplitude to fill the space.
 *
 * Canvas, not DOM nodes: a 22-minute source across ~1100px is ~370 bars, and at
 * Issue 390's zoom levels far more.
 */
export function Waveform({
  peaks,
  startS,
  endS,
  className,
  ariaLabel,
}: {
  peaks: WaveformPeaks | null
  /** Window into the source, in seconds. The clip timeline passes its own span. */
  startS: number
  endS: number
  className?: string
  ariaLabel?: string
}) {
  const hostRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [size, setSize] = useState({ w: 0, h: 0 })

  // Measure with getBoundingClientRect in a layout effect and use ResizeObserver
  // only as the change notifier, so the first paint is correct even before any
  // observer has fired.
  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    const measure = () => {
      const r = host.getBoundingClientRect()
      setSize((prev) =>
        Math.round(prev.w) === Math.round(r.width) && Math.round(prev.h) === Math.round(r.height)
          ? prev
          : { w: r.width, h: r.height },
      )
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(host)
    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || size.w <= 0 || size.h <= 0) return
    const dpr = window.devicePixelRatio || 1
    canvas.width = Math.floor(size.w * dpr)
    canvas.height = Math.floor(size.h * dpr)
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, size.w, size.h)

    const mid = size.h / 2
    const stride = BAR_W + BAR_GAP
    const buckets = Math.max(1, Math.floor(size.w / stride))
    const env = peakEnvelope(peaks, { startS, endS, buckets })

    // `currentColor` keeps the waveform on the design system: callers set the
    // colour with a text-* token instead of this file hardcoding one.
    ctx.fillStyle = getComputedStyle(canvas).color

    if (!env) {
      // The honest fallback: a flat line at the centre. Visibly "no data", not a
      // quiet waveform.
      ctx.globalAlpha = 0.35
      ctx.fillRect(0, mid - 0.5, size.w, 1)
      return
    }

    ctx.globalAlpha = 1
    for (let b = 0; b < buckets; b++) {
      // Floor of 1px so silence still reads as a track rather than a gap.
      const h = Math.max(1, env[b] * (size.h - 2))
      ctx.fillRect(b * stride, mid - h / 2, BAR_W, h)
    }
  }, [peaks, startS, endS, size])

  return (
    <div ref={hostRef} className={cn('relative h-full w-full', className)}>
      <canvas
        ref={canvasRef}
        aria-label={ariaLabel}
        role={ariaLabel ? 'img' : undefined}
        aria-hidden={ariaLabel ? undefined : true}
        className="h-full w-full"
        style={{ width: '100%', height: '100%' }}
      />
    </div>
  )
}
