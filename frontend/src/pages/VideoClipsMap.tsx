import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { QueryErrorState } from '@/components/QueryErrorState'
import { WhyThisClip } from '@/components/review/WhyThisClip'
import { fitTier } from '@/lib/fit'
import { FitBadge } from '@/components/ui/fit-badge'
import type { ReviewClip, ReviewClipListResponse, Video, VideoListResponse } from '@/types'

// SOURCE_NEEDED_HELP copy reused from VideoTable (single source of truth would
// require an extraction — noted as a future cleanup, not a DRY violation since
// the target is a separate page with no import path to VideoTable without
// creating a circular dependency risk).
const SOURCE_NEEDED_HELP =
  'We never download your video from YouTube (per their Terms of Service). To generate clips, ' +
  'upload the original file — for example export it from Google Takeout — and it will process ' +
  'automatically.'

// ── Sub-components ────────────────────────────────────────────────────────────

function TimelineMarker({
  clip,
  durationS,
  isSelected,
  onSelect,
}: {
  clip: ReviewClip
  durationS: number
  isSelected: boolean
  onSelect: () => void
}) {
  const setupStart = clip.setup_start_s ?? clip.start_s
  const peakS = clip.peak_s ?? clip.start_s
  const leftPct = durationS > 0 ? (setupStart / durationS) * 100 : 0
  const widthPct = durationS > 0 ? ((clip.end_s - setupStart) / durationS) * 100 : 0
  const peakOffsetPct =
    widthPct > 0 ? ((peakS - setupStart) / (clip.end_s - setupStart)) * 100 : 50

  const tier = fitTier(clip.score)
  const tierColor: Record<string, string> = {
    strong: 'bg-fit-strong',
    moderate: 'bg-fit-moderate',
    exploratory: 'bg-fit-exploratory',
  }

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`Clip at ${setupStart.toFixed(1)}s — principle: ${clip.principle || 'unknown'}, ${tier} fit`}
      aria-pressed={isSelected}
      className={[
        'absolute top-1/2 h-5 -translate-y-1/2 cursor-pointer rounded-sm transition-all',
        'border border-default opacity-80 hover:opacity-100',
        tierColor[tier] ?? 'bg-fit-exploratory',
        isSelected ? 'ring-2 ring-accent opacity-100 z-10' : 'z-0',
      ].join(' ')}
      style={{ left: `${leftPct}%`, width: `max(${widthPct}%, 4px)` }}
      onClick={onSelect}
      onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && onSelect()}
    >
      {/* Peak notch — small triangular flag at the peak position within the marker */}
      <div
        aria-label="peak moment"
        className="peak-flag absolute -top-2 h-2 w-1 rounded-sm bg-white opacity-80"
        style={{ left: `${peakOffsetPct}%` }}
      />
    </div>
  )
}

function EmptyState({
  origin,
  skipReasonLabel,
}: {
  origin: string
  skipReasonLabel: string | null | undefined
}) {
  if (origin === 'catalog') {
    return (
      <p className="py-8 text-center text-sm text-subtle">
        This video is a catalog reference — not clippable.
      </p>
    )
  }
  if (origin === 'link') {
    return (
      <p
        className="py-8 text-center text-sm text-subtle cursor-help border-b border-dotted border-default inline-block"
        title={SOURCE_NEEDED_HELP}
      >
        Upload source file to clip
      </p>
    )
  }
  // origin === 'upload' or unknown
  if (skipReasonLabel) {
    return (
      <div className="py-8 text-center max-w-md mx-auto">
        <p className="text-sm font-medium text-fg mb-2">No clips were generated</p>
        <p className="text-xs text-subtle leading-relaxed">{skipReasonLabel}</p>
        <p className="mt-3 text-xs text-muted">
          These estimates are grounded in your own data — not a guarantee of performance.
        </p>
      </div>
    )
  }
  return (
    <p className="py-8 text-center text-sm text-subtle">
      Generate clips to see your timeline.
    </p>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

export function VideoClipsMap() {
  const { videoId } = useParams<{ videoId: string }>()
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null)

  const videosQuery = useQuery({
    queryKey: ['videos'],
    queryFn: () => api<VideoListResponse>('/videos'),
  })

  const clipsQuery = useQuery({
    queryKey: ['clips-full', videoId],
    queryFn: () => api<ReviewClipListResponse>(`/videos/${videoId}/clips`),
    enabled: Boolean(videoId),
  })

  const video: Video | undefined = videosQuery.data?.videos.find((v) => v.id === videoId)
  const clips: ReviewClip[] = clipsQuery.data?.clips ?? []
  const durationS: number = video?.duration_s ?? 0
  const origin: string = video?.origin ?? 'upload'
  const skipReasonLabel: string | null | undefined = clipsQuery.data?.skip_reason_label

  const selectedClip = clips.find((c) => c.id === selectedClipId) ?? null

  if (videosQuery.isPending || clipsQuery.isPending) {
    return (
      <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-8">
        <p className="py-8 text-center text-sm text-subtle">Loading…</p>
      </main>
    )
  }

  // A failed load must NOT fall through to "Video not found." — a creator whose
  // video exists would be told it doesn't (Recap retry idiom, Issue 361 sweep).
  if (videosQuery.isError || clipsQuery.isError) {
    return (
      <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-8">
        <QueryErrorState
          title="Couldn’t load this video’s clip map."
          onRetry={() => {
            if (videosQuery.isError) void videosQuery.refetch()
            if (clipsQuery.isError) void clipsQuery.refetch()
          }}
        />
      </main>
    )
  }

  if (!video) {
    return (
      <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-8">
        <p className="py-8 text-center text-sm text-subtle">Video not found.</p>
      </main>
    )
  }

  return (
    <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-8">
      {/* Header */}
      <div className="mb-6">
        <Link to="/dashboard" className="text-xs text-accent-text hover:underline">
          ← Dashboard
        </Link>
        <h1 className="mt-2 text-lg font-medium text-fg truncate">
          {video.title ?? 'Untitled video'}
        </h1>
        <p className="text-xs text-subtle font-mono">{video.youtube_video_id}</p>
      </div>

      {clips.length === 0 ? (
        <div className="flex justify-center">
          <EmptyState origin={origin} skipReasonLabel={skipReasonLabel} />
        </div>
      ) : (
        <>
          {/* Timeline bar */}
          <section aria-label="Clip timeline" className="mb-6">
            <h2 className="mb-2 text-xs font-medium uppercase tracking-[0.06em] text-muted">
              Candidate timeline
            </h2>
            <div
              className="relative h-10 w-full rounded-md bg-elevated border border-default overflow-hidden"
              aria-label={`Video timeline — ${clips.length} clip candidates`}
            >
              {clips.map((clip) => (
                <TimelineMarker
                  key={clip.id}
                  clip={clip}
                  durationS={durationS}
                  isSelected={selectedClipId === clip.id}
                  onSelect={() =>
                    setSelectedClipId((prev) => (prev === clip.id ? null : clip.id))
                  }
                />
              ))}
            </div>
            {durationS > 0 && (
              <div className="mt-1 flex justify-between font-mono text-xs text-subtle">
                <span>0s</span>
                <span>{durationS.toFixed(0)}s</span>
              </div>
            )}
          </section>

          {/* Legend */}
          <div className="mb-4 flex flex-wrap items-center gap-3 text-xs text-subtle">
            <span className="font-medium text-fg">Fit:</span>
            <FitBadge tier="strong" />
            <FitBadge tier="moderate" />
            <FitBadge tier="exploratory" />
            <span className="ml-2">Click a marker to see why this clip was chosen.</span>
          </div>

          {/* Inline detail panel — shows WhyThisClip for the selected clip */}
          {selectedClip && (
            <section
              className="mb-6 rounded-md border border-default bg-surface p-4"
              aria-label="Clip detail"
            >
              <div className="mb-3 flex items-center justify-between gap-4">
                <h2 className="text-sm font-medium text-fg">Why this clip</h2>
                <div className="flex gap-2">
                  <Link
                    to={`/review?video_id=${videoId}&clip_id=${selectedClip.id}`}
                    className="text-xs text-accent-text hover:underline"
                    aria-label="Review this clip in order"
                  >
                    Review →
                  </Link>
                  <button
                    onClick={() => setSelectedClipId(null)}
                    className="text-xs text-subtle hover:text-fg"
                    aria-label="Close detail panel"
                  >
                    ✕
                  </button>
                </div>
              </div>
              <WhyThisClip clip={selectedClip} />
            </section>
          )}

          {/* Review in order CTA */}
          <div className="mt-4 flex items-center gap-4">
            <Link
              to={`/review?video_id=${videoId}`}
              className="text-sm text-accent-text hover:underline"
            >
              Review all clips in order →
            </Link>
            <span className="text-xs text-subtle">
              {clips.length} candidate{clips.length !== 1 ? 's' : ''}
            </span>
          </div>
        </>
      )}
    </main>
  )
}
