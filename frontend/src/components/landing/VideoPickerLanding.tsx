import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api'
import { videosRefetchInterval } from '@/lib/videosPoll'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { QueryErrorState } from '@/components/QueryErrorState'
import { EmptyStatePrompt } from '@/components/EmptyStatePrompt'
import { SOURCE_NEEDED_HELP, STATUS_VARIANT } from '@/components/dashboard/videoStatus'
import { InlineUploadFlow } from '@/components/landing/InlineUploadFlow'
import type {
  ClipCountsResponse,
  ReviewClip,
  ReviewClipListResponse,
  Video,
  VideoListResponse,
} from '@/types'

// Standalone landing for /review and /editor (no video_id param): instead of a
// dead-end "go to Dashboard" message, the creator picks a processed video or
// uploads a new one in place. Selection stays URL-only — a row click just sets
// ?video_id= on the current route and the page takes over.
//
// Deliberate divergences from the Dashboard's VideoTable:
//  - No per-row StageStepper: the server caps SSE at 3 concurrent streams per
//    creator (useStageStream.ts) — only the single InlineUploadFlow card opens
//    one. In-flight rows show a static badge and ride the 5s videos poll.
//  - Generate is consumed, not fire-and-forget: the response body carries the
//    clips list, so on success we advance straight into the tool.

// Issue 355: the headings were step titles ("Pick a video to review"), so the
// nav promised "Review" and the page called itself something else. The page name
// is now the h1 and the instruction moved down into the sub-line, where it still
// tells a first-run creator exactly what to do.
const TOOL_COPY = {
  review: {
    heading: 'Review clips',
    sub: 'Pick a processed video to review its clips, or upload a new one and start here.',
    cta: 'Review clips',
  },
  editor: {
    heading: 'Editor',
    sub: 'Pick a processed video to work its clips, or upload a new one and start here.',
    cta: 'Open in editor',
  },
} as const

// Explicit, minutes-consuming generate CTA. Unlike the Dashboard's
// fire-and-invalidate button this awaits the response body: clips > 0 advances
// into the tool via onClips; billing/limit errors (402/429/503) surface their
// server-authored message verbatim. Never auto-fired — generation spends the
// creator's minutes, so it must stay a consented click.
export function GenerateClipsButton({
  videoId,
  onClips,
}: {
  videoId: string
  onClips: (clips: ReviewClip[]) => void
}) {
  const queryClient = useQueryClient()
  const gen = useMutation({
    mutationFn: () =>
      api<ReviewClipListResponse>(`/videos/${videoId}/clips/generate`, { method: 'POST' }),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['videos'] })
      void queryClient.invalidateQueries({ queryKey: ['clip-counts'] })
      void queryClient.invalidateQueries({ queryKey: ['review-clips', videoId] })
      onClips(data.clips)
    },
  })

  const errorMessage = gen.isError
    ? gen.error instanceof ApiError
      ? gen.error.message
      : 'Request failed — please retry.'
    : null

  return (
    <div className="flex flex-col gap-1">
      <Button size="sm" disabled={gen.isPending} onClick={() => gen.mutate()}>
        {gen.isPending
          ? 'Generating… (this takes a minute or two)'
          : gen.isError
            ? 'Retry generate'
            : 'Generate clips'}
      </Button>
      {errorMessage && <span className="max-w-[280px] text-xs text-danger">{errorMessage}</span>}
    </div>
  )
}

// "Why no clips?" transparency link — same idiom as the Dashboard's ActionCell:
// the per-video timeline map renders skip_reason_label in its empty state.
function WhyNoClipsLink({ videoId }: { videoId: string }) {
  return (
    <Link
      to={`/video/${videoId}`}
      title="See why no clips were generated for this video"
      className="text-xs text-subtle hover:text-accent-text"
    >
      Why no clips?
    </Link>
  )
}

// Action cell for one picker row, mirroring the honesty logic of the
// Dashboard's ActionCell without navigation dead ends.
function PickerRowAction({
  video,
  counts,
  countsLoading,
  ctaLabel,
  onOpen,
  onOpenStyle,
}: {
  video: Video
  counts: { total: number; rendered: number } | undefined
  countsLoading: boolean
  ctaLabel: string
  onOpen: () => void
  /** Review tool only (Issue 370): open the video-level style review. */
  onOpenStyle?: () => void
}) {
  const queryClient = useQueryClient()
  const [noClips, setNoClips] = useState(false)

  const requeue = useMutation({
    mutationFn: () => api(`/videos/${video.id}/queue`, { method: 'POST' }),
    onSuccess: () => {
      setTimeout(() => void queryClient.invalidateQueries({ queryKey: ['videos'] }), 3000)
    },
  })

  if (!video.clippable) {
    return (
      <span
        title={SOURCE_NEEDED_HELP}
        className="cursor-help border-b border-dotted border-default text-sm text-subtle"
      >
        Upload source file to clip
      </span>
    )
  }

  if (video.ingest_status === 'pending' || video.ingest_status === 'running') {
    return <span className="text-sm text-subtle">Processing — a few minutes</span>
  }

  if (video.ingest_status === 'failed') {
    return (
      <Button
        variant="secondary"
        size="sm"
        disabled={requeue.isPending || requeue.isSuccess}
        onClick={() => requeue.mutate()}
      >
        {requeue.isPending || requeue.isSuccess ? 'Queued…' : 'Retry analysis'}
      </Button>
    )
  }

  // done
  if (countsLoading || !counts) return <span className="text-sm text-subtle">…</span>

  const styleLink = onOpenStyle && (
    <button onClick={onOpenStyle} className="text-xs text-subtle hover:text-accent-text">
      Review the style
    </button>
  )

  if (counts.total > 0) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={onOpen}>
          {ctaLabel}
        </Button>
        {styleLink}
      </div>
    )
  }

  if (noClips) {
    return (
      <span className="flex flex-col gap-1 text-xs text-muted">
        No strong moments matched your clipping principles.
        <span className="flex items-center gap-2">
          <WhyNoClipsLink videoId={video.id} />
          {styleLink}
        </span>
      </span>
    )
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <GenerateClipsButton
        videoId={video.id}
        onClips={(clips) => (clips.length > 0 ? onOpen() : setNoClips(true))}
      />
      <WhyNoClipsLink videoId={video.id} />
      {styleLink}
    </div>
  )
}

export function VideoPickerLanding({ tool }: { tool: 'review' | 'editor' }) {
  const [, setParams] = useSearchParams()
  const [uploadOpen, setUploadOpen] = useState(false)
  const copy = TOOL_COPY[tool]

  const videosQuery = useQuery({
    queryKey: ['videos'],
    queryFn: () => api<VideoListResponse>('/videos'),
    refetchInterval: (query) => videosRefetchInterval(query.state.data),
  })
  const videos = videosQuery.data?.videos ?? []

  const countsQuery = useQuery({
    queryKey: ['clip-counts'],
    queryFn: () => api<ClipCountsResponse>('/videos/clips/counts'),
    enabled: videos.length > 0,
  })
  const countsByVideoId: Record<string, { total: number; rendered: number }> = {}
  for (const row of countsQuery.data?.counts ?? []) {
    countsByVideoId[row.video_id] = { total: row.total, rendered: row.rendered }
  }

  function openVideo(id: string) {
    setParams({ video_id: id })
  }

  const isEmpty = !videosQuery.isPending && !videosQuery.isError && videos.length === 0
  const showUpload = uploadOpen || isEmpty

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-8">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-h2 text-fg">{copy.heading}</h1>
          <p className="mt-1 text-body text-muted">{copy.sub}</p>
        </div>
        <Button
          variant="secondary"
          aria-expanded={showUpload}
          onClick={() => setUploadOpen((o) => !o)}
        >
          {showUpload ? 'Close upload' : '+ Upload a video'}
        </Button>
      </div>

      {showUpload && <InlineUploadFlow onReady={openVideo} />}

      {videosQuery.isPending ? (
        <p className="py-8 text-center text-sm text-muted">Loading your videos…</p>
      ) : videosQuery.isError ? (
        <QueryErrorState
          title="Couldn’t load your videos."
          onRetry={() => void videosQuery.refetch()}
        />
      ) : isEmpty ? (
        // Issue 355: the old copy pointed "above" with nothing to click. The
        // upload form IS already open in this branch (showUpload), so the honest
        // move is to say so and offer the OTHER input path rather than a second
        // button that does the same thing.
        <EmptyStatePrompt
          variant="card"
          pose="confused"
          className="my-6"
          title={videosQuery.data?.message ?? 'No videos yet.'}
          detail="The upload form is open above — drop in a source file and we’ll transcribe it. Already have videos on YouTube? Browse them from your dashboard."
          actionLabel="Browse your channel"
          actionTo="/dashboard"
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr>
                {['Video', 'Status', 'Clips', 'Action'].map((h) => (
                  <th
                    key={h}
                    className="border-b border-default px-4 py-[11px] text-left text-xs font-medium uppercase tracking-[0.06em] text-subtle"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {videos.map((v) => {
                const counts = countsByVideoId[v.id]
                return (
                  <tr key={v.id} className="border-b border-default hover:bg-elevated">
                    <td className="px-4 py-3.5 align-middle">
                      <div className="max-w-[280px] truncate text-fg">{v.title || '—'}</div>
                      <div className="font-mono text-xs text-subtle">{v.kind}</div>
                    </td>
                    <td className="px-3 py-3.5 align-middle">
                      <Badge variant={STATUS_VARIANT[v.ingest_status]}>{v.ingest_status}</Badge>
                      {v.ingest_status === 'failed' && v.failure_reason && (
                        <span className="mt-1 block max-w-[260px] text-xs text-subtle">
                          {v.failure_reason}
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-3.5 align-middle">
                      {v.ingest_status === 'done' && counts ? (
                        <span className="font-mono text-sm text-fg">
                          {counts.rendered}/{counts.total}
                        </span>
                      ) : (
                        <span className="font-mono text-sm text-subtle">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3.5 align-middle">
                      <PickerRowAction
                        video={v}
                        counts={counts}
                        countsLoading={videos.length > 0 && countsQuery.isPending}
                        ctaLabel={copy.cta}
                        onOpen={() => openVideo(v.id)}
                        onOpenStyle={
                          tool === 'review'
                            ? () => setParams({ video_id: v.id, mode: 'style' })
                            : undefined
                        }
                      />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </main>
  )
}
