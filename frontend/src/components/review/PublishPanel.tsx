import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { nextOccurrence } from '@/lib/schedule'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Modal } from '@/components/ui/modal'
import { cn } from '@/lib/utils'
import type { PublicationListOut, PublicationOut, ReviewClip, SuggestedWindow } from '@/types'

// The backend repurposes status=failed for creator cancels, with this exact
// error string (routers/publications.py). The badge infers "Cancelled" from it.
const CANCELLED_ERROR = 'Cancelled by creator'

// Statuses the backend allows POST …/cancel on (409 otherwise).
const CANCELLABLE = new Set(['scheduled', 'confirmed'])

function statusBadge(pub: PublicationOut) {
  if (pub.status === 'failed' && pub.error === CANCELLED_ERROR)
    return <Badge variant="muted">Cancelled</Badge>
  switch (pub.status) {
    case 'scheduled':
      return <Badge variant="muted">Scheduled</Badge>
    case 'confirmed':
      return <Badge variant="accent">Confirmed</Badge>
    case 'pending':
    case 'running':
      return <Badge variant="warning">{pub.status === 'pending' ? 'Pending' : 'Uploading'}</Badge>
    case 'done':
      return <Badge variant="success">Published</Badge>
    default:
      return <Badge variant="danger">Failed</Badge>
  }
}

// ── One publication row: badge, time, per-status actions ─────────────────────

function PublicationRow({ clip, pub }: { clip: ReviewClip; pub: PublicationOut }) {
  const queryClient = useQueryClient()
  const [actionError, setActionError] = useState('')

  function actionMutation(action: 'confirm' | 'cancel') {
    return {
      mutationFn: () =>
        api<PublicationOut>(`/clips/${clip.id}/publications/${pub.id}/${action}`, {
          method: 'POST',
        }),
      onSuccess: async () => {
        setActionError('')
        await queryClient.invalidateQueries({ queryKey: ['publications', clip.id] })
      },
      onError: (e: Error) => setActionError(e.message),
    }
  }
  const confirm = useMutation(actionMutation('confirm'))
  const cancel = useMutation(actionMutation('cancel'))
  const busy = confirm.isPending || cancel.isPending
  const cancelled = pub.status === 'failed' && pub.error === CANCELLED_ERROR

  return (
    <li className="rounded-sm border border-default bg-bg p-2.5 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        {statusBadge(pub)}
        {pub.scheduled_at && (
          <span className="text-muted">{new Date(pub.scheduled_at).toLocaleString()}</span>
        )}
        {pub.status === 'done' && pub.youtube_video_id && (
          <a
            href={`https://youtu.be/${encodeURIComponent(pub.youtube_video_id)}`}
            target="_blank"
            rel="noreferrer"
            className="text-accent-text underline-offset-2 hover:underline"
          >
            Watch on YouTube →
          </a>
        )}
        <span className="ml-auto flex gap-1.5">
          {pub.status === 'scheduled' && (
            <Button size="sm" disabled={busy} onClick={() => confirm.mutate()}>
              Confirm to lock in
            </Button>
          )}
          {CANCELLABLE.has(pub.status) && (
            <Button variant="secondary" size="sm" disabled={busy} onClick={() => cancel.mutate()}>
              Cancel
            </Button>
          )}
        </span>
      </div>
      {pub.status === 'failed' && !cancelled && pub.error && (
        <p className="mt-1 text-danger">{pub.error}</p>
      )}
      {actionError && <p className="mt-1 text-danger">{actionError}</p>}
    </li>
  )
}

// ── SchedulePublishDialog — quick-pick windows + typed datetime fallback ─────

// NN/g date-input guidance: quick-pick chips for the <10 suggested windows,
// ALWAYS a typed input as fallback, and specific correction feedback for past
// dates. Serialization is toISOString() — tz-aware UTC, per the backend
// validator (naive or past datetimes 422).
export function SchedulePublishDialog({
  open,
  clip,
  windows,
  privacyNote,
  onClose,
}: {
  open: boolean
  clip: ReviewClip
  windows: SuggestedWindow[]
  privacyNote: string | undefined
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [picked, setPicked] = useState<{ label: string; when: Date } | null>(null)
  const [manual, setManual] = useState('')
  const [error, setError] = useState('')

  const schedule = useMutation({
    mutationFn: (scheduledAt: string) =>
      api<PublicationOut>(`/clips/${clip.id}/publications`, {
        method: 'POST',
        body: { scheduled_at: scheduledAt },
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['publications', clip.id] })
      setPicked(null)
      setManual('')
      setError('')
      // Two-step flow: the new scheduled row now shows "Confirm to lock in".
      onClose()
    },
    onError: (e: Error) => setError(e.message),
  })

  function submit() {
    const when = manual ? new Date(manual) : picked?.when
    if (!when || Number.isNaN(when.getTime())) {
      setError('Pick a suggested window or enter a date and time.')
      return
    }
    if (when.getTime() <= Date.now()) {
      setError('That time is in the past — pick a future date and time.')
      return
    }
    schedule.mutate(when.toISOString())
  }

  return (
    <Modal open={open} title="Schedule publish" onClose={onClose}>
      <div className="flex flex-col gap-3 text-sm">
        {windows.length > 0 && (
          <div>
            <p className="mb-1.5 text-xs text-muted">
              Suggested from your audience&apos;s weekly activity:
            </p>
            <div className="flex flex-wrap gap-2">
              {windows.map((w) => (
                <button
                  key={`${w.day_of_week}-${w.hour}`}
                  onClick={() => {
                    setPicked({ label: w.label, when: nextOccurrence(w.day_of_week, w.hour) })
                    setManual('')
                    setError('')
                  }}
                  className={cn(
                    'rounded-sm border px-3 py-1 text-xs',
                    picked?.label === w.label && !manual
                      ? 'border-accent bg-accent-soft text-accent-text'
                      : 'border-strong bg-bg text-muted hover:border-muted hover:text-fg',
                  )}
                >
                  {w.label}
                </button>
              ))}
            </div>
            {picked && !manual && (
              <p className="mt-1.5 text-xs text-subtle">
                Next {picked.label}: {picked.when.toLocaleString()}
              </p>
            )}
          </div>
        )}

        <label className="flex flex-col gap-1 text-xs text-muted">
          Or pick an exact date and time
          <input
            type="datetime-local"
            value={manual}
            onChange={(e) => {
              setManual(e.target.value)
              setError('')
            }}
            aria-label="Publish date and time"
            className="rounded-sm border border-strong bg-bg px-3 py-2 text-xs text-fg focus:border-accent focus:outline-none"
          />
        </label>

        {privacyNote && <p className="text-xs text-subtle">{privacyNote}</p>}
        {error && <p className="text-xs text-danger">{error}</p>}

        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={onClose}>
            Close
          </Button>
          <Button size="sm" disabled={schedule.isPending} onClick={submit}>
            {schedule.isPending ? 'Scheduling…' : 'Schedule'}
          </Button>
        </div>
      </div>
    </Modal>
  )
}

// ── PublishPanel — list + poll + schedule entry point ────────────────────────

// Issue 196 frontend: schedule a private upload of the rendered clip into the
// creator's audience-activity windows. Polls while an upload is in flight
// (Review.tsx refetchInterval idiom); every mutation awaits invalidation of
// ['publications', clipId].
export function PublishPanel({ clip }: { clip: ReviewClip }) {
  const [dialogOpen, setDialogOpen] = useState(false)
  const rendered = Boolean(clip.render_uri)

  const query = useQuery({
    queryKey: ['publications', clip.id],
    queryFn: () => api<PublicationListOut>(`/clips/${clip.id}/publications`),
    enabled: rendered,
    refetchInterval: (q) => {
      const pubs = q.state.data?.publications ?? []
      const inFlight = pubs.some((p) => p.status === 'pending' || p.status === 'running')
      return inFlight ? 4000 : false
    },
  })

  const pubs = query.data?.publications ?? []
  const windows = query.data?.suggested_windows ?? []
  const privacyNote = query.data?.privacy_note

  return (
    <div className="rounded-md border border-default bg-surface p-[18px] shadow-sm shadow-inset">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-h3 font-semibold text-fg">Publish</span>
        <Button size="sm" disabled={!rendered} onClick={() => setDialogOpen(true)}>
          Schedule publish
        </Button>
      </div>

      {!rendered && (
        <p className="text-xs text-subtle">Render this clip to schedule a publish.</p>
      )}
      {rendered && privacyNote && <p className="mb-2 text-xs text-subtle">{privacyNote}</p>}

      {pubs.length > 0 && (
        <ul className="flex flex-col gap-2">
          {pubs.map((p) => (
            <PublicationRow key={p.id} clip={clip} pub={p} />
          ))}
        </ul>
      )}
      {query.data?.truncated && (
        <p className="mt-2 text-xs text-subtle">Showing the 50 most recent publications.</p>
      )}

      <SchedulePublishDialog
        open={dialogOpen}
        clip={clip}
        windows={windows}
        privacyNote={privacyNote}
        onClose={() => setDialogOpen(false)}
      />
    </div>
  )
}
