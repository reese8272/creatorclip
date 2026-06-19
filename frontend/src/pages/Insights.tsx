import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { DisclaimerBand } from '@/components/DisclaimerBand'
import { ChannelSnapshot, DnaSnapshot } from '@/components/insights/ChannelSnapshot'
import { PerformerPanel } from '@/components/insights/PerformerPanel'
import { UploadWindows } from '@/components/insights/UploadWindows'
import { ImprovementBrief } from '@/components/insights/ImprovementBrief'
import { SavedInsights } from '@/components/insights/SavedInsights'
import type { InsightsResponse, SavedInsightsResponse, UploadIntel } from '@/types'

// Port of static/insights.html: channel totals, DNA snapshot, top/bottom
// performers (sortable + AI analyze/save), upload windows, improvement brief,
// saved insights.
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
            {data && <ChannelSnapshot totals={data.totals} />}
            {data && <DnaSnapshot dna={data.dna} />}
            <PerformerPanel
              kind="top"
              title="Top performers"
              sub="Drove your DNA. Lean into what worked."
              performers={data?.top_performers ?? []}
            />
            <PerformerPanel
              kind="bottom"
              title="Underperformers"
              sub="Useful contrast — patterns you've moved past."
              performers={data?.bottom_performers ?? []}
            />
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
