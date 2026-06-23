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
import { ChannelSnapshot, DnaSnapshot } from '@/components/insights/ChannelSnapshot'
import { PerformerPanel } from '@/components/insights/PerformerPanel'
import { UploadWindows } from '@/components/insights/UploadWindows'
import { ImprovementBrief } from '@/components/insights/ImprovementBrief'
import { SavedInsights } from '@/components/insights/SavedInsights'
import { InsightsFraming, WhatChanged } from '@/components/insights/InsightsNarrative'
import type { InsightsResponse, SavedInsightsResponse, UploadIntel } from '@/types'

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

  const data = insightsQuery.data

  return (
    <>
      <DisclaimerBand>
        AutoClip predicts fit with your style and audience — it does not promise virality.
        Recommendations are estimates grounded in your own data, not guarantees.
      </DisclaimerBand>

      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-8">
        {insightsQuery.isPending ? (
          <p className="text-sm text-muted">Loading your channel insights…</p>
        ) : insightsQuery.isError ? (
          <p className="text-sm text-danger">Could not load insights — try again.</p>
        ) : (
          <>
            {/* Page-level framing: what this is showing + why it matters */}
            {data && <InsightsFraming data={data} />}

            {/* Q1: What's working / what's not */}
            {data && <ChannelSnapshot totals={data.totals} />}
            {data && <DnaSnapshot dna={data.dna} />}
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
