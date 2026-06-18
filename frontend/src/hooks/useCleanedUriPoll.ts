import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { ReviewClipListResponse } from '@/types'

// Poll a video's clip list until the target clip gains a `cleaned_render_uri`,
// then stop. Both the clean pass and the transcript editor kick a Celery render
// and wait for the cleaned mp4 to land; this is the authoritative "ready" signal
// the vanilla page polled for (the tasks also emit SSE, but the URI is what the
// preview needs). Returns the URI once present, else null.
export function useCleanedUriPoll(
  videoId: string,
  clipId: string,
  enabled: boolean,
): string | null {
  const query = useQuery({
    queryKey: ['clips-clean-poll', videoId],
    queryFn: () => api<ReviewClipListResponse>(`/videos/${videoId}/clips`),
    enabled,
    refetchInterval: (q) => {
      const clip = q.state.data?.clips.find((c) => c.id === clipId)
      return clip?.cleaned_render_uri ? false : 3000
    },
  })
  return query.data?.clips.find((c) => c.id === clipId)?.cleaned_render_uri ?? null
}
