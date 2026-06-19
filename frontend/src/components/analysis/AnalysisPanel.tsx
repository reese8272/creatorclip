import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import type { TaskStatus } from '@/hooks/useTaskStream'

// Status chip shared by every analysis feature — maps stream state to a colored
// mono label. While streaming it shows the latest step (underscores → spaces).
export function StatusChip({
  status,
  step,
  error,
}: {
  status: TaskStatus
  step?: string | null
  error?: string | null
}) {
  if (error || status === 'error')
    return <span className="font-mono text-xs text-danger">error</span>
  if (status === 'done') return <span className="font-mono text-xs text-success">done</span>
  if (status === 'streaming')
    return (
      <span className="font-mono text-xs text-accent-text">
        {step ? step.replace(/_/g, ' ') : 'generating…'}
      </span>
    )
  return null
}

// Consistent panel chrome for the per-video analysis features.
export function AnalysisPanel({
  title,
  chip,
  children,
}: {
  title: string
  chip?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="mb-6 rounded-md border border-default bg-surface p-5 shadow-sm shadow-inset">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-sm font-medium uppercase tracking-[0.06em] text-muted">{title}</h2>
        {chip}
      </div>
      {children}
    </section>
  )
}

// Small "copy to clipboard" button with a transient confirmation.
export function CopyButton({ text, label = 'copy' }: { text: string; label?: string }) {
  return (
    <button
      type="button"
      onClick={(e) => {
        const btn = e.currentTarget
        navigator.clipboard.writeText(text).then(() => {
          const orig = btn.textContent
          btn.textContent = 'copied!'
          setTimeout(() => {
            btn.textContent = orig
          }, 1500)
        })
      }}
      className={cn('font-mono text-xs text-subtle hover:text-fg')}
    >
      {label}
    </button>
  )
}
