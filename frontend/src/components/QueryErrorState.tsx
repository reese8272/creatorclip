import { cn } from '@/lib/utils'

// Shared retry card for failed page-level queries (Issue 361 sweep). A failed
// load must NOT fall through to a first-run empty state — a creator whose data
// exists would be told they have none. Extracted from the Recap.tsx retry idiom
// once all four core pages (Dashboard, Review, VideoClipsMap, Editor) carried
// the branch (docs/DECISIONS.md 2026-07-20). Page-specific copy comes in via
// props; the retry affordance and card chrome stay uniform.
export function QueryErrorState({
  title,
  detail = 'This is usually temporary — try again in a moment.',
  onRetry,
  className,
}: {
  title: string
  detail?: string
  onRetry: () => void
  className?: string
}) {
  return (
    <div
      className={cn(
        'rounded-md border border-default bg-surface px-6 py-10 text-center',
        className,
      )}
    >
      <p className="text-sm text-fg">{title}</p>
      <p className="mt-1 text-xs text-subtle">{detail}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-4 rounded-md border border-default px-3 py-1.5 text-xs text-fg hover:bg-elevated"
      >
        Retry
      </button>
    </div>
  )
}
