import { Button } from '@/components/ui/button'
import { Check, RefreshCw, TriangleAlert } from '@/components/ui/icon'
import { ICON_INLINE } from '@/components/ui/iconSizes'
import { cn } from '@/lib/utils'
import type { ConflictInfo, SaveState } from '@/hooks/useEditDocument'

// The autosave indicator (Issue 391).
//
// PERSISTENT, NOT A TOAST. The acceptance criterion is "save failures surfaced,
// not swallowed", and a toast is the one presentation that cannot satisfy it: it
// vanishes on a timer, so a creator who looked away learns nothing and keeps
// editing against a document that is not being saved. This element stays until
// the state that produced it changes.
//
// `aria-live="polite"` rather than `assertive`: a save transition should not
// interrupt a screen-reader user mid-sentence while they are editing.

export function SaveStatus({
  state,
  error,
  conflict,
  onRetry,
  onKeepMine,
  onTakeTheirs,
  className,
}: {
  state: SaveState
  error: string | null
  conflict: ConflictInfo | null
  onRetry: () => void
  onKeepMine: () => void
  onTakeTheirs: () => void
  className?: string
}) {
  if (state === 'conflict' && conflict) {
    return (
      <div
        role="alert"
        className={cn(
          'flex flex-col gap-2 rounded-md border border-warning bg-surface px-3 py-2 text-small',
          className,
        )}
      >
        <p className="flex items-start gap-2 text-default">
          <TriangleAlert aria-hidden size={ICON_INLINE} className="mt-0.5 flex-shrink-0 text-warning" />
          <span>
            {conflict.kind === 'resumed'
              ? 'You have unsaved edits from your last session on this device. Choose which version to keep — nothing is merged automatically.'
              : 'This clip was edited somewhere else. Choose which version to keep — nothing is merged automatically.'}
          </span>
        </p>
        <div className="flex gap-2">
          <Button size="sm" onClick={onKeepMine}>
            Keep my edits
          </Button>
          <Button variant="secondary" size="sm" onClick={onTakeTheirs}>
            Use the other version
          </Button>
        </div>
      </div>
    )
  }

  if (state === 'error') {
    return (
      <div
        role="alert"
        className={cn(
          'flex items-center gap-2 rounded-md border border-danger bg-surface px-3 py-2 text-small text-default',
          className,
        )}
      >
        <TriangleAlert aria-hidden size={ICON_INLINE} className="flex-shrink-0 text-danger" />
        <span className="flex-1">{error ?? 'Could not save your edit.'}</span>
        <Button variant="secondary" size="sm" onClick={onRetry}>
          Retry
        </Button>
      </div>
    )
  }

  return (
    <p
      aria-live="polite"
      className={cn('flex items-center gap-1.5 text-caption text-subtle', className)}
    >
      {state === 'saving' && (
        <>
          <RefreshCw aria-hidden size={ICON_INLINE} className="flex-shrink-0 animate-spin" />
          Saving…
        </>
      )}
      {state === 'saved' && (
        <>
          <Check aria-hidden size={ICON_INLINE} className="flex-shrink-0 text-success" />
          Saved
        </>
      )}
      {state === 'dirty' && 'Unsaved changes'}
      {/* 'idle' renders an empty live region on purpose: the element must exist
          before the first announcement or a screen reader will not read it. */}
    </p>
  )
}
