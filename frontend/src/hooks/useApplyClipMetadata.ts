import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { ReviewClip } from '@/types'

// Official YouTube videos.insert limits the backend PATCH validates against:
// snippet.title max 100 CHARACTERS, snippet.description max 5000 UTF-8 BYTES.
export const TITLE_MAX_CHARS = 100
export const DESCRIPTION_MAX_BYTES = 5000

/** UTF-8 byte length — mirrors the backend's byte-counted description limit. */
export function utf8ByteLength(v: string): number {
  return new TextEncoder().encode(v).length
}

export interface ClipMetadataPatch {
  applied_title?: string | null
  applied_description?: string | null
}

// PATCH /clips/{id} with tri-state semantics: key omitted = leave untouched,
// key null = clear, key string = set (Wave-1 metadata-lane contract). Awaiting
// the invalidation keeps isPending true until the review-clips cache is fresh
// (TanStack v5 invalidation-from-mutation idiom).
export function useApplyClipMetadata(clip: ReviewClip) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (patch: ClipMetadataPatch) =>
      api<ReviewClip>(`/clips/${clip.id}`, { method: 'PATCH', body: patch }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['review-clips', clip.video_id] })
    },
  })
}
