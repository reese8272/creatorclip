import { useState } from 'react'
import { FitBadge } from '@/components/ui/fit-badge'
import { fitTier } from '@/lib/fit'
import { TrimFilmstrip } from '@/components/review/TrimFilmstrip'
import type { ReviewClip } from '@/types'

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
  const clipDur = clip.end_s - clip.start_s
  // Issue 182: clips stream through the authed download endpoint (presigned R2 in
  // prod, file stream in dev). `inline` backs the <video>.
  const mediaSrc = `/clips/${clip.id}/download?disposition=inline`
  const [currentTime, setCurrentTime] = useState(0)

  return (
    <div className="flex animate-fade-in flex-col items-center gap-4">
      {clip.render_uri ? (
        <video
          key={clip.id}
          src={mediaSrc}
          controls
          playsInline
          autoPlay
          onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
          className="aspect-[9/16] w-full max-w-[340px] rounded-xl border border-default bg-black shadow-accent-glow"
        />
      ) : (
        <div className="flex aspect-[9/16] w-full max-w-[340px] items-center justify-center rounded-xl border border-default bg-black text-sm text-subtle">
          Not yet rendered
        </div>
      )}

      <div className="flex flex-col items-center gap-2">
        <div className="text-center font-mono text-xs text-muted">
          Clip #{clip.rank ?? '—'} · {(clip.end_s - (clip.setup_start_s ?? clip.start_s)).toFixed(1)}s
        </div>
        {/* Headline fit signal is the honest tier, not a raw number (docs/UI.md). */}
        <FitBadge tier={fitTier(clip.score)} />
      </div>

      <TrimFilmstrip
        duration={clipDur}
        trimStart={trimStart}
        trimEnd={trimEnd}
        currentTime={currentTime}
        onChange={onTrimChange}
      />

      <button onClick={onNext} className="mt-1 text-xs text-muted hover:text-fg">
        Next clip →
      </button>
    </div>
  )
}
