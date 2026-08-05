import { useState } from 'react'
import { REVIEW_PLAYER_W } from '@/lib/toolLayout'
import { FitBadge } from '@/components/ui/fit-badge'
import { fitTier } from '@/lib/fit'
import { StagePlaceholder } from '@/components/stage/StagePlaceholder'
import { TrimFilmstrip } from '@/components/review/TrimFilmstrip'
import { useClipRender } from '@/hooks/useClipRender'
import type { ReviewClip } from '@/types'
import { ArrowRight } from '@/components/ui/icon'
import { ICON_INLINE, ICON_SIZE } from '@/components/ui/iconSizes'
import { VideoPlayer } from '@/components/ui/video-player'

// Issue 306 (redesign): the left review column — the 9:16 player, clip meta + fit
// pill, the filmstrip trim, and "Next clip →". Trim state is owned by the parent
// (Review) so the "Save trim" action in the Your-call card submits the same region.
export function ClipPlayer({
  clip,
  trimStart,
  trimEnd,
  onTrimChange,
  onNext,
}: {
  clip: ReviewClip
  trimStart: number
  trimEnd: number
  onTrimChange: (start: number, end: number) => void
  onNext: () => void
}) {
  // Clip-relative timebase: the setup origin (dd92fcd), matching what the
  // backend validates and cuts against (end_s - (setup_start_s ?? start_s)).
  // Using end_s - start_s here mis-scaled the filmstrip's x↔time mapping so
  // every handle drag submitted systematically compressed seconds.
  const clipDur = clip.end_s - (clip.setup_start_s ?? clip.start_s)
  // Issue 182: clips stream through the authed download endpoint (presigned R2 in
  // prod, file stream in dev). `inline` backs the <video>.
  const mediaSrc = `/clips/${clip.id}/download?disposition=inline`
  const [currentTime, setCurrentTime] = useState(0)
  // Render-trigger state machine — extracted to a shared hook (Issue 423) so
  // the Editor's stage exposes the same render/failure/expired ladder.
  const render = useClipRender(clip)

  return (
    <div className="flex animate-fade-in flex-col items-center gap-4">
      {clip.render_uri ? (
        <VideoPlayer
          // Keyed on the artifact, not just the clip: a confirmed trim/clean
          // swap changes render_uri but not the download src, so without a key
          // change the element would keep playing the old media until reload.
          mediaKey={clip.render_uri ?? undefined}
          src={mediaSrc}
          label={`Clip ${clip.rank ?? ''} preview`}
          // Chrome blocks unmuted autoplay: the element stays paused on a black
          // first frame until a user gesture (Issue 359d — the "black render"
          // symptom). The primitive pairs autoPlay with muted for exactly this.
          autoPlay
          onTimeChange={setCurrentTime}
          // Issue 389: height-derived instead of a frozen 320px cap, so the
          // player grows with the viewport inside the tool shell.
          className={REVIEW_PLAYER_W}
        />
      ) : (
        <StagePlaceholder clip={clip} render={render} widthClass={REVIEW_PLAYER_W} />
      )}

      <div className="flex flex-col items-center gap-2">
        <div className="text-center font-mono text-xs text-muted">
          {clip.origin === 'creator' ? 'Your selection' : `Clip #${clip.rank ?? '—'}`} ·{' '}
          {clipDur.toFixed(1)}s
        </div>
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

      <TrimFilmstrip
        duration={clipDur}
        trimStart={trimStart}
        trimEnd={trimEnd}
        currentTime={currentTime}
        onChange={onTrimChange}
      />

      <button onClick={onNext} className="mt-1 text-xs text-muted hover:text-fg">
        Next clip <ArrowRight className={`${ICON_SIZE.md} ${ICON_INLINE}`} aria-hidden="true" />
      </button>
    </div>
  )
}
