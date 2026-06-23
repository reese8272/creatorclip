import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api'
import { useAuth } from '@/hooks/useAuth'
import { DisclaimerBand } from '@/components/DisclaimerBand'
import { Button } from '@/components/ui/button'
import { SummaryCards } from '@/components/dashboard/SummaryCards'
import { AnalyticsPanel } from '@/components/dashboard/AnalyticsPanel'
import { LinkVideoForm } from '@/components/dashboard/LinkVideoForm'
import { EmptyHero } from '@/components/dashboard/EmptyHero'
import { VideoTable, type ClipInfo } from '@/components/dashboard/VideoTable'
import { DnaCta, TrialBanner, LowBalanceWarning } from '@/components/dashboard/DashboardBanners'
import type {
  ClipCountsResponse,
  DnaResponse,
  VideoListResponse,
} from '@/types'

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

export function Dashboard() {
  const { user, balance } = useAuth()
  const [linkOpen, setLinkOpen] = useState(false)

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

  return (
    <>
      <DisclaimerBand>
        AutoClip predicts fit with your style and audience — it does not promise virality.
        Recommendations are estimates grounded in your own data, not guarantees.
      </DisclaimerBand>

      <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-8">
        <TrialBanner balance={balance} />
        <DnaCta setup={user?.setup} />

        <SummaryCards
          dna={dnaQuery.data?.profile ?? null}
          videoCount={videos.length}
          clipsRendered={clipsRendered}
        />

        <AnalyticsPanel />

        <div className="mt-8 flex items-center justify-between gap-4 rounded-md border border-default border-l-2 border-l-accent bg-surface px-5 py-4 shadow-sm shadow-inset">
          <div>
            <h3 className="mb-1 text-sm font-medium text-fg">Analyze a video</h3>
            <p className="text-xs text-subtle">
              Ask why any video performed the way it did — grounded in your channel data and Creator
              DNA.
            </p>
          </div>
          <Link to="/analysis">
            <Button>Analyze →</Button>
          </Link>
        </div>

        <div className="mt-6">
          <LinkVideoForm open={linkOpen} onToggle={setLinkOpen} />
        </div>

        {isEmpty && <EmptyHero onLinkClick={() => setLinkOpen(true)} />}

        <h2 className="mb-3 mt-10 text-md font-medium uppercase tracking-[0.06em] text-muted">
          Your videos
        </h2>
        <LowBalanceWarning balance={balance} />

        {videosQuery.isPending ? (
          <p className="py-8 text-center text-sm text-subtle">Loading…</p>
        ) : isEmpty ? (
          <p className="py-8 text-center text-sm text-subtle">{emptyMessage}</p>
        ) : (
          <VideoTable
            videos={videos}
            clipInfoByVideo={clipInfoByVideo}
            analysisMode={user?.analysis_mode ?? 'auto'}
          />
        )}
      </main>
    </>
  )
}
