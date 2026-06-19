import { useState } from 'react'
import { api } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { FitBadge } from '@/components/ui/fit-badge'
import { fitTier } from '@/lib/fit'
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

export function ClipPlayer({ clip, onAdvance }: { clip: ReviewClip; onAdvance: () => void }) {
  const clipDur = clip.end_s - clip.start_s
  const [trimStart, setTrimStart] = useState(0)
  const [trimEnd, setTrimEnd] = useState(clipDur)
  const [panel, setPanel] = useState<'upvote' | 'downvote' | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [note, setNote] = useState('')
  const [flash, setFlash] = useState('')

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
    <div className="flex animate-fade-in flex-col items-center gap-4">
      {clip.render_uri ? (
        <video
          key={clip.id}
          src={clip.render_uri}
          controls
          playsInline
          autoPlay
          className="aspect-[9/16] w-full max-w-[360px] rounded-xl border border-default bg-black shadow-accent-glow"
        />
      ) : (
        <div className="flex aspect-[9/16] w-full max-w-[360px] items-center justify-center rounded-xl border border-default bg-black text-sm text-subtle">
          Not yet rendered
        </div>
      )}

      <div className="flex flex-col items-center gap-2">
        <div className="text-center font-mono text-xs text-muted">
          Clip #{clip.rank ?? '—'} · {(clip.end_s - (clip.setup_start_s ?? clip.start_s)).toFixed(1)}s
        </div>
        {/* Headline fit signal is the honest tier, not a raw number (docs/UI.md). */}
        <FitBadge tier={fitTier(clip.score)} />
      </div>

      <div className="w-full max-w-[360px]">
        <label className="mb-1 block text-xs uppercase tracking-[0.04em] text-muted">
          Trim start
        </label>
        <div className="flex items-center gap-2">
          <input
            type="range"
            aria-label="Trim start"
            min={0}
            max={clipDur}
            step={0.1}
            value={trimStart}
            onChange={(e) => setTrimStart(parseFloat(e.target.value))}
            className="flex-1 accent-[var(--color-accent)]"
          />
          <span className="w-12 text-right font-mono text-xs text-fg">{trimStart.toFixed(1)}s</span>
        </div>
        <label className="mb-1 mt-2 block text-xs uppercase tracking-[0.04em] text-muted">
          Trim end
        </label>
        <div className="flex items-center gap-2">
          <input
            type="range"
            aria-label="Trim end"
            min={0}
            max={clipDur}
            step={0.1}
            value={trimEnd}
            onChange={(e) => setTrimEnd(parseFloat(e.target.value))}
            className="flex-1 accent-[var(--color-accent)]"
          />
          <span className="w-12 text-right font-mono text-xs text-fg">{trimEnd.toFixed(1)}s</span>
        </div>
      </div>

      <div className="min-h-[18px] text-center text-xs text-success">{flash}</div>

      <div className="flex flex-wrap justify-center gap-2">
        <Button variant="confirm" onClick={() => openPanel('upvote')}>
          👍 Keep
        </Button>
        <Button variant="danger" onClick={() => openPanel('downvote')}>
          👎 Drop
        </Button>
        <Button variant="secondary" onClick={() => sendFeedback('skip')}>
          Skip
        </Button>
        <Button onClick={() => sendFeedback('trim')}>✂ Trim</Button>
      </div>

      {panel && (
        <div className="w-full max-w-[360px] animate-slide-up rounded-md border border-default bg-surface p-4 shadow-sm shadow-inset">
          <h4 className="mb-3 text-xs uppercase tracking-[0.06em] text-muted">
            {panel === 'upvote' ? 'Why are you keeping this?' : 'Why are you dropping this?'}
          </h4>
          <div className="mb-3 flex flex-wrap gap-2">
            {[...tags, { id: '__other__', label: 'Other…' }].map((t) => (
              <button
                key={t.id}
                onClick={() => toggleTag(t.id)}
                className={cn(
                  'rounded-md border px-3 py-1 text-xs',
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
              className="mb-3 w-full rounded-md border border-strong bg-bg px-3 py-2 text-xs text-fg placeholder:text-subtle focus:border-accent focus:outline-none"
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

      <button onClick={onAdvance} className="text-xs text-muted hover:text-fg">
        Next clip →
      </button>
    </div>
  )
}
