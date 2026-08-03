import { useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { useCleanedUriPoll } from '@/hooks/useCleanedUriPoll'
import { Button } from '@/components/ui/button'
import type { ReviewClip } from '@/types'
import { VideoPlayer } from '@/components/ui/video-player'

// The review-then-confirm affordance shared by the clean pass (Editor) and the
// trim re-render (Review): poll until the pending cleaned_render_uri lands,
// preview it, then either swap it in via POST /clips/{id}/clean/confirm or
// discard it via POST /clips/{id}/clean/discard (Issue 364 — a client-side-only
// discard left cleaned_render_uri set server-side, 409-ing the next clean/edit).
// Extracted from CleanPassPanel behavior-identically.
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
      // Drop the poll cache outright: confirm nulls cleaned_render_uri
      // server-side, and within the global 30s staleTime a cached entry whose
      // old URI is still set would make the next trim/clean's poll render the
      // previous version instantly and never poll for the new render.
      queryClient.removeQueries({ queryKey: ['clips-clean-poll', clip.video_id] })
      queryClient.invalidateQueries({ queryKey: ['review-clips', clip.video_id] })
    } catch {
      onError('Swap failed — try again.')
    }
  }

  async function discard() {
    try {
      await api(`/clips/${clip.id}/clean/discard`, { method: 'POST' })
      onDiscarded()
      // Same cache-drop rationale as confirm(): discard also nulls
      // cleaned_render_uri server-side.
      queryClient.removeQueries({ queryKey: ['clips-clean-poll', clip.video_id] })
      queryClient.invalidateQueries({ queryKey: ['review-clips', clip.video_id] })
    } catch {
      onError('Discard failed — try again.')
    }
  }

  // cleanedUri is purely the readiness signal — the raw storage URI is opaque
  // (s3://bucket/key in prod R2, not fetchable by a browser). Playback goes
  // through the authed download endpoint, which presigns/streams per env.
  if (!cleanedUri) return null
  return (
    <div>
      <VideoPlayer
        src={`/clips/${clip.id}/download?variant=cleaned&disposition=inline`}
        label="Cleaned clip preview"
        density="compact"
        className="mt-2 w-full"
      />
      <div className="mt-2 flex gap-2">
        <Button size="sm" onClick={confirm}>
          Use cleaned version
        </Button>
        <Button variant="secondary" size="sm" onClick={discard}>
          Keep original
        </Button>
      </div>
    </div>
  )
}
