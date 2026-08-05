import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api'
import { generateMoreMutationKey } from '@/lib/mutationKeys'
import { Button } from '@/components/ui/button'
import type { ReviewClipListResponse } from '@/types'

// Issue 431 — "Generate more clips": append-mode regeneration. One scoring
// call over the already-persisted signals (no re-ingest, no minute charge);
// the server excludes windows the creator already has, so what comes back is
// genuinely new. Mirrors GenerateClipsButton's consent/error contract: never
// auto-fired, server-authored 402/409/429/503 messages surface verbatim.
//
// mutationKey (lib/mutationKeys.ts) is load-bearing: Review suppresses its
// "all reviewed → dashboard" auto-navigate via useIsMutating on this key, so
// the redirect can't yank the creator away mid-request.
export function GenerateMoreClipsButton({
  videoId,
  onDone,
}: {
  videoId: string
  /** newTotal = full refreshed clip count; message set when nothing new was found. */
  onDone: (newTotal: number, message: string | null) => void
}) {
  const queryClient = useQueryClient()
  const gen = useMutation({
    mutationKey: generateMoreMutationKey(videoId),
    mutationFn: () =>
      api<ReviewClipListResponse>(`/videos/${videoId}/clips/generate-more`, { method: 'POST' }),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['review-clips', videoId] })
      void queryClient.invalidateQueries({ queryKey: ['clip-counts'] })
      onDone(data.clips.length, data.message ?? null)
    },
  })

  const errorMessage = gen.isError
    ? gen.error instanceof ApiError
      ? gen.error.message
      : 'Request failed — please retry.'
    : null

  return (
    <div className="flex flex-col items-end gap-1">
      <Button size="sm" variant="secondary" disabled={gen.isPending} onClick={() => gen.mutate()}>
        {gen.isPending
          ? 'Finding more clips… (a minute or two)'
          : gen.isError
            ? 'Retry generate more'
            : 'Generate more clips'}
      </Button>
      {errorMessage && <span className="max-w-[280px] text-xs text-danger">{errorMessage}</span>}
      {!gen.isError && gen.data?.message && (
        <span className="max-w-[280px] text-xs text-muted">{gen.data.message}</span>
      )}
    </div>
  )
}
