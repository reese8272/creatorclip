import { useState, type ReactNode, type Ref } from 'react'
import { cn } from '@/lib/utils'
import { STAGE_MEDIA_W } from '@/lib/toolLayout'
import { fitTier } from '@/lib/fit'
import { FitBadge } from '@/components/ui/fit-badge'
import { Button } from '@/components/ui/button'
import { StagePlaceholder } from '@/components/stage/StagePlaceholder'
import { useClipRender } from '@/hooks/useClipRender'
import type { ReviewClip } from '@/types'
import { VideoPlayer, type VideoPlayerHandle } from '@/components/ui/video-player'
import { Card } from '@/components/ui/card'

// ShortStage — the one primary panel of the Review and Editor pages (L26,
// Issue 424). The stage owns everything touching the 9:16 frame: the player or
// the unified StagePlaceholder ladder, the compact meta row, the cleaned-
// preview tab swap, and the `overlay`/`below` slots. Pages own everything that
// argues about or acts on the clip (case rail, triage actions, transcript,
// options, timeline dock).
//
// Load-bearing contracts preserved here:
// - `mediaKey` remount: a confirmed trim/clean swap changes render_uri but not
//   the download src, so only a key change forces the element to load the new
//   media.
// - muted-autoplay pairing lives in VideoPlayer (`autoPlay` implies muted).
// - placeholder width = player width (both take STAGE_MEDIA_W), so the two
//   states never jump when a render lands.
// - The Card here is the ONE `level="primary"` per page — pages must not add
//   another.
export interface ShortStageProps {
  clip: ReviewClip
  autoPlay?: boolean
  /** Binds the editing transport keys. At most ONE player per page. */
  transport?: boolean
  density?: 'full' | 'compact'
  onTimeChange?: (t: number) => void
  playerRef?: Ref<VideoPlayerHandle>
  /** Readiness signal for the pending cleaned/trimmed render (Editor). */
  cleanedUri?: string | null
  onConfirmCleaned?: () => void
  onDiscardCleaned?: () => void
  statusLine?: ReactNode
  /** Seam over the 9:16 frame — crop mini-map (#426), captions (#394), reframe drag (#396). */
  overlay?: ReactNode
  /** Below the meta row, inside the card — Review's trim filmstrip. */
  below?: ReactNode
  className?: string
}

export function ShortStage({
  clip,
  autoPlay,
  transport,
  density = 'full',
  onTimeChange,
  playerRef,
  cleanedUri,
  onConfirmCleaned,
  onDiscardCleaned,
  statusLine,
  overlay,
  below,
  className,
}: ShortStageProps) {
  const render = useClipRender(clip)
  // Cleaned-preview tab swap: ONE player, two sources — replaces the second
  // stacked player. When a cleaned render lands the stage swaps to it (the
  // creator just asked for it); `chosen` records an explicit tab click. When
  // cleanedUri clears (confirm/discard), the derived value falls back to
  // `current` without an effect.
  const [chosen, setChosen] = useState<'current' | 'cleaned' | null>(null)
  const showCleaned = Boolean(cleanedUri) && (chosen ?? 'cleaned') === 'cleaned'

  // Clip-relative timebase: the setup origin, matching what the backend
  // validates and cuts against (end_s - (setup_start_s ?? start_s)).
  const clipDur = clip.end_s - (clip.setup_start_s ?? clip.start_s)
  // Clips stream through the authed download endpoint (presigned R2 in prod,
  // file stream in dev). `inline` backs the <video>.
  const mediaSrc = `/clips/${clip.id}/download?disposition=inline`
  const cleanedSrc = `/clips/${clip.id}/download?variant=cleaned&disposition=inline`

  const TAB_BASE =
    'rounded-sm px-2 py-1 text-xs font-medium transition-colors duration-fast ease-standard'
  const TAB_ACTIVE = 'bg-accent-soft text-accent-text'
  const TAB_IDLE = 'bg-transparent text-muted hover:text-fg'

  return (
    <Card level="primary" className={cn('flex h-fit w-fit flex-col gap-3 p-4', className)}>
      {clip.render_uri ? (
        <div className="relative">
          <VideoPlayer
            ref={playerRef}
            // Keyed on the artifact, not just the clip: a confirmed trim/clean
            // swap changes render_uri but not the download src, so without a
            // key change the element would keep playing the old media.
            mediaKey={showCleaned ? `cleaned:${cleanedUri}` : (clip.render_uri ?? undefined)}
            src={showCleaned ? cleanedSrc : mediaSrc}
            label={showCleaned ? 'Edited clip preview' : `Clip ${clip.rank ?? ''} preview`}
            autoPlay={autoPlay}
            transport={transport}
            density={density}
            onTimeChange={onTimeChange}
            className={STAGE_MEDIA_W}
          />
          {overlay}
        </div>
      ) : (
        <StagePlaceholder clip={clip} render={render} widthClass={STAGE_MEDIA_W} />
      )}

      {/* In-stage tab swap for the pending cleaned/trimmed render. */}
      {cleanedUri && (
        <div className="flex flex-wrap items-center gap-2">
          <div
            role="tablist"
            aria-label="Preview version"
            className="inline-flex gap-0.5 rounded-md border border-strong bg-bg p-[3px]"
          >
            <button
              role="tab"
              aria-selected={!showCleaned}
              onClick={() => setChosen('current')}
              className={cn(TAB_BASE, !showCleaned ? TAB_ACTIVE : TAB_IDLE)}
            >
              Current
            </button>
            <button
              role="tab"
              aria-selected={showCleaned}
              onClick={() => setChosen('cleaned')}
              className={cn(TAB_BASE, showCleaned ? TAB_ACTIVE : TAB_IDLE)}
            >
              Edited preview
            </button>
          </div>
          {showCleaned && (
            <div className="flex gap-2">
              {onConfirmCleaned && (
                <Button size="sm" onClick={onConfirmCleaned}>
                  Use edited version
                </Button>
              )}
              {onDiscardCleaned && (
                <Button variant="secondary" size="sm" onClick={onDiscardCleaned}>
                  Keep original
                </Button>
              )}
            </div>
          )}
        </div>
      )}

      {/* One compact row, not a stack: pixels spent under the player come out
          of the player, and height is what sets its width at 9:16. */}
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-x-2 gap-y-1">
        <span className="font-mono text-mono text-muted">
          {clip.origin === 'creator' ? 'Your selection' : `Clip #${clip.rank ?? '—'}`} ·{' '}
          {clipDur.toFixed(1)}s
        </span>
        {/* Headline fit signal is the honest tier, not a raw number (docs/UI.md).
            A creator-made selection was never engine-scored (Issue 373) — no
            fit tier, a neutral provenance chip instead. */}
        {clip.origin === 'creator' ? (
          <span className="rounded-xs bg-accent-soft px-2 py-0.5 font-ui text-label font-medium uppercase tracking-[0.08em] text-accent-text">
            Not engine-scored
          </span>
        ) : (
          <FitBadge tier={fitTier(clip.score)} />
        )}
      </div>

      {statusLine && <div className="text-xs text-subtle">{statusLine}</div>}

      {below}
    </Card>
  )
}
