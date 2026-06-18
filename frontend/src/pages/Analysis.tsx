import { useSearchParams } from 'react-router-dom'
import { DisclaimerBand } from '@/components/DisclaimerBand'
import { AnalysisQuery } from '@/components/analysis/AnalysisQuery'
import { TitleOptimizer } from '@/components/analysis/TitleOptimizer'
import { HookAnalyzer } from '@/components/analysis/HookAnalyzer'
import { ChaptersPanel } from '@/components/analysis/ChaptersPanel'
import { ThumbnailConcepts } from '@/components/analysis/ThumbnailConcepts'

// Port of static/analysis.html. The free-form "analyze a video" query is always
// available; the four per-video features (titles, hook, chapters, thumbnails)
// render only when arrived at with ?video_id= (from the dashboard's Titles
// link), exactly like the vanilla page's DOMContentLoaded gate.
export function Analysis() {
  const [params] = useSearchParams()
  const videoId = params.get('video_id')
  const videoTitle = params.get('video_title') ?? ''

  return (
    <>
      <DisclaimerBand>
        AutoClip predicts fit with your style and audience — it does not promise virality.
        Recommendations are estimates grounded in your own data, not guarantees.
      </DisclaimerBand>

      <main className="mx-auto w-full max-w-2xl flex-1 px-4 py-8">
        <h1 className="mb-2 text-xl font-semibold text-fg">Analyze a video</h1>
        <p className="mb-8 text-sm text-subtle">
          Ask why a video performed the way it did. Analysis is grounded in your channel data and
          Creator DNA.
        </p>

        <AnalysisQuery />

        {videoId && (
          <>
            <TitleOptimizer videoId={videoId} videoTitle={videoTitle} />
            <HookAnalyzer videoId={videoId} />
            <ChaptersPanel videoId={videoId} />
            <ThumbnailConcepts videoId={videoId} />
          </>
        )}
      </main>
    </>
  )
}
