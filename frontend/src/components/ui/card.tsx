import type { HTMLAttributes, ReactNode } from 'react'
import { cn } from '@/lib/utils'

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  /** Adds a hover lift (raised surface + deeper shadow). Use for clickable cards. */
  interactive?: boolean
}

// Base surface for every card/panel. Elevation comes from the design system's
// shadow ladder + the inset top-edge highlight that restores the 3D cue flat
// dark loses (docs/UI.md "Shadows"). Flat bordered boxes are what made the UI
// read as a wireframe; the shadow + radius-md (8px) are the de-blocking.
export function Card({ className, interactive, ...props }: CardProps) {
  return (
    <div
      className={cn(
        'rounded-md border border-default bg-surface shadow-sm shadow-inset',
        interactive &&
          'transition-[background-color,border-color,box-shadow,transform] duration-base ease-standard hover:-translate-y-0.5 hover:border-strong hover:bg-raised hover:shadow-md',
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
