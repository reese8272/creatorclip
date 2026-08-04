import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { isWaveformPeaks, type WaveformPeaks } from '@/lib/peaks'

/**
 * Fetch a video's waveform envelope (Issue 392).
 *
 * `enabled` should be the video row's `has_peaks`, so a video that will never
 * have a waveform costs zero requests rather than a 404 on every editor open.
 *
 * Cached effectively forever within a session: the artifact is immutable for a
 * given video (the server sends `Cache-Control: immutable` too) and re-fetching
 * hundreds of kilobytes on a window focus would be pure waste.
 */
export function useVideoPeaks(videoId: string | null | undefined, enabled: boolean) {
  const query = useQuery({
    queryKey: ['video-peaks', videoId],
    queryFn: () => api<unknown>(`/videos/${videoId}/peaks`),
    enabled: !!videoId && enabled,
    staleTime: Infinity,
    gcTime: 30 * 60_000,
    // A missing waveform is a normal terminal state, not a transient failure —
    // retrying spends requests to be told the same thing.
    retry: false,
  })

  // Validate rather than trust: a malformed payload must degrade to the honest
  // flat track, never to a half-drawn waveform.
  const peaks: WaveformPeaks | null = isWaveformPeaks(query.data) ? query.data : null
  return { peaks, isPending: query.isPending && !!videoId && enabled }
}
