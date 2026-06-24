import { useState } from 'react'
import { api } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { FeedbackAction, FeedbackPayload, ReviewClip } from '@/types'

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

// Issue 306: the triage actions grouped into one "Your call" card (moved out of
// ClipPlayer). Keep/Drop open the inline feedback-tag panel; Save trim submits
// the trim region the filmstrip produced; Download streams the rendered clip.
export function YourCall({
  clip,
  trimStart,
  trimEnd,
  onAdvance,
}: {
  clip: ReviewClip
  trimStart: number
  trimEnd: number
  onAdvance: () => void
}) {
  const [panel, setPanel] = useState<'upvote' | 'downvote' | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [note, setNote] = useState('')
  const [flash, setFlash] = useState('')

  const downloadUrl = `/clips/${clip.id}/download`

  async function sendFeedback(action: FeedbackAction, tags?: string[], feedbackNote?: string) {
    const body: FeedbackPayload = { action }
    if (action === 'trim') {
      body.trim_start_s = trimStart
      body.trim_end_s = trimEnd
    }
    if (tags?.length) body.feedback_tags = tags
    if (feedbackNote) body.feedback_note = feedbackNote
    try {
      await api(`/clips/${clip.id}/feedback`, { method: 'POST', body })
      setFlash(action === 'trim' ? 'Trim saved ✓' : `${action} recorded ✓`)
      setTimeout(() => setFlash(''), 1500)
      if (action !== 'trim') onAdvance()
    } catch {
      setFlash('Error — try again')
    }
  }

  function openPanel(action: 'upvote' | 'downvote') {
    setPanel(action)
    setSelected(new Set())
    setNote('')
  }

  function toggleTag(id: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function submitTagged() {
    if (!panel) return
    const tags = Array.from(selected).filter((t) => t !== '__other__')
    const action = panel
    setPanel(null)
    sendFeedback(action, tags, note.trim() || undefined)
  }

  const tags = panel === 'upvote' ? APPROVE_TAGS : DENY_TAGS

  return (
    <div className="rounded-md border border-default bg-surface p-[18px] shadow-sm shadow-inset">
      <div className="mb-3.5 flex items-center justify-between">
        <span className="text-h3 font-semibold text-fg">Your call</span>
        <span className="min-h-[14px] font-mono text-small text-success">{flash}</span>
      </div>

      <div className="flex gap-2.5">
        <Button variant="confirm" className="h-[46px] flex-1 text-base" onClick={() => openPanel('upvote')}>
          👍 Keep
        </Button>
        <Button variant="danger" className="h-[46px] flex-1 text-base" onClick={() => openPanel('downvote')}>
          👎 Drop
        </Button>
      </div>

      <div className="mt-2.5 flex gap-2">
        <Button variant="secondary" size="sm" className="h-[38px] flex-1" onClick={() => sendFeedback('skip')}>
          Skip
        </Button>
        <Button variant="secondary" size="sm" className="h-[38px] flex-1" onClick={() => sendFeedback('trim')}>
          ✂ Save trim
        </Button>
        {clip.render_uri ? (
          <a
            href={downloadUrl}
            download
            className="inline-flex h-[38px] flex-1 items-center justify-center gap-1.5 rounded-sm border border-strong bg-bg text-small text-muted shadow-inset hover:bg-elevated hover:text-fg"
          >
            ⬇ Download
          </a>
        ) : (
          <span className="inline-flex h-[38px] flex-1 items-center justify-center rounded-sm border border-strong bg-bg text-small text-subtle">
            ⬇ Download
          </span>
        )}
      </div>

      {panel && (
        <div className="mt-3.5 animate-slide-up border-t border-default pt-3.5">
          <h4 className="mb-3 text-label uppercase tracking-[0.06em] text-muted">
            {panel === 'upvote' ? 'Why are you keeping this?' : 'Why are you dropping this?'}
          </h4>
          <div className="mb-3 flex flex-wrap gap-2">
            {[...tags, { id: '__other__', label: 'Other…' }].map((t) => (
              <button
                key={t.id}
                onClick={() => toggleTag(t.id)}
                className={cn(
                  'rounded-sm border px-3 py-1 text-xs',
                  selected.has(t.id)
                    ? 'border-accent bg-accent-soft text-accent-text'
                    : 'border-strong bg-bg text-muted hover:border-muted hover:text-fg',
                )}
              >
                {t.label}
              </button>
            ))}
          </div>
          {selected.has('__other__') && (
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Tell us more…"
              className="mb-3 w-full rounded-sm border border-strong bg-bg px-3 py-2 text-xs text-fg placeholder:text-subtle focus:border-accent focus:outline-none"
            />
          )}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" size="sm" onClick={() => setPanel(null)}>
              Cancel
            </Button>
            <Button size="sm" onClick={submitTagged}>
              Submit
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
