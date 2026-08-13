import { useState } from 'react'
import { api, ApiError } from '@/lib/api'
import { CleanedPreviewConfirm } from '@/components/review/CleanedPreviewConfirm'
import { useClipRender } from '@/hooks/useClipRender'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { FeedbackAction, FeedbackPayload, ReviewClip, TaskQueued } from '@/types'
import { ArrowRight, Download, RotateCcw, Scissors, ThumbsDown, ThumbsUp } from '@/components/ui/icon'
import { ICON_INLINE, ICON_SIZE } from '@/components/ui/iconSizes'

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

type FlashTone = 'muted' | 'success' | 'danger'

const FLASH_TONE_CLASS: Record<FlashTone, string> = {
  muted: 'text-muted',
  success: 'text-success',
  danger: 'text-danger',
}

// The queue reads "Keep"/"Drop"; echoing the wire enum back ("upvote recorded")
// named the transport, not the creator's decision. `skip` is absent: it never
// POSTs (Issue 472 — pure navigation), so there is nothing to confirm.
type RatingAction = Exclude<FeedbackAction, 'skip'>

const ACTION_CONFIRMATION: Record<RatingAction, string> = {
  upvote: 'Kept',
  downvote: 'Dropped',
  trim: 'Trim saved',
}

// A 502/503 from the edge, or a dropped connection, means the write never
// reached the API — say so, because the clip stays put and the creator has no
// other way to tell a lost rating from a saved one.
function feedbackErrorText(e: unknown): string {
  if (e instanceof ApiError && e.status < 500) return e.message
  return "Couldn't reach the server — nothing was saved. Try again."
}

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
  // Issue 437: the tagged {text, tone} idiom the profile sections already use
  // (IdentitySection, BrandKitSection, DnaCard, …). A bare string forced ONE
  // colour on every outcome, so a failed keep/drop rendered in the success
  // green and read as confirmation.
  const [flash, setFlash] = useState<{ text: string; tone: FlashTone } | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [trimApplying, setTrimApplying] = useState(false)
  const [trimStatus, setTrimStatus] = useState<{ text: string; error: boolean } | null>(null)
  // Issue 451 — the re-render affordance for an ALREADY-rendered clip. Reuses the shared
  // ladder rather than reimplementing it; see the control below for why the stage's own
  // instance and this one cannot contend.
  const { rendering, renderError, sourceExpired, triggerRender } = useClipRender(clip)

  const downloadUrl = `/clips/${clip.id}/download`

  // Wave-1 trim-rerender contract: clip-relative seconds; the re-render lands in
  // cleaned_render_uri and rides the same review-then-confirm flow as the clean
  // pass. Separate from "Save trim", which only logs the preference signal.
  async function applyTrimRender() {
    setTrimStatus(null)
    try {
      await api<TaskQueued>(`/clips/${clip.id}/trim-render`, {
        method: 'POST',
        body: { trim_start_s: trimStart, trim_end_s: trimEnd },
      })
      setTrimApplying(true)
      setTrimStatus({
        text: 'Re-rendering with your trim — the preview appears below (~20s).',
        error: false,
      })
    } catch (e) {
      if (e instanceof ApiError && e.code === 'pending_clean_or_edit') {
        // A prior clean/edit is waiting on a decision — surface its preview so
        // the creator can confirm or discard instead of hitting a dead end.
        setTrimStatus({ text: e.message, error: true })
        setTrimApplying(true)
      } else if (e instanceof ApiError) {
        // 422 trim_noop / kept_too_short / removed_too_much messages embed
        // their limits — show them verbatim.
        setTrimStatus({ text: e.message, error: true })
      } else {
        setTrimStatus({ text: 'Could not queue the re-render — try again.', error: true })
      }
    }
  }

  /** Resolves true when the rating actually reached the API. */
  async function sendFeedback(
    action: RatingAction,
    tags?: string[],
    feedbackNote?: string,
  ): Promise<boolean> {
    if (submitting) return false
    const body: FeedbackPayload = { action }
    if (action === 'trim') {
      body.trim_start_s = trimStart
      body.trim_end_s = trimEnd
    }
    if (tags?.length) body.feedback_tags = tags
    if (feedbackNote) body.feedback_note = feedbackNote
    setSubmitting(true)
    setFlash({ text: 'Saving…', tone: 'muted' })
    try {
      await api(`/clips/${clip.id}/feedback`, { method: 'POST', body })
      setFlash({ text: ACTION_CONFIRMATION[action], tone: 'success' })
      setTimeout(() => setFlash(null), 1500)
      if (action !== 'trim') onAdvance()
      return true
    } catch (e) {
      // Deliberately NOT auto-cleared: a success can fade, a lost rating can't
      // (cf. SaveStatus.tsx — "PERSISTENT, NOT A TOAST").
      setFlash({ text: feedbackErrorText(e), tone: 'danger' })
      return false
    } finally {
      setSubmitting(false)
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

  async function submitTagged() {
    if (!panel || submitting) return
    const tags = Array.from(selected).filter((t) => t !== '__other__')
    // Closed only on success — a failed write leaves the panel, the tags and
    // the note exactly as the creator left them, so one click re-submits.
    if (await sendFeedback(panel, tags, note.trim() || undefined)) setPanel(null)
  }

  const tags = panel === 'upvote' ? APPROVE_TAGS : DENY_TAGS

  return (
    <div className="rounded-md border border-default bg-surface p-[18px] shadow-sm inset-shadow-highlight">
      <div className="mb-3.5 flex items-center justify-between">
        <span className="text-h3 font-semibold text-fg">Your call</span>
        {/* Rendered unconditionally: a live region must exist BEFORE its first
            update or the announcement is dropped (SaveStatus.tsx). One polite
            region serves both tones — a role that mutates per-outcome is
            announced unreliably. */}
        <span
          role="status"
          aria-live="polite"
          className={cn(
            'min-h-[14px] font-mono text-small',
            FLASH_TONE_CLASS[flash?.tone ?? 'muted'],
          )}
        >
          {flash?.text ?? ''}
        </span>
      </div>

      <div className="flex gap-2.5">
        <Button variant="success" className="h-[46px] flex-1 text-base" onClick={() => openPanel('upvote')}>
          <ThumbsUp className={ICON_SIZE.md} aria-hidden="true" /> Keep
        </Button>
        <Button variant="danger" className="h-[46px] flex-1 text-base" onClick={() => openPanel('downvote')}>
          <ThumbsDown className={ICON_SIZE.md} aria-hidden="true" /> Drop
        </Button>
      </div>

      <div className="mt-2.5 flex gap-2">
        {/* Issue 472: Skip is pure queue navigation — no POST. The old
            sendFeedback('skip') wrote a feedback row that RETRACTED the clip's
            latest label server-side, so Trim → Skip silently erased the trim. */}
        <Button
          variant="secondary"
          size="sm"
          className="h-[38px] flex-1"
          disabled={submitting}
          onClick={onAdvance}
        >
          Skip
        </Button>
        <Button
          variant="secondary"
          size="sm"
          className="h-[38px] flex-1"
          disabled={submitting}
          onClick={() => void sendFeedback('trim')}
        >
          <Scissors className={ICON_SIZE.sm} aria-hidden="true" /> Save trim
        </Button>
        {clip.render_uri ? (
          <a
            href={downloadUrl}
            download
            className="inline-flex h-[38px] flex-1 items-center justify-center gap-1.5 rounded-sm border border-strong bg-bg text-small text-muted inset-shadow-highlight hover:bg-elevated hover:text-fg"
          >
            <Download className={ICON_SIZE.sm} aria-hidden="true" /> Download
          </a>
        ) : (
          <span className="inline-flex h-[38px] flex-1 items-center justify-center gap-1.5 rounded-sm border border-strong bg-bg text-small text-subtle">
            <Download className={ICON_SIZE.sm} aria-hidden="true" /> Download
          </span>
        )}
      </div>

      <div className="mt-2 flex flex-col gap-1.5">
        <Button
          variant="secondary"
          size="sm"
          className="h-[38px] w-full"
          disabled={trimApplying || !clip.render_uri}
          onClick={applyTrimRender}
        >
          <RotateCcw className={ICON_SIZE.sm} aria-hidden="true" /> Apply trim &amp; re-render
        </Button>
        {trimStatus && (
          <p className={cn('text-xs', trimStatus.error ? 'text-danger' : 'text-subtle')}>
            {trimStatus.text}
          </p>
        )}
        {/*
          Issue 451 — a clip that has ALREADY rendered had no re-render path. The trigger
          lived only inside StagePlaceholder, which the stage swaps out the moment
          `render_uri` lands, so every render-pipeline fix (450's framing, 448's mask)
          could only ever reach clips rendered after the deploy. Verifying 450 on a real
          clip required replicating the endpoint's reset by hand against prod.

          This is the SAME `useClipRender` ladder the placeholder uses, not a second
          implementation. The two instances never contend: this control only exists while
          `render_uri` is set, which is exactly when the stage is showing the player and
          ignoring its own copy. Once the endpoint clears `render_uri`, the stage takes
          over and drives its spinner from server `render_status`.

          A celery-direct enqueue is NOT an alternative — the worker skips a clip that
          already has a `render_uri`. `POST /clips/{id}/render` owns the reset (Issue 353).
        */}
        {clip.render_uri &&
          (sourceExpired ? (
            <p className="text-xs text-subtle">
              The source video was purged under our retention window, so this clip can't be
              re-rendered. Upload it again to make new clips from it.
            </p>
          ) : (
            <Button
              variant="secondary"
              size="sm"
              className="h-[38px] w-full"
              disabled={rendering}
              onClick={() => void triggerRender()}
            >
              <RotateCcw className={ICON_SIZE.sm} aria-hidden="true" />
              {rendering ? 'Re-rendering…' : 'Re-render this clip'}
            </Button>
          ))}
        {clip.render_uri && rendering && !sourceExpired && (
          <p className="text-xs text-subtle">
            Rebuilding this clip with the current render settings (~30s). The player comes back
            when it lands.
          </p>
        )}
        {renderError && <p className="text-xs text-danger">{renderError}</p>}
        {trimApplying && (
          <CleanedPreviewConfirm
            clip={clip}
            enabled={trimApplying}
            onConfirmed={() => {
              setTrimApplying(false)
              setTrimStatus({ text: 'Trimmed version is now the main render.', error: false })
            }}
            onDiscarded={() => {
              setTrimApplying(false)
              setTrimStatus({ text: 'Keeping original render.', error: false })
            }}
            onError={(text) => setTrimStatus({ text, error: true })}
          />
        )}
      </div>

      {/* The publish title/description moved to ClipMetadataPanel (Issue 424) —
          this card is the decision, that panel is the packaging. */}

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
            <Button size="sm" onClick={() => void submitTagged()} disabled={submitting}>
              {submitting ? 'Saving…' : 'Submit'}
            </Button>
          </div>
        </div>
      )}

      {/* Quiet footer, not a destination (Issue 424 — moved off the stage; the
          accessible name is load-bearing for the shortlist queue tests). */}
      <div className="mt-3 flex justify-center border-t border-default pt-2.5">
        <button onClick={onAdvance} className="text-xs text-muted hover:text-fg">
          Next clip <ArrowRight className={`${ICON_SIZE.md} ${ICON_INLINE}`} aria-hidden="true" />
        </button>
      </div>
    </div>
  )
}
