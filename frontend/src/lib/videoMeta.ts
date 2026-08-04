import { relativeTime } from '@/lib/utils'

import type { Video } from '@/types'

// The human secondary line for a library row (Issue 388).
//
// It used to be `{kind} · {youtube_video_id}` — a raw ID like `dQw4w9WgXcQ`,
// which is an internal handle a creator cannot recognise their own footage by.
// Creators identify footage by what it IS and when they made it, so the line is
// now form, length and age. The ID stays available on the row's title attribute
// and on the video-detail page, where linking out to YouTube is the point.

const KIND_LABELS: Record<string, string> = {
  long: 'Long-form',
  short: 'Short',
}

export function formatKind(kind: string): string {
  return KIND_LABELS[kind] ?? kind
}

export function formatDuration(seconds: number | null): string | null {
  if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) return null
  const total = Math.round(seconds)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const mm = h > 0 ? String(m).padStart(2, '0') : String(m)
  return `${h > 0 ? `${h}:` : ''}${mm}:${String(s).padStart(2, '0')}`
}

/** e.g. "Long-form · 12:34 · 3d ago". Omits parts that are unknown. */
export function videoMetaLine(video: Pick<Video, 'kind' | 'duration_s' | 'created_at'>): string {
  return [formatKind(video.kind), formatDuration(video.duration_s), relativeTime(video.created_at)]
    .filter(Boolean)
    .join(' · ')
}

/**
 * Label for a video with no title (Issue 412): "Untitled · Jun 12". Derived
 * from the upload date because that is how a creator tells two untitled
 * uploads apart; the old uppercase UNTITLED badge read as debug output on the
 * primary library surface. There is no filename field to derive from —
 * the backend never stores one.
 */
export function untitledLabel(video: Pick<Video, 'created_at'>): string {
  const d = new Date(video.created_at)
  if (Number.isNaN(d.getTime())) return 'Untitled'
  return `Untitled · ${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`
}
