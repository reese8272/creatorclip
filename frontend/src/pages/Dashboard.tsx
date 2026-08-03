import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api'
import { useAuth } from '@/hooks/useAuth'
import { DisclaimerBand } from '@/components/DisclaimerBand'
import { EmptyStatePrompt } from '@/components/EmptyStatePrompt'
import { Button } from '@/components/ui/button'
import { buttonVariants } from '@/components/ui/buttonVariants'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { AnalyticsPanel } from '@/components/dashboard/AnalyticsPanel'
import { UploadVideoForm } from '@/components/dashboard/UploadVideoForm'
import { ChannelBrowser } from '@/components/dashboard/ChannelBrowser'
import { EmptyHero } from '@/components/dashboard/EmptyHero'
import { VideoTable, type ClipInfo } from '@/components/dashboard/VideoTable'
import { DnaCta, TrialBanner, LowBalanceWarning } from '@/components/dashboard/DashboardBanners'
import { QueryErrorState } from '@/components/QueryErrorState'
import { videosRefetchInterval } from '@/lib/videosPoll'
import type { ClipCountsResponse, DnaProfile, DnaResponse, VideoListResponse } from '@/types'
import { ArrowRight } from '@/components/ui/icon'
import { ICON_INLINE, ICON_SIZE } from '@/components/ui/iconSizes'

// Sidebar: clips waiting in the review queue (Issue 305).
//
// Issue 355 finding 4 named this card's zero state by hand: a big "0" over
// "clips ready to review" above a primary button into an empty queue. At zero
// we now show the way to fill it instead of the count of what isn't there.
function ReviewQueueCard({ count }: { count: number }) {
  return (
    <div className="rounded-md border border-accent-border bg-gradient-to-br from-accent-soft to-surface p-[18px] shadow-sm inset-shadow-highlight">
      <div className="text-label uppercase tracking-[0.08em] text-accent-text">Review queue</div>
      {count === 0 ? (
        <EmptyStatePrompt
          className="mt-2"
          title="Nothing waiting yet."
          detail="Generate clips from one of your videos and they’ll queue up here."
          actionLabel="Pick a video"
          actionTo="/review"
        />
      ) : (
        <>
          <div className="mb-1 mt-2 font-mono text-2xl font-semibold text-fg">{count}</div>
          <div className="mb-3.5 text-small text-muted">clips ready to review</div>
          <Link to="/review" className={cn(buttonVariants(), 'w-full')}>
            Open review <ArrowRight className={`${ICON_SIZE.md} ${ICON_INLINE}`} aria-hidden="true" />
          </Link>
        </>
      )}
    </div>
  )
}

// Sidebar: at-a-glance Creator DNA status (Issue 305).
function CreatorDnaCard({ dna }: { dna: DnaProfile | null }) {
  return (
    <div className="rounded-md border border-default bg-surface p-[18px] shadow-sm inset-shadow-highlight">
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
        View profile <ArrowRight className={`${ICON_SIZE.md} ${ICON_INLINE}`} aria-hidden="true" />
      </Link>
    </div>
  )
}

export function Dashboard() {
  const { user, balance } = useAuth()
  const [uploadOpen, setUploadOpen] = useState(false)
  const [browseOpen, setBrowseOpen] = useState(false)

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
  const channelName = user?.channel_title ?? user?.email ?? 'your channel'

  // Mirrors DnaCta's own render condition — while that banner is up it owns the
  // page's primary action, so Upload must not compete with it (Issue 355).
  const setupIncomplete: boolean =
    !!user?.setup && user.setup.step !== 'complete' && user.setup.step !== 'link_first_video'

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
            {/* Issue 355: the clip count was stated three times on this screen.
                The sidebar Review-queue card owns it; the header does not. */}
            <p className="mt-1 text-small text-muted">
              {videos.length} videos · {channelName}
            </p>
          </div>
          {/* ONE primary action (Issue 355 finding 3). Which one it is depends on
              where the creator actually is: until onboarding completes, DnaCta
              above owns the primary and Upload steps back to secondary, so the
              page never shows two equally-weighted "start here" buttons.
              "Analyze a video" left the header entirely — it is a per-video tool,
              reached from a video row (VideoTable "Titles") or the ask-surface row. */}
          <div className="flex flex-col items-start gap-1 sm:items-end">
            <Button
              variant={setupIncomplete ? 'secondary' : 'primary'}
              onClick={() => setUploadOpen((o) => !o)}
              aria-expanded={uploadOpen}
            >
              + Upload a video
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setBrowseOpen(true)}>
              or browse videos already on your channel
            </Button>
          </div>
        </div>

        <UploadVideoForm open={uploadOpen} />
        <ChannelBrowser open={browseOpen} onClose={() => setBrowseOpen(false)} />
        <LowBalanceWarning balance={balance} />

        {videosQuery.isPending ? (
          <p className="py-8 text-center text-sm text-subtle">Loading…</p>
        ) : videosQuery.isError ? (
          // A failed load must NOT fall through to the first-run empty hero —
          // a creator with videos would be told they have none (Recap idiom).
          <QueryErrorState
            title="Couldn't load your videos."
            onRetry={() => void videosQuery.refetch()}
          />
        ) : isEmpty ? (
          // Issue 355: the trailing "pick a path above to get started" line is
          // gone — EmptyHero already opens with "Let's get your first clip.",
          // and after the demotion below there is one path, not several.
          <EmptyHero onUploadClick={() => setUploadOpen(true)} />
        ) : (
          <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1fr)_296px]">
            {/* Main: videos table in a bordered card */}
            <div className="overflow-hidden rounded-md border border-default bg-surface shadow-sm inset-shadow-highlight">
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
