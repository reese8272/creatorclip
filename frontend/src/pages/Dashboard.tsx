import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api'
import { useAuth } from '@/hooks/useAuth'
import { DisclaimerBand } from '@/components/DisclaimerBand'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { AnalyticsPanel } from '@/components/dashboard/AnalyticsPanel'
import { UploadVideoForm } from '@/components/dashboard/UploadVideoForm'
import { EmptyHero } from '@/components/dashboard/EmptyHero'
import { VideoTable, type ClipInfo } from '@/components/dashboard/VideoTable'
import { DnaCta, TrialBanner, LowBalanceWarning } from '@/components/dashboard/DashboardBanners'
import type { ClipCountsResponse, DnaProfile, DnaResponse, VideoListResponse } from '@/types'

// Poll while any clip-trackable video is mid-pipeline; stop once everything has
// settled. TanStack Query owns the lifecycle (replaces the hand-rolled backoff
// timer in static/index.html). Non-clippable linked rows (Issue 139) have no
// running pipeline, so they never keep the poll alive.
const POLL_MS = 5000
function videosRefetchInterval(data: VideoListResponse | undefined): number | false {
  const inFlight = (data?.videos ?? []).some(
    (v) => v.clippable && (v.ingest_status === 'pending' || v.ingest_status === 'running'),
  )
  return inFlight ? POLL_MS : false
}

// Sidebar: clips waiting in the review queue (Issue 305).
function ReviewQueueCard({ count }: { count: number }) {
  return (
    <div className="rounded-md border border-accent-border bg-gradient-to-br from-accent-soft to-surface p-[18px] shadow-sm shadow-inset">
      <div className="text-label uppercase tracking-[0.08em] text-accent-text">Review queue</div>
      <div className="mb-1 mt-2 font-mono text-2xl font-semibold text-fg">{count}</div>
      <div className="mb-3.5 text-small text-muted">clips ready to review</div>
      <Link to="/review">
        <Button className="w-full">Open review →</Button>
      </Link>
    </div>
  )
}

// Sidebar: at-a-glance Creator DNA status (Issue 305).
function CreatorDnaCard({ dna }: { dna: DnaProfile | null }) {
  return (
    <div className="rounded-md border border-default bg-surface p-[18px] shadow-sm shadow-inset">
      <div className="mb-2.5 text-label uppercase tracking-[0.08em] text-muted">Creator DNA</div>
      <div className="flex items-center gap-2.5">
        {dna ? (
          <>
            <Badge variant="accent">{dna.status}</Badge>
            <span className="font-mono text-xs text-subtle">v{dna.version}</span>
          </>
        ) : (
          <span className="text-small text-muted">Not built yet</span>
        )}
      </div>
      <Link
        to="/profile"
        className="mt-3 inline-block text-small text-accent-text hover:underline"
      >
        View profile →
      </Link>
    </div>
  )
}

export function Dashboard() {
  const { user, balance } = useAuth()
  const [uploadOpen, setUploadOpen] = useState(false)

  const videosQuery = useQuery({
    queryKey: ['videos'],
    queryFn: () => api<VideoListResponse>('/videos'),
    refetchInterval: (query) => videosRefetchInterval(query.state.data),
  })
  const dnaQuery = useQuery({
    queryKey: ['dna'],
    queryFn: () =>
      api<DnaResponse>('/creators/me/dna').catch((e) => {
        if (e instanceof ApiError && e.status === 404) return { profile: null }
        throw e
      }),
  })

  const videos = videosQuery.data?.videos ?? []

  // Single batched query for all clip counts (Issue 213 — replaces N+1 useQueries, OCB-2).
  const clipCountsQuery = useQuery({
    queryKey: ['clip-counts'],
    queryFn: () => api<ClipCountsResponse>('/videos/clips/counts'),
    enabled: videos.length > 0,
  })

  const clipInfoByVideo: Record<string, ClipInfo> = {}
  let clipsRendered = 0
  const countsLoading = clipCountsQuery.isPending
  const countsByVideoId: Record<string, { total: number; rendered: number }> = {}
  for (const row of clipCountsQuery.data?.counts ?? []) {
    countsByVideoId[row.video_id] = { total: row.total, rendered: row.rendered }
  }
  for (const v of videos.filter((v) => v.ingest_status === 'done')) {
    const counts = countsByVideoId[v.id]
    clipInfoByVideo[v.id] = {
      total: counts?.total ?? 0,
      rendered: counts?.rendered ?? 0,
      loading: countsLoading,
    }
    clipsRendered += counts?.rendered ?? 0
  }

  const isEmpty = !videosQuery.isPending && videos.length === 0
  const emptyMessage =
    videosQuery.data?.message ?? 'No videos yet — pick a path above to get started.'
  const channelName = user?.channel_title ?? user?.email ?? 'your channel'

  return (
    <>
      <DisclaimerBand>
        AutoClip predicts fit with your style and audience — it does not promise virality.
        Recommendations are estimates grounded in your own data, not guarantees.
      </DisclaimerBand>

      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-8">
        <TrialBanner balance={balance} />
        <DnaCta setup={user?.setup} />

        {/* Header row: videos-first (Issue 305) */}
        <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="font-display text-h1 text-fg">Your videos</h1>
            <p className="mt-1 text-small text-muted">
              {videos.length} videos · {clipsRendered} clips rendered · {channelName}
            </p>
          </div>
          <div className="flex gap-2.5">
            <Button onClick={() => setUploadOpen((o) => !o)} aria-expanded={uploadOpen}>
              + Upload a video
            </Button>
            <Link to="/analysis">
              <Button variant="secondary">Analyze a video</Button>
            </Link>
          </div>
        </div>

        <UploadVideoForm open={uploadOpen} />
        <LowBalanceWarning balance={balance} />

        {videosQuery.isPending ? (
          <p className="py-8 text-center text-sm text-subtle">Loading…</p>
        ) : isEmpty ? (
          <>
            <EmptyHero onUploadClick={() => setUploadOpen(true)} />
            <p className="py-2 text-center text-sm text-subtle">{emptyMessage}</p>
          </>
        ) : (
          <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1fr)_296px]">
            {/* Main: videos table in a bordered card */}
            <div className="overflow-hidden rounded-md border border-default bg-surface shadow-sm shadow-inset">
              <VideoTable
                videos={videos}
                clipInfoByVideo={clipInfoByVideo}
                analysisMode={user?.analysis_mode ?? 'auto'}
              />
            </div>

            {/* Sidebar */}
            <div className="flex flex-col gap-4">
              <ReviewQueueCard count={clipsRendered} />
              <AnalyticsPanel variant="sidebar" />
              <CreatorDnaCard dna={dnaQuery.data?.profile ?? null} />
            </div>
          </div>
        )}
      </main>
    </>
  )
}
