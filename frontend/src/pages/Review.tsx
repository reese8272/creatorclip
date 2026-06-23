import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { DisclaimerBand } from '@/components/DisclaimerBand'
import { ClipPlayer } from '@/components/review/ClipPlayer'
import { WhyThisClip } from '@/components/review/WhyThisClip'
import { CollapsibleTool } from '@/components/review/CollapsibleTool'
import { Button } from '@/components/ui/button'
import type { PersonalizationStatus, ReviewClipListResponse } from '@/types'

// Issue 216: Honest personalization-status band — shown below the virality disclaimer.
// Below threshold: "Still learning" with N/threshold progress; above: "Personalized".
// No virality language; no weight float exposed to user.
function PersonalizationBand({ status }: { status: PersonalizationStatus }) {
  if (status.active) {
    return (
      <div className="border-b border-default bg-surface-subtle px-4 py-1.5 text-center text-xs text-muted">
        Personalized to your feedback ({status.labels} ratings collected)
      </div>
    )
  }
  return (
    <div className="border-b border-default bg-surface-subtle px-4 py-1.5 text-center text-xs text-muted">
      Still learning — DNA-based ranking ({status.labels}/{status.threshold} ratings collected)
    </div>
  )
}

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

  const personalization = data?.personalization ?? null

  return (
    <>
      <DisclaimerBand>
        AutoClip predicts fit with your style and audience — it does not promise virality. All scores
        are estimates grounded in your own channel data.
      </DisclaimerBand>
      {personalization && <PersonalizationBand status={personalization} />}

      <main className="mx-auto grid w-full max-w-5xl flex-1 grid-cols-1 gap-6 px-4 py-8 lg:grid-cols-2">
        {/* Left: the clip player + triage actions.
            Editorial tools (transcript / caption / clean) have moved to the
            Editor page (Issue 188) so triage and refinement are separate
            surfaces — the "editing-tools-beside-player conflation" bug
            (OFF_COURSE_BUGS) is resolved by giving editing its own page. */}
        <div className="flex flex-col gap-4">
          <ClipPlayer key={clip.id} clip={clip} onAdvance={() => setIndex((i) => i + 1)} />
        </div>

        {/* Right: Why this clip + Refine entry point. */}
        <div className="flex flex-col gap-4">
          <CollapsibleTool title="Why this clip" defaultOpen>
            <WhyThisClip clip={clip} />
          </CollapsibleTool>
          <div className="rounded-md border border-default bg-surface p-4">
            <p className="mb-3 text-xs text-muted">
              Want to cut words, adjust captions, or remove filler? Open the full editor.
            </p>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => navigate(`/editor?video_id=${videoId}&clip_id=${clip.id}`)}
            >
              Refine →
            </Button>
          </div>
        </div>
      </main>
    </>
  )
}
