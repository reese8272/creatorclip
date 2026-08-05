import { Link } from 'react-router-dom'
import { cn } from '@/lib/utils'
import type { ClipRenderState } from '@/hooks/useClipRender'
import type { ReviewClip } from '@/types'
import { Play, RotateCcw } from '@/components/ui/icon'
import { ICON_SIZE } from '@/components/ui/iconSizes'

// The unified not-yet-playable stage states (Issue 423): source-expired card,
// render spinner, failed + retry, and the manual render trigger — extracted
// verbatim from ClipPlayer so Review and the Editor share one ladder. The
// placeholder takes the SAME width class as the player so the two states never
// jump when a render lands.
export function StagePlaceholder({
  clip,
  render,
  widthClass,
  className,
}: {
  clip: ReviewClip
  render: ClipRenderState
  /** The exact player-width literal (placeholder width = player width). */
  widthClass: string
  className?: string
}) {
  const { rendering, renderError, sourceExpired, triggerRender } = render
  return (
    <div
      className={cn(
        widthClass,
        // Same width as the real player so the two states do not jump.
        'flex aspect-[9/16] flex-col items-center justify-center gap-3 rounded-xl border border-default bg-black px-6 text-center text-sm text-subtle',
        className,
      )}
    >
      {sourceExpired ? (
        <>
          <span className="text-danger">Source media expired</span>
          <span className="text-xs">
            The original upload passed its retention window — re-upload the video to render this
            clip.
          </span>
          <Link
            to="/dashboard"
            className="rounded-sm border border-strong bg-bg px-3 py-1.5 text-xs text-muted hover:bg-elevated hover:text-fg"
          >
            Upload again
          </Link>
        </>
      ) : rendering ? (
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
            <RotateCcw className={ICON_SIZE.sm} aria-hidden="true" /> Retry render
          </button>
        </>
      ) : (
        <>
          <span>Not rendered yet</span>
          <button
            onClick={triggerRender}
            className="rounded-sm border border-strong bg-bg px-3 py-1.5 text-xs text-muted hover:bg-elevated hover:text-fg"
          >
            <Play className={ICON_SIZE.sm} aria-hidden="true" /> Render this clip
          </button>
        </>
      )}
      {renderError && <span className="text-xs text-danger">{renderError}</span>}
    </div>
  )
}
