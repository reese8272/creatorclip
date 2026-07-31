// Insights page rebuild (Issue 212 — carry-over of Issue 93).
//
// The page is structured around three user questions:
//   Q1: What's working / what's not — grounded in specific video rows (PerformerPanel)
//   Q2: What changed since last week — 7d vs 28d-average diff (WhatChanged)
//   Q3: What to try next — structured brief with named-principle citations (ImprovementBrief)
//
// The "what this is showing + why it matters" framing (InsightsFraming) sits at the top
// so a first-time user immediately understands what each section is and how it's grounded
// in their own channel data — not generic advice.
//
// Information architecture boundary with Issue 213:
//   Insights = channel-level synthesis (what's working across videos)
//   VideoMap (/app/video/:id) = per-video clip timeline
// The PerformerPanel rows deep-link to /app/video/:id but do NOT duplicate the timeline here.

import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { DisclaimerBand } from '@/components/DisclaimerBand'
import { QueryErrorState } from '@/components/QueryErrorState'
import { Chip } from '@/components/Chip'
import { ChannelSnapshot } from '@/components/insights/ChannelSnapshot'
import { ChannelFingerprint } from '@/components/insights/ChannelFingerprint'
import { PerformerPanel } from '@/components/insights/PerformerPanel'
import { UploadWindows } from '@/components/insights/UploadWindows'
import { ImprovementBrief } from '@/components/insights/ImprovementBrief'
import { SavedInsights } from '@/components/insights/SavedInsights'
import { InsightsFraming, WhatChanged } from '@/components/insights/InsightsNarrative'
import { ProofOfLift } from '@/components/insights/ProofOfLift'
import { OriginalityGuard } from '@/components/insights/OriginalityGuard'
import type {
  InsightsResponse,
  OriginalityAdvisory,
  ProofOfLift as ProofOfLiftData,
  SavedInsightsResponse,
  UploadIntel,
} from '@/types'

export function Insights() {
  const insightsQuery = useQuery({
    queryKey: ['insights'],
    queryFn: () => api<InsightsResponse>('/creators/me/insights'),
  })
  const uploadQuery = useQuery({
    queryKey: ['upload-intel'],
    queryFn: () => api<UploadIntel>('/creators/me/upload-intel'),
  })
  const savedQuery = useQuery({
    queryKey: ['saved-insights'],
    queryFn: () => api<SavedInsightsResponse>('/creators/me/insights/saved'),
  })
  const liftQuery = useQuery({
    queryKey: ['proof-of-lift'],
    queryFn: () => api<ProofOfLiftData>('/creators/me/insights/lift'),
  })
  const originalityQuery = useQuery({
    queryKey: ['originality-guard'],
    queryFn: () => api<OriginalityAdvisory>('/creators/me/insights/originality'),
  })

  const data = insightsQuery.data

  return (
    <>
      <DisclaimerBand>
        AutoClip predicts fit with your style and audience — it does not promise virality.
        Recommendations are estimates grounded in your own data, not guarantees.
      </DisclaimerBand>

      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-8">
        <div className="mb-6 flex items-center gap-3.5">
          <Chip pose="idea" size={48} />
          <div>
            <h1 className="font-display text-h1 text-fg">Insights</h1>
            <p className="mt-1 text-small text-muted">
              Your channel read back to you — what’s working, what changed, and what to try next. All
              grounded in your own analytics.
            </p>
          </div>
        </div>

        {insightsQuery.isPending ? (
          <p className="text-sm text-muted">Loading your channel insights…</p>
        ) : insightsQuery.isError ? (
          <QueryErrorState
            title="Could not load insights."
            onRetry={() => void insightsQuery.refetch()}
          />
        ) : (
          <>
            {/* Page-level framing: what this is showing + why it matters */}
            {data && <InsightsFraming data={data} />}

            {/* What actually happened to what you published (Issue 374) —
                the outcome loop, ahead of the channel snapshot because it is
                the only falsifiable claim on this page. */}
            <ProofOfLift lift={liftQuery.data} isError={liftQuery.isError} />

            {/* Sameness advisory over the creator's own recent clips (Issue 375) —
                advisory only, never a certification of monetization eligibility. */}
            <OriginalityGuard advisory={originalityQuery.data} isError={originalityQuery.isError} />

            {/* Q1: What's working / what's not */}
            {data && <ChannelSnapshot totals={data.totals} />}
            {data && <ChannelFingerprint dna={data.dna} />}
            <PerformerPanel
              kind="top"
              title="Top performers"
              sub="These drove your DNA. Each row shows why, grounded in your own metrics."
              performers={data?.top_performers ?? []}
            />
            <PerformerPanel
              kind="bottom"
              title="Underperformers"
              sub="Contrast set — patterns that didn't resonate with your audience."
              performers={data?.bottom_performers ?? []}
            />

            {/* Q2: What changed since last week */}
            <WhatChanged />

            {/* Q3: What to try next */}
            <UploadWindows intel={uploadQuery.data} isError={uploadQuery.isError} />
            <ImprovementBrief />
            <SavedInsights
              insights={savedQuery.data?.insights ?? []}
              isError={savedQuery.isError}
            />
          </>
        )}
      </main>
    </>
  )
}
