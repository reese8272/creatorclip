import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Chip } from '@/components/Chip'
import type {
  VideoFeedbackListResponse,
  VideoFeedbackPayload,
  VideoSentiment,
} from '@/types'
import { ThumbsDown, ThumbsUp } from '@/components/ui/icon'
import { ICON_SIZE } from '@/components/ui/iconSizes'
import { VideoPlayer } from '@/components/ui/video-player'

// Video-level style tag taxonomy (Issue 370) — grounded in the clip-level
// APPROVE/DENY tags (YourCall) and extended with tone/structure/visuals.
// Server validates shape only, so this list can iterate without a deploy.
const LIKE_TAGS = [
  { id: 'pacing_feels_right', label: 'Pacing feels right' },
  { id: 'editing_style_mine', label: 'Editing style is mine' },
  { id: 'tone_on_brand', label: 'Tone is on-brand' },
  { id: 'strong_intro', label: 'Strong intro' },
  { id: 'clear_structure', label: 'Clear structure' },
  { id: 'visuals_work', label: 'Visuals work' },
]
const DISLIKE_TAGS = [
  { id: 'pacing_off', label: 'Pacing is off' },
  { id: 'editing_style_not_mine', label: "Editing style isn't mine" },
  { id: 'tone_off_brand', label: 'Tone is off-brand' },
  { id: 'weak_intro', label: 'Weak intro' },
  { id: 'rambling_structure', label: 'Rambling structure' },
  { id: 'visuals_flat', label: 'Visuals fall flat' },
]

// Standalone style review (Issue 370): watch any of your videos and record what
// works or doesn't about the STYLE — the "why" that teaches personalization
// (Issue 371 distills these notes into ranking). Works without generated clips;
// if the source is purged the player collapses but the form stays usable.
export function StyleReview({ videoId }: { videoId: string }) {
  const queryClient = useQueryClient()
  const [sentiment, setSentiment] = useState<VideoSentiment | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [note, setNote] = useState('')
  const [streamError, setStreamError] = useState(false)
  const [flash, setFlash] = useState('')

  const feedbackQuery = useQuery({
    queryKey: ['video-feedback', videoId],
    queryFn: () => api<VideoFeedbackListResponse>(`/videos/${videoId}/feedback`),
  })

  const submit = useMutation({
    mutationFn: (body: VideoFeedbackPayload) =>
      api(`/videos/${videoId}/feedback`, { method: 'POST', body }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['video-feedback', videoId] })
      setSentiment(null)
      setSelected(new Set())
      setNote('')
      setFlash('Style note saved — this teaches your ranking')
      setTimeout(() => setFlash(''), 2500)
    },
  })

  function toggleTag(id: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function submitReview() {
    if (!sentiment) return
    const tags = Array.from(selected)
    submit.mutate({
      sentiment,
      feedback_tags: tags.length ? tags : undefined,
      feedback_note: note.trim() || undefined,
    })
  }

  const tags = sentiment === 'like' ? LIKE_TAGS : DISLIKE_TAGS
  const canSubmit = !!sentiment && (selected.size > 0 || note.trim().length > 0)
  const errorMessage = submit.isError
    ? submit.error instanceof ApiError
      ? submit.error.message
      : 'Request failed — please retry.'
    : null
  const items = feedbackQuery.data?.items ?? []

  return (
    <main className="mx-auto grid w-full max-w-5xl flex-1 grid-cols-1 gap-6 px-4 py-8 lg:grid-cols-2">
      {/* Left: the video being reviewed */}
      <div className="flex flex-col gap-3">
        {streamError ? (
          <div className="flex aspect-video w-full flex-col items-center justify-center gap-2 rounded-xl border border-default bg-black/60 px-6 text-center text-sm text-subtle">
            <span>Source media isn’t available (retention window) — you can still record your
              style take from memory.</span>
          </div>
        ) : (
          <VideoPlayer
            src={`/videos/${videoId}/stream`}
            label="Video being style-reviewed"
            aspect="landscape"
            onError={() => setStreamError(true)}
            className="w-full"
          />
        )}
        <p className="text-small text-muted">
          Watch with your editor’s eye — what about the <em>style</em> works for your channel, and
          what doesn’t?
        </p>
      </div>

      {/* Right: the style take + past notes */}
      <div className="flex flex-col gap-4">
        <div className="rounded-md border border-default bg-surface p-[18px] shadow-sm inset-shadow-highlight">
          <div className="mb-3.5 flex items-center justify-between">
            <span className="flex items-center gap-2 text-h3 font-semibold text-fg">
              <Chip pose="book" size={26} />
              Your take on this style
            </span>
            <span className="min-h-[14px] font-mono text-small text-success">{flash}</span>
          </div>

          <div className="flex gap-2.5">
            <Button
              variant={sentiment === 'like' ? 'success' : 'secondary'}
              className="h-[42px] flex-1"
              onClick={() => setSentiment('like')}
            >
              <ThumbsUp className={ICON_SIZE.md} aria-hidden="true" /> Works for me
            </Button>
            <Button
              variant={sentiment === 'dislike' ? 'danger' : 'secondary'}
              className="h-[42px] flex-1"
              onClick={() => setSentiment('dislike')}
            >
              <ThumbsDown className={ICON_SIZE.md} aria-hidden="true" /> Not my style
            </Button>
          </div>

          {sentiment && (
            <div className="mt-3.5 animate-slide-up border-t border-default pt-3.5">
              <h4 className="mb-3 text-label uppercase tracking-[0.06em] text-muted">
                {sentiment === 'like' ? 'What works about it?' : 'What’s off about it?'}
              </h4>
              <div className="mb-3 flex flex-wrap gap-2">
                {tags.map((t) => (
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
              <textarea
                value={note}
                onChange={(e) => setNote(e.target.value)}
                maxLength={2000}
                rows={3}
                placeholder="In your own words — pacing, cuts, tone, structure… (this is what teaches the system most)"
                aria-label="Style note"
                className="mb-3 w-full rounded-sm border border-strong bg-bg px-3 py-2 text-xs leading-relaxed text-fg placeholder:text-subtle focus:border-accent focus:outline-none"
              />
              <div className="flex items-center justify-end gap-2">
                {errorMessage && <span className="mr-auto text-xs text-danger">{errorMessage}</span>}
                <Button
                  size="sm"
                  disabled={!canSubmit || submit.isPending}
                  title={canSubmit ? undefined : 'Pick at least one tag or write a note'}
                  onClick={submitReview}
                >
                  {submit.isPending ? 'Saving…' : 'Save style note'}
                </Button>
              </div>
            </div>
          )}
        </div>

        <div className="rounded-md border border-default bg-surface shadow-sm inset-shadow-highlight">
          <div className="border-b border-default px-4 py-3 text-label uppercase tracking-[0.06em] text-muted">
            Your style notes on this video
          </div>
          {feedbackQuery.isPending ? (
            <p className="px-4 py-3.5 text-small text-subtle">Loading…</p>
          ) : items.length === 0 ? (
            <p className="px-4 py-3.5 text-small text-subtle">
              No notes yet — your first take lands here.
            </p>
          ) : (
            items.map((item) => (
              <div key={item.id} className="border-b border-default px-4 py-3 last:border-b-0">
                <div className="mb-1 flex items-center gap-2 text-xs">
                  <span
                    className={cn(
                      'inline-flex items-center gap-1',
                      item.sentiment === 'like' ? 'text-success' : 'text-danger',
                    )}
                  >
                    {item.sentiment === 'like' ? (
                      <>
                        <ThumbsUp className={ICON_SIZE.xs} aria-hidden="true" /> Works
                      </>
                    ) : (
                      <>
                        <ThumbsDown className={ICON_SIZE.xs} aria-hidden="true" /> Not my style
                      </>
                    )}
                  </span>
                  <span className="font-mono text-subtle">
                    {new Date(item.created_at).toLocaleDateString()}
                  </span>
                </div>
                {item.feedback_tags && item.feedback_tags.length > 0 && (
                  <div className="mb-1 flex flex-wrap gap-1.5">
                    {item.feedback_tags.map((t) => (
                      <span
                        key={t}
                        className="rounded-sm bg-elevated px-2 py-0.5 font-mono text-[10px] text-muted"
                      >
                        {t}
                      </span>
                    ))}
                  </div>
                )}
                {item.feedback_note && (
                  <p className="text-small leading-relaxed text-fg">{item.feedback_note}</p>
                )}
              </div>
            ))
          )}
        </div>
      </div>
    </main>
  )
}
