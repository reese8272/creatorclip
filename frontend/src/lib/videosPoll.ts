import type { VideoListResponse } from '@/types'

// Poll while any clip-trackable video is mid-pipeline; stop once everything has
// settled. TanStack Query owns the lifecycle (replaces the hand-rolled backoff
// timer in static/index.html). Non-clippable linked rows (Issue 139) have no
// running pipeline, so they never keep the poll alive. Shared by the Dashboard
// and the standalone Review/Editor landings so their `['videos']` caches advance
// identically.
const POLL_MS = 5000

export function videosRefetchInterval(data: VideoListResponse | undefined): number | false {
  const inFlight = (data?.videos ?? []).some(
    (v) => v.clippable && (v.ingest_status === 'pending' || v.ingest_status === 'running'),
  )
  return inFlight ? POLL_MS : false
}
