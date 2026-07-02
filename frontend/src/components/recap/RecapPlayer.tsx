import { sendActivity } from '@/lib/activity'
import type { Summary } from '@/types'

// Issue 192: the 16:9 recap player — the ClipPlayer media idiom rotated to
// landscape (aspect-video, not aspect-[9/16]). Presentational: rendering
// progress and the create/retry affordance live in the parent (Recap page),
// which follows the render task's SSE.
export function RecapPlayer({ summary }: { summary: Summary }) {
  // Recaps stream through the authed download endpoint (presigned R2 in prod,
  // file stream in dev). `inline` backs the <video>.
  const mediaSrc = `/summaries/${summary.id}/download?disposition=inline`

  if (!summary.render_uri) {
    return (
      <div className="flex aspect-video w-full flex-col items-center justify-center gap-3 rounded-xl border border-default bg-black px-6 text-center text-sm text-subtle">
        {summary.render_status === 'failed' ? (
          <span className="text-danger">Recap render failed — request a new recap below.</span>
        ) : (
          <>
            <span className="inline-block h-5 w-5 animate-spin rounded-full border-2 border-strong border-t-accent" />
            <span>Rendering your recap… this takes a few minutes.</span>
          </>
        )}
      </div>
    )
  }

  return (
    <video
      key={summary.id}
      src={mediaSrc}
      controls
      playsInline
      onPlay={() => sendActivity('click', 'recap_watched', { summary_id: summary.id })}
      className="aspect-video w-full rounded-xl border border-default bg-black shadow-accent-glow"
    />
  )
}
