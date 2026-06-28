import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { DisclaimerBand } from '@/components/DisclaimerBand'
import { Chip } from '@/components/Chip'
import { ChipPersonalizing } from '@/components/chip/ChipStates'
import { ClipPlayer } from '@/components/review/ClipPlayer'
import { WhyThisClip } from '@/components/review/WhyThisClip'
import { YourCall } from '@/components/review/YourCall'
import { CollapsibleTool } from '@/components/review/CollapsibleTool'
import { Button } from '@/components/ui/button'
import type { PersonalizationStatus, ReviewClip, ReviewClipListResponse } from '@/types'

// Issue 216: Honest personalization-status band — shown below the virality disclaimer.
// Below threshold: "Still learning" with N/threshold progress; above: "Personalized".
// No virality language; no weight float exposed to user.
//
// Issue 314: while still learning, the animated ChipPersonalizing (meditating Chip +
// floating binary digits, 150×200) is its real home — it signals the keep/drop
// ratings being learned. Once personalized, the band collapses back to the thin
// inline-chip strip (no large animation needed). prefers-reduced-motion collapses
// the animation to a single resting frame via the global rule in index.css.
function PersonalizationBand({ status }: { status: PersonalizationStatus }) {
  if (!status.active) {
    return (
      <div className="flex flex-col items-center gap-1 border-b border-default bg-surface px-4 py-3 text-center text-xs text-muted">
        <ChipPersonalizing />
        <span>
          Still learning — DNA-based ranking ({status.labels}/{status.threshold} ratings collected)
        </span>
      </div>
    )
  }
  return (
    <div className="flex items-center justify-center gap-2 border-b border-default bg-surface px-4 py-1.5 text-center text-xs text-muted">
      <Chip pose="meditate" size={22} />
      Personalized to your feedback ({status.labels} ratings collected)
    </div>
  )
}

// Per-clip subtree. Keyed by clip.id in the parent so trim state re-initialises
// from the new clip's duration on advance (no set-state-in-effect). Lifts the
// trim region here so the filmstrip (left) and "Save trim" (right) share it.
function ReviewClipView({
  clip,
  videoId,
  onAdvance,
}: {
  clip: ReviewClip
  videoId: string
  onAdvance: () => void
}) {
  const navigate = useNavigate()
  const clipDur = clip.end_s - clip.start_s
  const [trim, setTrim] = useState({ start: 0, end: clipDur })

  return (
    <main className="mx-auto grid w-full max-w-5xl flex-1 grid-cols-1 gap-6 px-4 py-8 lg:grid-cols-2">
      {/* Left: player + filmstrip trim + Next */}
      <ClipPlayer
        clip={clip}
        trimStart={trim.start}
        trimEnd={trim.end}
        onTrimChange={(start, end) => setTrim({ start, end })}
        onNext={onAdvance}
      />

      {/* Right: Why this clip · Your call · Open in the editor */}
      <div className="flex flex-col gap-4">
        <CollapsibleTool
          defaultOpen
          plain
          title={
            <span className="flex items-center gap-2">
              <Chip pose="think" size={24} />
              Why this clip
            </span>
          }
        >
          <WhyThisClip clip={clip} />
        </CollapsibleTool>

        <YourCall clip={clip} trimStart={trim.start} trimEnd={trim.end} onAdvance={onAdvance} />

        <div className="rounded-md border border-accent-border bg-gradient-to-br from-accent-soft to-surface p-[18px] shadow-sm shadow-inset">
          <div className="mb-1.5 flex items-center gap-2.5">
            <Chip pose="laptop" size={30} />
            <span className="text-h3 font-semibold text-fg">Open in the editor</span>
          </div>
          <p className="mb-3.5 text-small leading-relaxed text-muted">
            Fine-tune the full edit — caption style &amp; placement, word-by-word transcript cuts,
            filler &amp; silence removal, and pacing to match your style.
          </p>
          <Button onClick={() => navigate(`/editor?video_id=${videoId}&clip_id=${clip.id}`)}>
            Refine in editor →
          </Button>
        </div>
      </div>
    </main>
  )
}

// Port of static/review.html (Issue 85f), redesigned (Issue 306) to the player-first
// two-column layout: player + filmstrip trim on the left; Why-this-clip, the
// "Your call" triage card, and the editor entry point on the right.
export function Review() {
  const [params] = useSearchParams()
  const videoId = params.get('video_id')
  const navigate = useNavigate()
  const [index, setIndex] = useState(0)

  const { data, isPending } = useQuery({
    queryKey: ['review-clips', videoId],
    queryFn: () => api<ReviewClipListResponse>(`/videos/${videoId}/clips`),
    enabled: !!videoId,
    // Auto-render runs in the background after clip generation; poll while any
    // clip is still queued/rendering so the player swaps from "Rendering…" to the
    // playable video without a manual refresh. Stops polling once all are settled.
    refetchInterval: (query) => {
      const clips = query.state.data?.clips ?? []
      const inFlight = clips.some(
        (c) => c.render_status === 'pending' || c.render_status === 'running',
      )
      return inFlight ? 4000 : false
    },
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

      <ReviewClipView
        key={clip.id}
        clip={clip}
        videoId={videoId}
        onAdvance={() => setIndex((i) => i + 1)}
      />
    </>
  )
}
