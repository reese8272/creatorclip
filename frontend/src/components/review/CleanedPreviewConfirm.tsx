import { useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { useCleanedUriPoll } from '@/hooks/useCleanedUriPoll'
import { Button } from '@/components/ui/button'
import type { ReviewClip } from '@/types'

// The review-then-confirm affordance shared by the clean pass (Editor) and the
// trim re-render (Review): poll until the pending cleaned_render_uri lands,
// preview it, then either swap it in via POST /clips/{id}/clean/confirm or keep
// the original (client-side discard — no server call, matching the clean-pass
// flow). Extracted from CleanPassPanel behavior-identically.
export function CleanedPreviewConfirm({
  clip,
  enabled,
  onConfirmed,
  onDiscarded,
  onError,
}: {
  clip: ReviewClip
  enabled: boolean
  onConfirmed: () => void
  onDiscarded: () => void
  onError: (message: string) => void
}) {
  const queryClient = useQueryClient()
  const cleanedUri = useCleanedUriPoll(clip.video_id, clip.id, enabled)

  async function confirm() {
    try {
      await api(`/clips/${clip.id}/clean/confirm`, { method: 'POST' })
      onConfirmed()
      queryClient.invalidateQueries({ queryKey: ['review-clips', clip.video_id] })
    } catch {
      onError('Swap failed — try again.')
    }
  }

  if (!cleanedUri) return null
  return (
    <div>
      <video
        src={cleanedUri}
        controls
        className="mt-2 w-full rounded-sm border border-default"
      />
      <div className="mt-2 flex gap-2">
        <Button size="sm" onClick={confirm}>
          Use cleaned version
        </Button>
        <Button variant="secondary" size="sm" onClick={onDiscarded}>
          Keep original
        </Button>
      </div>
    </div>
  )
}
