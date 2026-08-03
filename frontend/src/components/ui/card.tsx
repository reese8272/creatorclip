import type { HTMLAttributes, ReactNode } from 'react'
import { cn } from '@/lib/utils'

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  /**
   * Which rung of the elevation ladder this card sits on (docs/UI.md "Elevation").
   * - `panel` (default) — L1, the ordinary card. SECONDARY content.
   * - `primary` — L2, the ONE dominant panel on a screen.
   *
   * Luminance alone does not carry dominance: L1→L2 is a 3.5-point step. The
   * `primary` treatment therefore moves four cues together — surface up, border
   * to `strong`, more padding at a larger radius — which is what actually reads
   * as "this is the thing" (Issue 400).
   */
  level?: 'panel' | 'primary'
  /** Adds a hover lift (one rung up + deeper shadow). Use for clickable cards. */
  interactive?: boolean
}

// Base surface for every card/panel. Elevation comes from the design system's
// shadow ladder + the inset top-edge highlight that restores the 3D cue flat
// dark loses (docs/UI.md "Shadows"). Flat bordered boxes are what made the UI
// read as a wireframe; the shadow + radius-md (8px) are the de-blocking.
export function Card({ className, level = 'panel', interactive, ...props }: CardProps) {
  return (
    <div
      className={cn(
        'border shadow-sm inset-shadow-highlight',
        level === 'primary'
          ? 'rounded-lg border-strong bg-elevated'
          : 'rounded-md border-default bg-surface',
        // Hover lifts exactly ONE rung. This used to jump L1 straight to
        // `bg-raised` (L3), which is the overlay level and skipped a step.
        interactive &&
          'transition-[background-color,border-color,box-shadow,transform] duration-base ease-standard hover:-translate-y-0.5 hover:border-strong hover:shadow-md',
        interactive && (level === 'primary' ? 'hover:bg-raised' : 'hover:bg-elevated'),
        className,
      )}
      {...props}
    />
  )
}

interface CardHeaderProps {
  title: ReactNode
  description?: ReactNode
  /** Right-aligned content — e.g. a provenance badge. */
  aside?: ReactNode
  className?: string
}

export function CardHeader({ title, description, aside, className }: CardHeaderProps) {
  return (
    <div className={cn('flex items-start justify-between gap-4 px-5 pt-5', className)}>
      <div className="min-w-0">
        <h2 className="text-h3 font-ui text-fg">{title}</h2>
        {description && <p className="mt-1 text-small text-muted">{description}</p>}
      </div>
      {aside && <div className="shrink-0">{aside}</div>}
    </div>
  )
}

export function CardBody({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('p-5', className)} {...props} />
}
