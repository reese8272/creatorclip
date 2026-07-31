import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Chip } from '@/components/Chip'
import type { ChipPose } from '@/components/chip/poses'
import { Button, buttonVariants } from '@/components/ui/button'
import { cn } from '@/lib/utils'

// One action form, and exactly one, is REQUIRED (Issue 355 finding 4). The
// first-run walkthrough found ~9 empty states that named a prerequisite in prose
// — "generate them from the Dashboard", "connect your channel and sync" — and
// gave the creator nothing to click, sometimes for a control that does not exist
// anywhere in the app. Making the action a required union is what stops that
// recurring: a dead-end empty state no longer type-checks.
//
//   actionTo   — router destination (rendered as a link wearing the button skin)
//   onAction   — in-page handler (opens a form, fires a mutation)
//   action     — caller-supplied control, for anything owning its own mutation
//                state (GenerateClipsButton carries a spend confirmation + error)
type EmptyStateAction =
  | { actionLabel: string; actionTo: string; onAction?: never; action?: never }
  | { actionLabel: string; onAction: () => void; actionTo?: never; action?: never }
  | { action: ReactNode; actionLabel?: never; actionTo?: never; onAction?: never }

export type EmptyStatePromptProps = EmptyStateAction & {
  /** What is empty, stated as fact — not an apology. */
  title: string
  /** Why it is empty, or what will fill it. */
  detail?: ReactNode
  /** Lower-weight escape hatch, rendered as a plain text link. */
  secondaryLabel?: string
  secondaryTo?: string
  /** Mascot pose. Omit inside a Panel — the panel already has a visible title. */
  pose?: ChipPose
  /** 'inline' (default) is bare, for use inside an existing Panel or card;
   *  'card' adds page-level chrome for a standalone empty region. */
  variant?: 'card' | 'inline'
  className?: string
}

export function EmptyStatePrompt({
  title,
  detail,
  secondaryLabel,
  secondaryTo,
  pose,
  variant = 'inline',
  className,
  ...action
}: EmptyStatePromptProps) {
  return (
    <div
      className={cn(
        variant === 'card' && 'rounded-md border border-default bg-surface px-6 py-10 text-center',
        className,
      )}
    >
      {pose && (
        <Chip pose={pose} size={variant === 'card' ? 64 : 40} className={cn(variant === 'card' && 'mx-auto')} />
      )}
      <p className={cn('text-sm text-fg', pose && 'mt-2')}>{title}</p>
      {detail && <p className="mt-1 text-sm text-muted">{detail}</p>}
      <div
        className={cn(
          'mt-4 flex flex-wrap items-center gap-3',
          variant === 'card' && 'justify-center',
        )}
      >
        <EmptyStateActionControl {...action} />
        {secondaryLabel && secondaryTo && (
          <Link to={secondaryTo} className="text-sm text-muted underline hover:text-fg">
            {secondaryLabel}
          </Link>
        )}
      </div>
    </div>
  )
}

function EmptyStateActionControl(action: EmptyStateAction) {
  if (action.action !== undefined) return <>{action.action}</>

  if (action.actionTo !== undefined) {
    return (
      <Link to={action.actionTo} className={buttonVariants({ variant: 'secondary', size: 'sm' })}>
        {action.actionLabel}
      </Link>
    )
  }

  return (
    <Button variant="secondary" size="sm" onClick={action.onAction}>
      {action.actionLabel}
    </Button>
  )
}
