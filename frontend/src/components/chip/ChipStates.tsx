import type { CSSProperties } from 'react'
import { Chip } from '@/components/Chip'
import type { ChipPose } from '@/components/chip/poses'
import { cn } from '@/lib/utils'

// Chip loading / thinking states — Issue 304 (design handoff "Chip Animations").
// Pure presentational. Each is the animated treatment itself (no demo-card chrome)
// so it drops straight into the matching surface. The global prefers-reduced-motion
// rule in index.css (`*` selector) collapses every animation below to a single
// resting frame, so no per-component motion guard is needed.
//
// Keyframes (index.css): chip-bob / chip-spin / chip-scan / chip-blink /
// chip-dot / chip-cardcycle / chip-floatup.

const anim = (value: string): CSSProperties => ({ animation: value })

// 1 — Analyzing: Chip bobs beside a corner-framed frame with a scan sweep.
//     Home: clip scoring / useStageStream "scoring".
//     Issue 314 — DEFERRED (kept exported + unit-tested): the scoring step the
//     pipeline emits is the `signals` stage, which already surfaces an animated
//     chip (ChipGeneratingClips, mounted in StageStepper). There is no separate
//     scoring surface to co-locate this in without stacking two animated chips in
//     the same tight VideoTable cell (layout thrash). Mount when a dedicated
//     scoring surface exists. Do not delete the export.
export function ChipAnalyzing({
  pose = 'magnify',
  size = 84,
  className,
}: {
  pose?: ChipPose
  size?: number
  className?: string
}) {
  const corner = 'absolute h-3 w-3 border-accent-text'
  return (
    <div className={cn('flex items-center gap-[18px]', className)}>
      <Chip pose={pose} size={size} style={anim('chip-bob 2.4s ease-in-out infinite')} />
      <div
        className="relative h-[116px] w-[66px] overflow-hidden rounded-md border border-strong"
        style={{ background: 'linear-gradient(160deg, oklch(18% 0.02 285), oklch(10% 0.01 285))' }}
      >
        <div
          className="absolute left-[8%] right-[8%] h-0.5 bg-accent-text"
          style={{
            boxShadow: '0 0 10px 2px var(--color-accent)',
            ...anim('chip-scan 1.8s ease-in-out infinite'),
          }}
        />
        <span className={cn(corner, 'left-1.5 top-1.5 border-l-2 border-t-2')} />
        <span className={cn(corner, 'right-1.5 top-1.5 border-r-2 border-t-2')} />
        <span className={cn(corner, 'bottom-1.5 left-1.5 border-b-2 border-l-2')} />
        <span className={cn(corner, 'bottom-1.5 right-1.5 border-b-2 border-r-2')} />
      </div>
    </div>
  )
}

// 2 — Thinking: Chip bobs next to a typing-dot bubble.
//     Home: before the first assistant token / brief generation.
export function ChipThinking({
  pose = 'think',
  size = 98,
  className,
}: {
  pose?: ChipPose
  size?: number
  className?: string
}) {
  const dot = 'h-[7px] w-[7px] rounded-full bg-accent-text'
  return (
    <div className={cn('flex items-end gap-1', className)}>
      <Chip pose={pose} size={size} style={anim('chip-bob 2.6s ease-in-out infinite')} />
      <div className="mb-[54px] flex gap-[5px] rounded-[14px_14px_14px_4px] border border-default bg-elevated px-3 py-2.5">
        <span className={dot} style={anim('chip-dot 1.2s infinite')} />
        <span className={dot} style={anim('chip-dot 1.2s infinite .15s')} />
        <span className={dot} style={anim('chip-dot 1.2s infinite .3s')} />
      </div>
    </div>
  )
}

// 3 — Streaming: Chip + a bubble holding the (token-by-token) text and a blink caret.
//     Home: Chat.tsx while streaming (replaces the bare ▍ caret).
export function ChipStreaming({
  text,
  pose = 'think',
  className,
}: {
  text: string
  pose?: ChipPose
  className?: string
}) {
  return (
    <div className={cn('flex w-full items-start gap-2.5', className)}>
      <Chip pose={pose} size={46} className="flex-shrink-0" />
      <div className="min-h-[120px] flex-1 rounded-[14px_14px_14px_4px] border border-default bg-elevated px-3 py-2.5 text-body leading-relaxed text-fg">
        {text}
        <span
          className="ml-px inline-block h-3.5 w-[7px] translate-y-0.5 bg-accent-text"
          style={anim('chip-blink 1s steps(1) infinite')}
        />
      </div>
    </div>
  )
}

// 4 — Looking it up: Chip bobs inside an orbiting "sources" ring.
//     Home: improvement-brief / live web-research step.
export function ChipLookingItUp({
  pose = 'magnify',
  size = 80,
  className,
}: {
  pose?: ChipPose
  size?: number
  className?: string
}) {
  return (
    <div className={cn('relative flex h-[150px] w-[150px] items-center justify-center', className)}>
      <div
        className="absolute inset-2.5 rounded-full border-[1.5px] border-dashed border-accent-border"
        style={anim('chip-spin 7s linear infinite')}
      >
        <span
          className="absolute left-1/2 top-[-6px] h-[11px] w-[11px] -translate-x-1/2 rounded-full bg-accent-text"
          style={{ boxShadow: '0 0 8px var(--color-accent)' }}
        />
        <span className="absolute bottom-1 left-1 h-2 w-2 rounded-full bg-accent" />
        <span
          className="absolute bottom-1 right-1 h-2 w-2 rounded-full"
          style={{ background: 'oklch(72% 0.16 145)' }}
        />
      </div>
      <Chip pose={pose} size={size} style={anim('chip-bob 2.8s ease-in-out infinite')} />
    </div>
  )
}

// 5 — Loading screen: Chip inside a spinner with a mono label.
//     Home: route-level Suspense / first load. `fullScreen` centers in the viewport.
export function ChipLoadingScreen({
  pose = 'wave',
  label = 'Warming up…',
  fullScreen = false,
  className,
}: {
  pose?: ChipPose
  label?: string
  fullScreen?: boolean
  className?: string
}) {
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        'flex flex-col items-center justify-center gap-3',
        fullScreen && 'min-h-[60vh] w-full',
        className,
      )}
    >
      <div className="relative flex h-[106px] w-[106px] items-center justify-center">
        <div
          className="absolute inset-0 rounded-full border-2 border-transparent"
          style={{
            borderTopColor: 'var(--color-accent)',
            borderRightColor: 'var(--color-accent-border)',
            ...anim('chip-spin 1.1s linear infinite'),
          }}
        />
        <Chip pose={pose} size={72} style={anim('chip-bob 2.4s ease-in-out infinite')} />
      </div>
      <span className="font-mono text-small text-muted">{label}</span>
    </div>
  )
}

// 6 — Rendering: Chip bobs over a determinate progress bar (0–100).
//     Home: caption/clean-pass "render queued" polling.
//     Issue 314 — DEFERRED (kept exported + unit-tested): this state is
//     determinate-only — it requires a real numeric `progress` (0–100). The
//     pipeline deliberately exposes NO numeric progress (useStageStream emits only
//     a coarse stage/label, and StageStepper never shows a countdown). Mounting it
//     now would mean fabricating a percentage, which the honesty scaffold forbids.
//     Mount when a real render-progress signal exists; until then leave unmounted.
export function ChipRendering({
  progress,
  label = 'Rendering styled cut',
  pose = 'laptop',
  size = 84,
  className,
}: {
  progress: number
  label?: string
  pose?: ChipPose
  size?: number
  className?: string
}) {
  const pct = Math.max(0, Math.min(100, Math.round(progress)))
  return (
    <div className={cn('flex w-full flex-col items-center gap-4', className)}>
      <Chip pose={pose} size={size} style={anim('chip-bob 2.5s ease-in-out infinite')} />
      <div className="w-full">
        <div className="mb-[5px] flex justify-between font-mono text-label text-muted">
          <span>{label}</span>
          <span>{pct}%</span>
        </div>
        <div
          className="h-[7px] overflow-hidden rounded-full bg-elevated"
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div
            className="h-full rounded-full"
            style={{
              width: `${pct}%`,
              background: 'linear-gradient(90deg, var(--color-accent), var(--color-accent-text))',
            }}
          />
        </div>
      </div>
    </div>
  )
}

// 7 — Generating clips: Chip beside three cards cycling in/out.
//     Home: clip generation in the VideoTable.
export function ChipGeneratingClips({
  pose = 'idea',
  size = 82,
  className,
}: {
  pose?: ChipPose
  size?: number
  className?: string
}) {
  const card =
    'h-[74px] w-[42px] rounded-sm border border-accent-border'
  const cardStyle: CSSProperties = {
    background: 'linear-gradient(160deg, oklch(22% 0.06 280), oklch(12% 0.01 285))',
  }
  return (
    <div className={cn('flex items-center gap-4', className)}>
      <Chip pose={pose} size={size} />
      <div className="flex gap-2">
        <div className={card} style={{ ...cardStyle, ...anim('chip-cardcycle 3s ease-in-out infinite') }} />
        <div className={card} style={{ ...cardStyle, ...anim('chip-cardcycle 3s ease-in-out infinite .4s') }} />
        <div className={card} style={{ ...cardStyle, ...anim('chip-cardcycle 3s ease-in-out infinite .8s') }} />
      </div>
    </div>
  )
}

// 8 — Personalizing: meditating Chip with floating binary digits.
//     Home: the personalization band while keep/drop ratings are learned.
export function ChipPersonalizing({
  pose = 'meditate',
  size = 98,
  className,
}: {
  pose?: ChipPose
  size?: number
  className?: string
}) {
  return (
    <div className={cn('relative flex h-[150px] w-[200px] items-center justify-center', className)}>
      <Chip pose={pose} size={size} style={anim('chip-bob 3s ease-in-out infinite')} />
      <span
        className="absolute bottom-6 left-[30%] font-mono text-label text-accent-text"
        style={anim('chip-floatup 3s ease-in infinite')}
      >
        1
      </span>
      <span
        className="absolute bottom-[18px] left-[46%] font-mono text-small text-accent"
        style={anim('chip-floatup 3.4s ease-in infinite .6s')}
      >
        0
      </span>
      <span
        className="absolute bottom-[26px] left-[63%] font-mono text-label text-accent-text"
        style={anim('chip-floatup 2.8s ease-in infinite 1.1s')}
      >
        1
      </span>
      <span
        className="absolute bottom-5 left-[38%] font-mono text-small"
        style={{ color: 'oklch(72% 0.16 145)', ...anim('chip-floatup 3.2s ease-in infinite 1.7s') }}
      >
        0
      </span>
    </div>
  )
}
