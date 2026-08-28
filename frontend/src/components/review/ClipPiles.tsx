import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api'
import { EmptyStatePrompt } from '@/components/EmptyStatePrompt'
import { PublishPanel } from '@/components/review/PublishPanel'
import { Badge } from '@/components/ui/badge'
import { Download } from '@/components/ui/icon'
import { ICON_SIZE } from '@/components/ui/iconSizes'
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
  // Roving tabindex (Issue 445 AC6): one tab stop for the whole tablist, with
  // ←/→/Home/End moving between piles. Selection follows focus — moving to a
  // tab selects it — matching the WAI-ARIA tabs pattern for a small, cheap
  // tab switch (each pile is already-loaded client-side data).
  function onKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    const idx = PILE_ORDER.indexOf(active)
    let next: number
    if (e.key === 'ArrowRight') next = (idx + 1) % PILE_ORDER.length
    else if (e.key === 'ArrowLeft') next = (idx - 1 + PILE_ORDER.length) % PILE_ORDER.length
    else if (e.key === 'Home') next = 0
    else if (e.key === 'End') next = PILE_ORDER.length - 1
    else return
    e.preventDefault()
    const pile = PILE_ORDER[next]
    onSelect(pile)
    e.currentTarget
      .querySelector<HTMLButtonElement>(`[data-pile="${pile}"]`)
      ?.focus()
  }

  return (
    <div
      className="flex shrink-0 items-center gap-1"
      role="tablist"
      aria-label="Clip piles"
      onKeyDown={onKeyDown}
    >
      {PILE_ORDER.map((pile) => (
        <button
          key={pile}
          data-pile={pile}
          role="tab"
          aria-selected={active === pile}
          tabIndex={active === pile ? 0 : -1}
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

// Issue 447 — the Keep pile's finish line: where a kept clip stands on the
// rendered → downloaded → published path. Neutral workflow states only — a chip
// describes what has HAPPENED, never predicted performance or reach.
function finishLineSteps(clip: ReviewClip): { label: string; done: boolean }[] {
  const rendered = clip.render_status === 'done' && Boolean(clip.render_uri)
  const downloaded = Boolean(clip.downloaded_at)
  const pub = clip.latest_publication
  // The third chip narrates the newest publication's state; anything not yet
  // done (incl. failed — details live in the expanded Publish panel) stays todo.
  let publishLabel = 'Published'
  let published = false
  if (pub?.status === 'done') published = true
  else if (pub?.status === 'scheduled' || pub?.status === 'confirmed') publishLabel = 'Scheduled'
  else if (pub?.status === 'pending' || pub?.status === 'running') publishLabel = 'Publishing'
  return [
    { label: 'Rendered', done: rendered },
    { label: 'Downloaded', done: downloaded },
    { label: publishLabel, done: published },
  ]
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
  const [publishOpen, setPublishOpen] = useState(false)
  const title = clip.applied_title || clip.suggested_title || 'Untitled clip'
  const caption = clip.applied_description || clip.suggested_hook || null
  const dur = clip.end_s - (clip.setup_start_s ?? clip.start_s)
  const kept = clip.triage === 'kept'
  // Same authed same-origin pattern as YourCall's download affordance: the
  // session cookie rides the request; the server 302s to a presigned URL.
  const downloadUrl = `/clips/${clip.id}/download`
  return (
    <li className="flex flex-col rounded-md border border-default bg-surface p-3">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <p className="break-words text-sm font-medium text-fg">{title}</p>
          {caption && <p className="mt-1 break-words text-xs text-muted">{caption}</p>}
          <p className="mt-1.5 font-mono text-xs text-subtle">
            {dur.toFixed(1)}s
            {clip.rank != null && ` · rank ${clip.rank}`}
            {clip.render_status !== 'done' && ` · ${clip.render_status}`}
          </p>
          {kept && (
            <div className="mt-2 flex flex-wrap items-center gap-1.5" data-testid="finish-line">
              {finishLineSteps(clip).map((step) => (
                <Badge
                  key={step.label}
                  variant={step.done ? 'success' : 'muted'}
                  data-state={step.done ? 'done' : 'todo'}
                >
                  {step.label}
                </Badge>
              ))}
            </div>
          )}
        </div>
        <div className="flex shrink-0 flex-col gap-1.5">
          {kept ? (
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
          {kept && clip.render_uri && (
            <a
              href={downloadUrl}
              download
              className="inline-flex items-center justify-center gap-1 rounded-sm border border-strong bg-bg px-2.5 py-1 text-xs text-muted hover:bg-elevated hover:text-fg"
            >
              <Download className={ICON_SIZE.sm} aria-hidden="true" /> Download
            </a>
          )}
          {kept && (
            <button
              onClick={() => setPublishOpen((v) => !v)}
              className={cn(
                'rounded-sm border px-2.5 py-1 text-xs',
                publishOpen
                  ? 'border-accent bg-accent-soft text-accent-text'
                  : 'border-strong bg-bg text-muted hover:bg-elevated hover:text-fg',
              )}
            >
              {publishOpen ? 'Hide publish' : 'Publish'}
            </button>
          )}
        </div>
      </div>
      {/* Schedule-publish from the pile (Issue 447) — the full PublishPanel,
          reused: schedule dialog, confirm/cancel, status rows and privacy note
          all behave exactly as they do on the review card. */}
      {kept && publishOpen && (
        <div className="mt-3">
          <PublishPanel clip={clip} />
        </div>
      )}
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
      // The field is `triage`, pinned by a contract test: the backend declares
      // TriageIn with extra="forbid" precisely so a wrong key (this call once
      // sent `state`) fails loudly as a 422 instead of a cheerful silent no-op.
      await api(`/clips/${clip.id}/triage`, { method: 'PUT', body: { triage: to } })
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
