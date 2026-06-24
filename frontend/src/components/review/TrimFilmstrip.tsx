import { useRef, type PointerEvent as ReactPointerEvent } from 'react'
import { clampTrim } from '@/components/review/trim'

// mm:ss for the ruler ends + readout.
function fmt(s: number): string {
  const m = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return `${m}:${sec.toString().padStart(2, '0')}`
}

// Eight gradient "frames" approximating a filmstrip thumbnail track. Decorative
// only — the real signal is the trim region + handles drawn over them.
const FRAME_GRADIENTS = [
  'linear-gradient(180deg, oklch(24% 0.022 285), oklch(13% 0.014 285))',
  'linear-gradient(180deg, oklch(20% 0.02 285), oklch(12% 0.013 285))',
  'linear-gradient(180deg, oklch(26% 0.024 285), oklch(14% 0.015 285))',
  'linear-gradient(180deg, oklch(21% 0.02 285), oklch(12% 0.013 285))',
  'linear-gradient(180deg, oklch(25% 0.023 285), oklch(13% 0.014 285))',
  'linear-gradient(180deg, oklch(19% 0.018 285), oklch(11% 0.012 285))',
  'linear-gradient(180deg, oklch(27% 0.025 285), oklch(14% 0.015 285))',
  'linear-gradient(180deg, oklch(22% 0.021 285), oklch(12% 0.013 285))',
]

interface TrimFilmstripProps {
  duration: number
  trimStart: number
  trimEnd: number
  currentTime?: number
  onChange: (start: number, end: number) => void
}

// Issue 306: replaces the two stacked trim sliders with a draggable dual-handle
// filmstrip — excluded ends dimmed, accent-outlined selection, playhead, tick
// ruler, and a live "Xs selected" readout. The handles drive the same
// trimStart/trimEnd state the feedback "Save trim" action submits.
export function TrimFilmstrip({
  duration,
  trimStart,
  trimEnd,
  currentTime = 0,
  onChange,
}: TrimFilmstripProps) {
  const trackRef = useRef<HTMLDivElement>(null)
  const dur = duration > 0 ? duration : 1

  const pct = (t: number) => `${Math.max(0, Math.min(100, (t / dur) * 100))}%`
  const startPct = pct(trimStart)
  const endPct = pct(trimEnd)
  const selected = Math.max(0, trimEnd - trimStart)

  function xToTime(clientX: number): number {
    const rect = trackRef.current?.getBoundingClientRect()
    if (!rect || rect.width === 0) return 0
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width))
    return ratio * dur
  }

  function startDrag(e: ReactPointerEvent<HTMLDivElement>) {
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
  }

  function moveDrag(handle: 'start' | 'end') {
    return (e: ReactPointerEvent<HTMLDivElement>) => {
      if (!e.currentTarget.hasPointerCapture(e.pointerId)) return
      const next = clampTrim(handle, xToTime(e.clientX), trimStart, trimEnd, dur)
      onChange(next.start, next.end)
    }
  }

  function endDrag(e: ReactPointerEvent<HTMLDivElement>) {
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId)
    }
  }

  const handle = (kind: 'start' | 'end', left: string, label: string) => (
    <div
      role="slider"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={Math.round(dur)}
      aria-valuenow={Math.round(kind === 'start' ? trimStart : trimEnd)}
      tabIndex={0}
      onPointerDown={startDrag}
      onPointerMove={moveDrag(kind)}
      onPointerUp={endDrag}
      className="absolute -top-1 -bottom-1 z-10 flex w-5 -translate-x-1/2 cursor-ew-resize touch-none items-center justify-center"
      style={{ left }}
    >
      <div className="flex h-full w-[9px] items-center justify-center rounded-[5px] bg-accent shadow-sm shadow-inset">
        <span className="h-[9px] w-0.5 rounded-full bg-on-accent opacity-80" />
      </div>
    </div>
  )

  return (
    <div className="w-full max-w-[340px]">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-label uppercase tracking-[0.06em] text-muted">Trim clip</span>
        <span className="font-mono text-label text-accent-text">{selected.toFixed(1)}s selected</span>
      </div>

      {/* tick ruler */}
      <div
        className="mb-[5px] h-[7px] rounded-[3px]"
        style={{
          backgroundImage:
            'repeating-linear-gradient(90deg, var(--color-strong) 0 1px, transparent 1px 10%)',
        }}
      />

      <div
        ref={trackRef}
        className="relative h-[60px] touch-none select-none rounded-md border border-strong bg-surface"
      >
        {/* filmstrip frames */}
        <div className="absolute inset-0 flex overflow-hidden rounded-[7px]">
          {FRAME_GRADIENTS.map((g, i) => (
            <div
              key={i}
              className="flex-1 border-r border-black/35 last:border-r-0"
              style={{ background: g }}
            />
          ))}
        </div>
        {/* dimmed excluded ends */}
        <div
          className="absolute bottom-0 left-0 top-0 rounded-l-[7px] bg-bg/75"
          style={{ width: startPct }}
        />
        <div className="absolute bottom-0 right-0 top-0 rounded-r-[7px] bg-bg/75" style={{ left: endPct }} />
        {/* selected region outline */}
        <div
          className="pointer-events-none absolute bottom-0 top-0 border-y-2 border-accent"
          style={{ left: startPct, width: `${Math.max(0, ((trimEnd - trimStart) / dur) * 100)}%` }}
        />
        {/* playhead */}
        <div
          className="pointer-events-none absolute -top-[3px] -bottom-[3px] w-0.5 -translate-x-1/2 bg-fg"
          style={{ left: pct(currentTime) }}
        >
          <div className="absolute -top-1 left-1/2 h-2 w-2 -translate-x-1/2 rounded-full bg-fg" />
        </div>
        {handle('start', startPct, 'Trim start')}
        {handle('end', endPct, 'Trim end')}
      </div>

      <div className="mt-1.5 flex items-center justify-between font-mono text-label text-subtle">
        <span>0:00</span>
        <span className="text-accent-text">
          {fmt(trimStart)} – {fmt(trimEnd)}
        </span>
        <span>{fmt(dur)}</span>
      </div>
    </div>
  )
}
