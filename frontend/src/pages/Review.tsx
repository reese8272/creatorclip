import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { ToolMain, ToolShell } from '@/components/layout/ToolShell'
import { Chip } from '@/components/Chip'
import { ChipPersonalizing } from '@/components/chip/ChipStates'
import { ClipPlayer } from '@/components/review/ClipPlayer'
import { PublishPanel } from '@/components/review/PublishPanel'
import { WhyThisClip } from '@/components/review/WhyThisClip'
import { YourCall } from '@/components/review/YourCall'
import { CollapsibleTool } from '@/components/review/CollapsibleTool'
import { QueryErrorState } from '@/components/QueryErrorState'
import { EmptyStatePrompt } from '@/components/EmptyStatePrompt'
import { StyleReview } from '@/components/review/StyleReview'
import { GenerateClipsButton, VideoPickerLanding } from '@/components/landing/VideoPickerLanding'
import { Button } from '@/components/ui/button'
import type { PersonalizationStatus, ReviewClip, ReviewClipListResponse } from '@/types'
import { ArrowLeft, ArrowRight } from '@/components/ui/icon'
import { ICON_INLINE, ICON_SIZE } from '@/components/ui/iconSizes'
import { Card } from '@/components/ui/card'

// Issue 216: Honest personalization-status band — shown below the virality disclaimer.
// Below threshold: "Still learning" with N/threshold progress; above: "Personalized".
// No virality language; no weight float exposed to user.
//
// Issue 314: while still learning, the animated ChipPersonalizing (meditating Chip +
// floating binary digits, 150×200) is its real home — it signals the keep/drop
// ratings being learned. Once personalized, the band collapses back to the thin
// inline-chip strip (no large animation needed). prefers-reduced-motion collapses
// the animation to a single resting frame via the global rule in index.css.
// Issue 389 made this a compact toolbar item rather than a full-width band: three
// stacked bands were spending ~110px of a fixed-height workspace on status. The
// whole sentence stays inside ONE element on purpose — Review.test.tsx scopes its
// no-virality check to `band.textContent`, so splitting the copy across siblings
// would both break the query and widen what that check actually covers.
function PersonalizationBand({ status }: { status: PersonalizationStatus }) {
  return (
    <span className="flex items-center gap-2 text-xs text-muted">
      <Chip pose="meditate" size={22} />
      <span>
        {status.active
          ? `Personalized to your feedback (${status.labels} ratings collected)`
          : `Still learning — DNA-based ranking (${status.labels}/${status.threshold} ratings collected)`}
      </span>
    </span>
  )
}

// The animated ChipPersonalizing (150×200, Issue 314) signals the keep/drop
// ratings being learned. It cannot live in a 32px toolbar row, so below threshold
// it moves to the top of the actions column where there is room for it.
function PersonalizationCard({ status }: { status: PersonalizationStatus }) {
  if (status.active) return null
  return (
    <div className="flex flex-col items-center gap-1 rounded-md border border-default bg-surface px-4 py-3 text-center text-xs text-muted">
      <ChipPersonalizing />
      <span>Learning your taste from every keep and drop.</span>
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
  personalization,
}: {
  clip: ReviewClip
  videoId: string
  onAdvance: () => void
  personalization: PersonalizationStatus | null
}) {
  const navigate = useNavigate()
  // Duration of the RENDERED mp4 — the render origin is setup_start_s when
  // set (the engine starts the clip at the setup), so end_s - start_s would
  // overstate the filmstrip/trim range for setup clips.
  const clipDur = clip.end_s - (clip.setup_start_s ?? clip.start_s)
  const [trim, setTrim] = useState({ start: 0, end: clipDur })

  // Three regions at lg (Issue 389): the argued case · the media · the decision.
  // Promoting "Why this clip" out of the rail into its own column is what removes
  // the 1440px dead space without inventing a feature — it is the most-read
  // content on the page and it was sharing a rail with three other cards.
  // grid-rows-[minmax(0,1fr)] is the grid analogue of min-h-0.
  return (
    <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 lg:grid-cols-[minmax(280px,1.1fr)_auto_minmax(300px,26rem)] lg:grid-rows-[minmax(0,1fr)]">
      {/* ── A · The case for this clip ── */}
      <section
        data-tool-scroll
        aria-label="Why this clip"
        className="min-h-0 lg:overflow-y-auto"
      >
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

        {/* Scoring CONTEXT, not a decision — so it lives under the case, not in
            the actions rail. Moving it here also fills the void below the case
            card that left column A ~60% empty canvas at 1080p (Issue 412). */}
        {personalization && <PersonalizationCard status={personalization} />}
      </section>

      {/* ── B · The media — the ONE primary panel (L2), on an `auto` track ──
          w-fit: the clip is 9:16, so a stretched card would frame a large empty
          region beside the player — dominance should come from weight, not from
          enclosing dead space. */}
      <section aria-label="Clip preview" className="flex min-h-0 justify-center lg:overflow-y-auto">
        <Card level="primary" className="h-fit w-fit p-4">
          <ClipPlayer
            clip={clip}
            trimStart={trim.start}
            trimEnd={trim.end}
            onTrimChange={(start, end) => setTrim({ start, end })}
            onNext={onAdvance}
          />
        </Card>
      </section>

      {/* ── C · Your call ── */}
      <section
        data-tool-scroll
        aria-label="Clip actions"
        className="flex min-h-0 flex-col gap-4 lg:overflow-y-auto"
      >
        <YourCall clip={clip} trimStart={trim.start} trimEnd={trim.end} onAdvance={onAdvance} />

        {/* Collapsed by default. A rail of four open, equally-weighted cards is
            the composition problem; the strongest lever for making a secondary
            panel recede is not a class, it is not being open. */}
        <CollapsibleTool title="Publish">
          <PublishPanel clip={clip} />
        </CollapsibleTool>

        {/* Demoted from a gradient card that competed with the player for
            attention. It is a quiet next step, not a second destination. */}
        <Card className="p-4">
          <div className="mb-1.5 flex items-center gap-2.5">
            <Chip pose="laptop" size={24} />
            <span className="text-body font-semibold text-fg">Open in the editor</span>
          </div>
          <p className="mb-3 text-small leading-relaxed text-muted">
            Fine-tune the full edit — caption style &amp; placement, word-by-word transcript cuts,
            filler &amp; silence removal, and pacing to match your style.
          </p>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => navigate(`/editor?video_id=${videoId}&clip_id=${clip.id}`)}
          >
            Refine in editor <ArrowRight className={`${ICON_SIZE.md} ${ICON_INLINE}`} aria-hidden="true" />
          </Button>
        </Card>
      </section>
    </div>
  )
}

// Port of static/review.html (Issue 85f), redesigned (Issue 306) to a player-first
// layout and again (Issue 389) into the full-height tool shell: the argued case,
// the media, and the decision as three independently-scrolling regions under one
// compact toolbar strip.
export function Review() {
  const [params, setParams] = useSearchParams()
  const videoId = params.get('video_id')
  // Issue 370: ?mode=style opens the video-level style review instead of the
  // clip queue — the standalone "explain why this style works" surface.
  const styleMode = params.get('mode') === 'style'
  const navigate = useNavigate()
  const [index, setIndex] = useState(0)
  // Issue 377 — shortlist mode: default the queue to the engine's argued top
  // picks (WhyThisClip primary content is unchanged — it was already
  // default-open); the full candidate set is one click away via "show all
  // candidates" so an engine miss is never hidden, only de-prioritized.
  const [showAllCandidates, setShowAllCandidates] = useState(false)

  const { data, isPending, isError, refetch } = useQuery({
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

  const allClips = data?.clips ?? []
  const shortlistedClips = allClips.filter((c) => c.shortlisted)
  // Fall back to the full set when nothing was shortlisted (e.g. a video with
  // only a creator-made selection, which is never engine-scored/shortlisted —
  // Issue 373) so the queue is never emptier than what actually exists.
  const hasHiddenCandidates = shortlistedClips.length > 0 && shortlistedClips.length < allClips.length
  const clips = showAllCandidates || shortlistedClips.length === 0 ? allClips : shortlistedClips
  const reviewed = clips.length > 0 && index >= clips.length
  const clip = clips[index]

  function toggleShowAllCandidates() {
    setShowAllCandidates((v) => !v)
    setIndex(0)
  }

  useEffect(() => {
    if (reviewed) {
      const t = setTimeout(() => navigate('/dashboard'), 2000)
      return () => clearTimeout(t)
    }
  }, [reviewed, navigate])

  // Every branch renders inside ToolShell, so the honesty statement and the legal
  // links are structurally impossible to omit — including the loading and error
  // states, which each carried their own copy of the band before Issue 389.
  function message(text: string) {
    return (
      <ToolShell>
        <ToolMain className="text-center">
          <p className="text-sm text-muted">{text}</p>
        </ToolMain>
      </ToolShell>
    )
  }

  // Standalone landing: no video selected → inline picker + upload-in-place
  // instead of a dead-end pointer at the Dashboard.
  if (!videoId)
    return (
      <ToolShell>
        <VideoPickerLanding tool="review" />
      </ToolShell>
    )
  if (styleMode)
    return (
      <ToolShell>
        <div className="mx-auto w-full max-w-5xl px-4 pt-4">
          <button
            onClick={() => setParams({ video_id: videoId })}
            className="text-xs text-muted hover:text-fg"
          >
            <ArrowLeft className={`${ICON_SIZE.md} ${ICON_INLINE}`} aria-hidden="true" /> Back to clip review
          </button>
        </div>
        <StyleReview videoId={videoId} />
      </ToolShell>
    )
  if (isPending) return message('Loading clip…')
  // A failed load must NOT fall through to "No clips yet" — a creator whose
  // clips exist would be told to regenerate them (Recap retry idiom).
  if (isError)
    return (
      <ToolShell>
        <ToolMain>
          <QueryErrorState
            title="Couldn’t load clips for this video."
            onRetry={() => void refetch()}
          />
        </ToolMain>
      </ToolShell>
    )
  if (reviewed) return message('All clips reviewed! Great work. Taking you back to the dashboard…')
  if (!clip)
    return (
      <ToolShell>
        <ToolMain className="flex flex-col items-center gap-3">
          {/* Issue 355: this used to point at the Dashboard in prose with nothing
              to click. Generating is the actual next step, so it happens here. */}
          <EmptyStatePrompt
            variant="card"
            pose="confused"
            title="No clips yet for this video."
            detail="Generate them here — we’ll rank them against your channel’s DNA and argue the case for each."
            action={<GenerateClipsButton videoId={videoId} onClips={() => void refetch()} />}
          />
          {/* Issue 370: a 0-clip video is still reviewable as a whole. */}
          <Button
            variant="secondary"
            onClick={() => setParams({ video_id: videoId, mode: 'style' })}
          >
            Review the style instead <ArrowRight className={`${ICON_SIZE.md} ${ICON_INLINE}`} aria-hidden="true" />
          </Button>
        </ToolMain>
      </ToolShell>
    )

  const personalization = data?.personalization ?? null

  return (
    // scroll={false}: <main> clips and the three regions scroll independently, so
    // the player never leaves the viewport while you read the case for the clip.
    <ToolShell scroll={false} className="flex flex-col gap-3 px-4 py-3 lg:px-6">
      {/* One toolbar strip where there were three stacked full-width bands
          (personalization, style link, shortlist) — ~110px of a fixed-height
          workspace reclaimed for the work itself. */}
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-x-4 gap-y-2 text-xs text-muted">
        {personalization ? <PersonalizationBand status={personalization} /> : <span />}

        {/* Issue 377 — shortlist mode: default queue is the engine's argued top
            picks; "show all candidates" is the load-bearing escape hatch so an
            engine miss can never be silently hidden. */}
        {shortlistedClips.length > 0 && (
          <div className="flex items-center gap-3" data-testid="shortlist-banner">
            <span>
              {showAllCandidates
                ? `Showing all ${allClips.length} candidates`
                : `Top ${shortlistedClips.length} picks — the case for each, ranked`}
            </span>
            {(hasHiddenCandidates || showAllCandidates) && (
              <button onClick={toggleShowAllCandidates} className="text-accent-text hover:underline">
                {showAllCandidates
                  ? `Show top picks only (${shortlistedClips.length})`
                  : `Show all ${allClips.length} candidates`}
              </button>
            )}
          </div>
        )}

        {/* Issue 370: the whole video's style is reviewable, not just its clips. */}
        <button
          onClick={() => setParams({ video_id: videoId, mode: 'style' })}
          className="hover:text-accent-text"
        >
          Review this video’s overall style <ArrowRight className={`${ICON_SIZE.md} ${ICON_INLINE}`} aria-hidden="true" />
        </button>
      </div>

      <ReviewClipView
        key={clip.id}
        clip={clip}
        videoId={videoId}
        personalization={personalization}
        onAdvance={() => setIndex((i) => i + 1)}
      />
    </ToolShell>
  )
}
