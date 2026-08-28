import { useState } from 'react'
import { api, ApiError } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { RotateCcw, X } from '@/components/ui/icon'
import { ICON_SIZE } from '@/components/ui/iconSizes'
import { cn } from '@/lib/utils'
import type { FeedbackPayload, ReviewClip } from '@/types'

const APPROVE_TAGS = [
  { id: 'titles_fit_style', label: 'Titles fit my style' },
  { id: 'editing_matches_pace', label: 'Editing matches my pace' },
  { id: 'good_hook', label: 'Good hook' },
  { id: 'right_length', label: 'Right length' },
]
const DENY_TAGS = [
  { id: 'editing_mismatch', label: "Editing doesn't match" },
  { id: 'off_brand_topic', label: 'Off-brand topic' },
  { id: 'bad_hook', label: 'Bad hook' },
  { id: 'wrong_length', label: 'Wrong length' },
]

export interface LastCall {
  clip: ReviewClip
  action: 'upvote' | 'downvote'
  /** Position in the pending queue when the verdict landed — Undo returns here. */
  index: number
}

// Issue 445 (owner decision 2026-08-10): the verdict commits on the FIRST
// click and the queue advances; the "why?" tags become OPTIONAL post-hoc
// enrichment for the clip just rated, with an Undo. This strip is page-level
// because YourCall remounts per clip (key={clip.id}) — its state cannot
// outlive the advance.
//
// Undo is a triage retraction, PUT /clips/{id}/triage → pending — NEVER a
// `skip` feedback POST (Issue 472: skip is a pure no-op ack, not a verdict).
export function LastCallStrip({
  call,
  onUndone,
  onDismiss,
}: {
  call: LastCall
  /** The retraction reached the server — the page rewinds the queue to the clip. */
  onUndone: (call: LastCall) => void
  onDismiss: () => void
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)

  const kept = call.action === 'upvote'
  const tags = kept ? APPROVE_TAGS : DENY_TAGS
  const title = call.clip.applied_title || call.clip.suggested_title || 'Untitled clip'

  function toggleTag(id: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  // Re-records the SAME verdict with tags attached — the feedback log is
  // append-only and the trainer reads the latest verdict per clip (Issue 444),
  // so enriching cannot flip or duplicate the decision.
  async function saveTags() {
    if (busy) return
    const tagList = Array.from(selected).filter((t) => t !== '__other__')
    const trimmedNote = note.trim()
    if (!tagList.length && !trimmedNote) return
    const body: FeedbackPayload = { action: call.action }
    if (tagList.length) body.feedback_tags = tagList
    if (trimmedNote) body.feedback_note = trimmedNote
    setBusy(true)
    setError('')
    try {
      await api(`/clips/${call.clip.id}/feedback`, { method: 'POST', body })
      setSaved(true)
      // The strip has done its job — linger briefly so "Noted." is readable.
      setTimeout(onDismiss, 1200)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Couldn’t reach the server — nothing was saved.')
    } finally {
      setBusy(false)
    }
  }

  async function undo() {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      await api(`/clips/${call.clip.id}/triage`, { method: 'PUT', body: { triage: 'pending' } })
      onUndone(call)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Couldn’t reach the server — nothing was saved.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      data-testid="last-call-strip"
      className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-2 rounded-md border border-default bg-surface px-3 py-2"
    >
      <span className="text-xs text-muted">
        <span className={cn('font-medium', kept ? 'text-success' : 'text-danger')}>
          {kept ? 'Kept' : 'Dropped'}
        </span>{' '}
        <span className="break-all text-fg">{title}</span>
      </span>
      {saved ? (
        <span role="status" aria-live="polite" className="text-xs text-success">
          Noted.
        </span>
      ) : (
        <>
          <span className="text-xs text-subtle">Add why? (optional)</span>
          <div className="flex flex-wrap items-center gap-1.5">
            {[...tags, { id: '__other__', label: 'Other…' }].map((t) => (
              <button
                key={t.id}
                onClick={() => toggleTag(t.id)}
                className={cn(
                  'rounded-sm border px-2 py-0.5 text-xs',
                  selected.has(t.id)
                    ? 'border-accent bg-accent-soft text-accent-text'
                    : 'border-strong bg-bg text-muted hover:border-muted hover:text-fg',
                )}
              >
                {t.label}
              </button>
            ))}
            {selected.has('__other__') && (
              <input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Tell us more…"
                className="w-40 rounded-sm border border-strong bg-bg px-2 py-0.5 text-xs text-fg placeholder:text-subtle focus:border-accent focus:outline-none"
              />
            )}
            {(selected.size > 0 || note.trim()) && (
              <Button size="sm" className="h-6 px-2 text-xs" disabled={busy} onClick={() => void saveTags()}>
                {busy ? 'Saving…' : 'Save'}
              </Button>
            )}
          </div>
        </>
      )}
      <div className="ml-auto flex items-center gap-2">
        {error && (
          <span role="status" aria-live="polite" className="text-xs text-danger">
            {error}
          </span>
        )}
        {!saved && (
          <button
            onClick={() => void undo()}
            disabled={busy}
            className="inline-flex items-center gap-1 text-xs text-muted hover:text-fg disabled:opacity-50"
          >
            <RotateCcw className={ICON_SIZE.sm} aria-hidden="true" /> Undo
          </button>
        )}
        <button onClick={onDismiss} className="text-subtle hover:text-fg" aria-label="Dismiss">
          <X className={ICON_SIZE.sm} aria-hidden="true" />
        </button>
      </div>
    </div>
  )
}
