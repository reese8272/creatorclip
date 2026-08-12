import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api'
import { EmptyStatePrompt } from '@/components/EmptyStatePrompt'
import { cn } from '@/lib/utils'
import { PILE_LABEL, PILE_ORDER, type Pile } from '@/lib/clipPiles'
import type { ReviewClip } from '@/types'

// Issue 445 — the three piles. Keep/Drop write `triage` (Issue 444) and until
// now nothing read it: the queue was "top SHORTLIST_SIZE by rank", so a creator
// who rated three clips of twelve was told "All clips reviewed!" while the
// dashboard simultaneously showed a count that could never decrease.
//
// Needs-review stays the focused one-clip-at-a-time flow it already is. Kept
// and dropped are LISTS, because the question there is "which of these am I
// publishing?" — that needs titles and captions side by side, not a carousel.

export function PileTabs({
  counts,
  active,
  onSelect,
}: {
  counts: Record<Pile, number>
  active: Pile
  onSelect: (pile: Pile) => void
}) {
  return (
    <div className="flex shrink-0 items-center gap-1" role="tablist" aria-label="Clip piles">
      {PILE_ORDER.map((pile) => (
        <button
          key={pile}
          role="tab"
          aria-selected={active === pile}
          onClick={() => onSelect(pile)}
          className={cn(
            'rounded-sm px-3 py-1.5 text-xs font-medium transition-colors duration-fast ease-standard',
            active === pile
              ? 'bg-elevated text-fg'
              : 'text-muted hover:bg-elevated hover:text-fg',
          )}
        >
          {PILE_LABEL[pile]}{' '}
          <span className="font-mono text-subtle">{counts[pile]}</span>
        </button>
      ))}
    </div>
  )
}

// One row of a kept/dropped pile. Title and caption WRAP rather than truncate —
// a clip you are about to publish is exactly when you need to read them in full,
// and the previous `truncate` + native `title=` tooltip clipped long values in
// both places at once.
function PileRow({
  clip,
  onMove,
  busy,
}: {
  clip: ReviewClip
  onMove: (clip: ReviewClip, to: Pile) => void
  busy: boolean
}) {
  const title = clip.applied_title || clip.suggested_title || 'Untitled clip'
  const caption = clip.applied_description || clip.suggested_hook || null
  const dur = clip.end_s - (clip.setup_start_s ?? clip.start_s)
  return (
    <li className="flex items-start gap-3 rounded-md border border-default bg-surface p-3">
      <div className="min-w-0 flex-1">
        <p className="break-words text-sm font-medium text-fg">{title}</p>
        {caption && <p className="mt-1 break-words text-xs text-muted">{caption}</p>}
        <p className="mt-1.5 font-mono text-xs text-subtle">
          {dur.toFixed(1)}s
          {clip.rank != null && ` · rank ${clip.rank}`}
          {clip.render_status !== 'done' && ` · ${clip.render_status}`}
        </p>
      </div>
      <div className="flex shrink-0 flex-col gap-1.5">
        {clip.triage === 'kept' ? (
          <button
            onClick={() => onMove(clip, 'pending')}
            disabled={busy}
            className="rounded-sm border border-strong bg-bg px-2.5 py-1 text-xs text-muted hover:bg-elevated hover:text-fg disabled:opacity-50"
          >
            Un-keep
          </button>
        ) : (
          <button
            onClick={() => onMove(clip, 'pending')}
            disabled={busy}
            className="rounded-sm border border-strong bg-bg px-2.5 py-1 text-xs text-muted hover:bg-elevated hover:text-fg disabled:opacity-50"
          >
            Restore
          </button>
        )}
      </div>
    </li>
  )
}

export function PileList({
  pile,
  clips,
  videoId,
  onGoToReview,
}: {
  pile: Exclude<Pile, 'pending'>
  clips: ReviewClip[]
  videoId: string
  /** EmptyStatePrompt requires an action by design — an empty pile must offer
   *  the way to fill it, not just say it is empty. */
  onGoToReview: () => void
}) {
  const queryClient = useQueryClient()
  const [busyId, setBusyId] = useState<string | null>(null)
  const [error, setError] = useState('')

  // PUT /clips/{id}/triage is idempotent and safe to retry (Issue 444); the
  // feedback POST is not, which is why a pile move uses triage. No optimistic
  // update: this codebase has none, and Issue 437's contract is that a failed
  // write must never look like a success. The list refetches from the server,
  // so what you see is what was actually stored.
  async function move(clip: ReviewClip, to: Pile) {
    setError('')
    setBusyId(clip.id)
    try {
      await api(`/clips/${clip.id}/triage`, { method: 'PUT', body: { state: to } })
      await queryClient.invalidateQueries({ queryKey: ['review-clips', videoId] })
      await queryClient.invalidateQueries({ queryKey: ['clip-counts'] })
    } catch (e) {
      setError(
        e instanceof ApiError ? e.message : 'Couldn’t reach the server — nothing was saved.',
      )
    } finally {
      setBusyId(null)
    }
  }

  if (clips.length === 0)
    return (
      <EmptyStatePrompt
        className="mt-6"
        title={pile === 'kept' ? 'Nothing kept yet.' : 'Nothing dropped yet.'}
        detail={
          pile === 'kept'
            ? 'Clips you keep land here, ready to publish.'
            : 'Clips you drop land here. Nothing is deleted — you can restore any of them.'
        }
        actionLabel="Review clips"
        onAction={onGoToReview}
      />
    )

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      {error && (
        <p role="status" aria-live="polite" className="mb-2 text-xs text-danger">
          {error}
        </p>
      )}
      <ul className="flex flex-col gap-2">
        {clips.map((c) => (
          <PileRow key={c.id} clip={c} onMove={move} busy={busyId === c.id} />
        ))}
      </ul>
    </div>
  )
}
