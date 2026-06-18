import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { DisclaimerBand } from '@/components/DisclaimerBand'
import { ClipPlayer } from '@/components/review/ClipPlayer'
import { WhyThisClip } from '@/components/review/WhyThisClip'
import { CaptionStylePanel } from '@/components/review/CaptionStylePanel'
import { CleanPassPanel } from '@/components/review/CleanPassPanel'
import { TranscriptEditor } from '@/components/review/TranscriptEditor'
import { CollapsibleTool } from '@/components/review/CollapsibleTool'
import type { ReviewClipListResponse } from '@/types'

// Port of static/review.html (Issue 85f). Redesigned to a player-first layout:
// the clip player + review actions lead, the transcript editor sits alongside,
// and the secondary tools (why / captions / clean) are collapsible sections —
// replacing the vanilla icon-rail + slide-out drawer.
export function Review() {
  const [params] = useSearchParams()
  const videoId = params.get('video_id')
  const navigate = useNavigate()
  const [index, setIndex] = useState(0)

  const { data, isPending } = useQuery({
    queryKey: ['review-clips', videoId],
    queryFn: () => api<ReviewClipListResponse>(`/videos/${videoId}/clips`),
    enabled: !!videoId,
  })

  const clips = data?.clips ?? []
  const reviewed = clips.length > 0 && index >= clips.length
  const clip = clips[index]

  useEffect(() => {
    if (reviewed) {
      const t = setTimeout(() => navigate('/dashboard'), 2000)
      return () => clearTimeout(t)
    }
  }, [reviewed, navigate])

  function message(text: string) {
    return (
      <>
        <DisclaimerBand>
          AutoClip predicts fit with your style and audience — it does not promise virality. All
          scores are estimates grounded in your own channel data.
        </DisclaimerBand>
        <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-10">
          <p className="text-center text-sm text-muted">{text}</p>
        </main>
      </>
    )
  }

  if (!videoId) return message('No video selected — go to Dashboard to pick a video.')
  if (isPending) return message('Loading clip…')
  if (reviewed) return message('All clips reviewed! Great work. Taking you back to the dashboard…')
  if (!clip) return message('No clips yet — generate them from the Dashboard.')

  return (
    <>
      <DisclaimerBand>
        AutoClip predicts fit with your style and audience — it does not promise virality. All scores
        are estimates grounded in your own channel data.
      </DisclaimerBand>

      <main className="mx-auto grid w-full max-w-5xl flex-1 grid-cols-1 gap-6 px-4 py-8 lg:grid-cols-2">
        <div className="flex flex-col gap-4">
          <ClipPlayer key={clip.id} clip={clip} onAdvance={() => setIndex((i) => i + 1)} />
          <CollapsibleTool title="Why this clip" defaultOpen>
            <WhyThisClip clip={clip} />
          </CollapsibleTool>
          <CollapsibleTool title="Caption style">
            <CaptionStylePanel clip={clip} />
          </CollapsibleTool>
          <CollapsibleTool title="Clean filler + silence">
            <CleanPassPanel clip={clip} />
          </CollapsibleTool>
        </div>

        <div>
          <h2 className="mb-3 text-sm font-medium uppercase tracking-[0.06em] text-muted">
            Transcript
          </h2>
          <TranscriptEditor key={clip.id} clip={clip} />
        </div>
      </main>
    </>
  )
}
