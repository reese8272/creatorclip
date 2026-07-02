import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api'
import { sendActivity } from '@/lib/activity'
import { useTaskStream } from '@/hooks/useTaskStream'
import { TaskStepper } from '@/components/TaskStepper'
import { RecapPlayer } from '@/components/recap/RecapPlayer'
import { FitBadge } from '@/components/ui/fit-badge'
import { fitTier } from '@/lib/fit'
import { Button } from '@/components/ui/button'
import type { Summary, SummaryListResponse, SummaryQueued, SummarySegment } from '@/types'

// mm:ss for segment boundaries — recap sources are long VODs, so raw seconds
// (the clip pages' idiom) read poorly past the ten-minute mark.
function fmtTime(s: number): string {
  const total = Math.floor(s)
  const min = Math.floor(total / 60)
  const sec = total % 60
  return `${min}:${String(sec).padStart(2, '0')}`
}

// One chronological recap segment: time range, honest fit tier (never the raw
// score), the named principle it cites, and the engine's rationale.
function SegmentRow({ segment, index }: { segment: SummarySegment; index: number }) {
  return (
    <li className="rounded-md border border-default bg-surface p-3">
      <div className="mb-1 flex flex-wrap items-center gap-3">
        <span className="font-mono text-xs text-muted">
          {index + 1}. {fmtTime(segment.start_s)}–{fmtTime(segment.end_s)}
        </span>
        <FitBadge tier={fitTier(segment.score)} />
        {segment.principle && (
          <span className="font-mono text-xs text-subtle">{segment.principle}</span>
        )}
      </div>
      {segment.rationale && <p className="text-xs text-muted">{segment.rationale}</p>}
    </li>
  )
}

// Issue 192 — the recap front door: request a 16:9 recap of a long video,
// follow the render live over the task SSE (CaptionStylePanel idiom via
// useTaskStream + TaskStepper), then play it inline and show the selected
// segments in chronological order.
export function Recap() {
  const { videoId } = useParams<{ videoId: string }>()
  const queryClient = useQueryClient()
  const [streamUrl, setStreamUrl] = useState<string | null>(null)

  const summariesQuery = useQuery({
    queryKey: ['summaries', videoId],
    queryFn: () => api<SummaryListResponse>(`/videos/${videoId}/summaries`),
    enabled: Boolean(videoId),
  })

  const create = useMutation({
    mutationFn: () => api<SummaryQueued>(`/videos/${videoId}/summaries`, { method: 'POST' }),
    onSuccess: (queued) => {
      void queryClient.invalidateQueries({ queryKey: ['summaries', videoId] })
      // Redis-blip fail-open: stream_url can be null — the render is still
      // queued; the list poll/refresh picks up the result.
      if (queued.stream_url) setStreamUrl(queued.stream_url)
    },
  })

  const stream = useTaskStream(streamUrl)

  // Bridge SSE completion back into the query cache so the rendered recap
  // (render_uri) swaps in without a manual refresh.
  useEffect(() => {
    if (stream.status === 'done' || stream.status === 'error') {
      void queryClient.invalidateQueries({ queryKey: ['summaries', videoId] })
    }
  }, [stream.status, queryClient, videoId])

  // Elapsed tick for the TaskStepper (Onboarding idiom).
  const startRef = useRef<number | null>(null)
  const [elapsedMs, setElapsedMs] = useState(0)
  useEffect(() => {
    if (stream.status !== 'streaming') return
    if (!startRef.current) startRef.current = Date.now()
    const id = setInterval(() => {
      setElapsedMs(Date.now() - (startRef.current ?? Date.now()))
    }, 1000)
    return () => clearInterval(id)
  }, [stream.status])

  // Newest first from the server; the newest summary is THE recap.
  const latest: Summary | undefined = summariesQuery.data?.summaries[0]
  const segments = [...(latest?.segments ?? [])].sort((a, b) => a.start_s - b.start_s)
  const renderInFlight =
    latest != null && (latest.render_status === 'pending' || latest.render_status === 'running')
  const busy = create.isPending || renderInFlight || stream.status === 'streaming'

  function requestRecap() {
    sendActivity('click', 'recap_requested', { video_id: videoId })
    startRef.current = null
    setElapsedMs(0)
    setStreamUrl(null)
    create.mutate()
  }

  if (summariesQuery.isPending) {
    return (
      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-8">
        <p className="py-8 text-center text-sm text-subtle">Loading…</p>
      </main>
    )
  }

  return (
    <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-8">
      <div className="mb-6">
        <Link to={`/video/${videoId}`} className="text-xs text-accent-text hover:underline">
          ← Video timeline
        </Link>
        <h1 className="mt-2 text-lg font-medium text-fg">Recap</h1>
        <p className="text-xs text-subtle">
          A 16:9 highlight recap cut from this video&apos;s strongest scored moments, in
          chronological order. Segment picks are estimates grounded in your own channel data — not
          a guarantee of performance.
        </p>
      </div>

      {latest ? (
        <>
          <RecapPlayer summary={latest} />
          {segments.length > 0 && (
            <section aria-label="Recap segments" className="mt-6">
              <h2 className="mb-2 text-xs font-medium uppercase tracking-[0.06em] text-muted">
                Segments — in story order
              </h2>
              <ol className="flex flex-col gap-2">
                {segments.map((seg, i) => (
                  <SegmentRow key={`${seg.start_s}-${seg.end_s}`} segment={seg} index={i} />
                ))}
              </ol>
            </section>
          )}
        </>
      ) : (
        <div className="rounded-md border border-default bg-surface px-6 py-10 text-center">
          <p className="mb-4 text-sm text-muted">
            No recap yet. We&apos;ll stitch this video&apos;s strongest scored moments into one
            16:9 highlight cut.
          </p>
        </div>
      )}

      <div className="mt-6 flex flex-col gap-2">
        <Button className="w-fit" onClick={requestRecap} disabled={busy}>
          {create.isPending
            ? 'Requesting…'
            : busy
              ? 'Recap rendering…'
              : latest
                ? 'Create a new recap'
                : 'Create recap'}
        </Button>
        {create.isError && (
          <p className="text-xs text-danger">
            {create.error instanceof ApiError
              ? create.error.message
              : 'Could not request the recap — try again.'}
          </p>
        )}
        <TaskStepper steps={stream.steps} status={stream.status} elapsedMs={elapsedMs} />
        {stream.status === 'error' && stream.error && (
          <p className="text-xs text-danger">Render failed — {stream.error}</p>
        )}
      </div>
    </main>
  )
}
