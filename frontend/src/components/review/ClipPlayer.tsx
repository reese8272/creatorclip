import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api'
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
  const queryClient = useQueryClient()
  const [renderError, setRenderError] = useState('')
  const [requesting, setRequesting] = useState(false)

  // Auto-render (auto-render) normally renders clips in the background right after
  // generation, so this manual trigger is a fallback/retry affordance. Charges no
  // extra minutes (paid at upload). Only `running` (or a click we just made) shows
  // the spinner; a `pending` clip keeps the manual button so a never-queued clip
  // can't spin forever. Review's poll swaps in the video once render_uri lands.
  const rendering = requesting || clip.render_status === 'running'

  async function triggerRender() {
    setRenderError('')
    setRequesting(true)
    try {
      await api(`/clips/${clip.id}/render`, { method: 'POST' })
      await queryClient.invalidateQueries({ queryKey: ['review-clips'] })
    } catch (e) {
      // 409 = a render is already in progress; treat as success (the poll picks it up).
      if (e instanceof ApiError && e.status === 409) {
        await queryClient.invalidateQueries({ queryKey: ['review-clips'] })
      } else {
        setRenderError(e instanceof ApiError ? e.message : 'Render failed — try again.')
      }
    } finally {
      // Clear the optimistic flag once the request settles. From here the spinner is
      // driven by server state (render_status === 'running' set by the worker), so a
      // render that fails fast — e.g. the source media was purged — surfaces as
      // "Render failed" + retry instead of spinning forever. Previously `requesting`
      // was only reset on the error path, so a 202/409 latched the spinner permanently
      // whenever no render_uri ever landed (the "render loop").
      setRequesting(false)
    }
  }

  return (
    <div className="flex animate-fade-in flex-col items-center gap-4">
      {clip.render_uri ? (
        <video
          key={clip.id}
          src={mediaSrc}
          controls
          playsInline
          // Chrome blocks unmuted autoplay: the element stays paused on a black
          // first frame until a user gesture (Issue 359d — the "black render"
          // symptom). Muted autoplay is allowed; the controls let the user unmute.
          autoPlay
          muted
          preload="auto"
          onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
          className="aspect-[9/16] w-full max-w-[340px] rounded-xl border border-default bg-black shadow-accent-glow"
        />
      ) : (
        <div className="flex aspect-[9/16] w-full max-w-[340px] flex-col items-center justify-center gap-3 rounded-xl border border-default bg-black px-6 text-center text-sm text-subtle">
          {rendering ? (
            <>
              <span className="inline-block h-5 w-5 animate-spin rounded-full border-2 border-strong border-t-accent" />
              <span>Rendering your clip… (~30s)</span>
            </>
          ) : clip.render_status === 'failed' ? (
            <>
              <span className="text-danger">Render failed</span>
              <button
                onClick={triggerRender}
                className="rounded-sm border border-strong bg-bg px-3 py-1.5 text-xs text-muted hover:bg-elevated hover:text-fg"
              >
                ↻ Retry render
              </button>
            </>
          ) : (
            <>
              <span>Not rendered yet</span>
              <button
                onClick={triggerRender}
                className="rounded-sm border border-strong bg-bg px-3 py-1.5 text-xs text-muted hover:bg-elevated hover:text-fg"
              >
                ▶ Render this clip
              </button>
            </>
          )}
          {renderError && <span className="text-xs text-danger">{renderError}</span>}
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
