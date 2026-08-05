import { useQuery } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api'
import { isCropTrack } from '@/lib/cropTrack'
import type { CropTrack, ReviewClip } from '@/types'

// The persisted crop track for a rendered clip (L26 Issue 426, endpoint from
// Issue 421). Keyed on render_uri: every re-render replaces or deletes the
// track server-side, so a new artifact must refetch — and staleTime Infinity
// because for a GIVEN artifact the track never changes. 404 (`no_crop_track`)
// is the normal pre-pipeline state, mapped to null — the overlay renders
// nothing (honest absence is the launch norm), never an error.
export function useCropTrack(clip: ReviewClip): CropTrack | null {
  const { data } = useQuery<CropTrack | null>({
    queryKey: ['crop-track', clip.id, clip.render_uri],
    enabled: Boolean(clip.render_uri),
    staleTime: Infinity,
    retry: false,
    queryFn: async () => {
      try {
        const track = await api<CropTrack>(`/clips/${clip.id}/crop-track`)
        return isCropTrack(track) ? track : null
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null
        throw e
      }
    },
  })
  return data ?? null
}
