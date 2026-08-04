import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'
import { SOURCE_PLAYER_W } from '@/lib/toolLayout'
import { fitTier } from '@/lib/fit'
import { fmtClock } from '@/lib/timecode'
import type { FitTier } from '@/components/ui/fit-badge'
import { Chip } from '@/components/Chip'
import { Button } from '@/components/ui/button'
import { ChaptersPanel } from '@/components/analysis/ChaptersPanel'
import { FullTranscriptPanel } from '@/components/editor/FullTranscriptPanel'
import { MasterTimeline, type SourceSelection } from '@/components/editor/MasterTimeline'
import { useVideoPeaks } from '@/hooks/useVideoPeaks'
import type { Chapter, ReviewClip, Video, VideoTranscript } from '@/types'
import { ArrowRight } from '@/components/ui/icon'
import { ICON_INLINE, ICON_SIZE } from '@/components/ui/iconSizes'
import { VideoPlayer, type VideoPlayerHandle } from '@/components/ui/video-player'

const TIER_LABEL: Record<FitTier, string> = {
  strong: 'Strong',
  moderate: 'Moderate',
  exploratory: 'Exploratory',
}
const TIER_TEXT: Record<FitTier, string> = {
  strong: 'oklch(72% 0.16 145)',
  moderate: 'oklch(78% 0.14 75)',
  exploratory: 'var(--color-muted)',
}

// Confirmation card for a proposed creator clip (Issue 373): shows the range,
// posts it, and advances into the clips list on success. Server-authored
// errors (bounds, 402/429/503, source_expired) render verbatim.
function CreateClipCard({
  selection,
  videoId,
  onClose,
}: {
  selection: SourceSelection
  videoId: string
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const create = useMutation({
    mutationFn: () =>
      api<ReviewClip>(`/videos/${videoId}/clips`, {
        method: 'POST',
        body: { start_s: selection.start_s, end_s: selection.end_s },
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['review-clips', videoId] })
      onClose()
    },
  })
  const duration = selection.end_s - selection.start_s
  const errorMessage = create.isError
    ? create.error instanceof ApiError
      ? create.error.message
      : 'Request failed — please retry.'
    : null

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-md border border-accent-border bg-gradient-to-br from-accent-soft to-surface px-3.5 py-2.5">
      <span className="font-mono text-small text-fg">
        {fmtClock(selection.start_s)} → {fmtClock(selection.end_s)}
        <span className="text-subtle"> · {duration.toFixed(1)}s</span>
      </span>
      <Button size="sm" disabled={create.isPending} onClick={() => create.mutate()}>
        {create.isPending ? 'Creating…' : 'Create clip'}
      </Button>
      <Button variant="ghost" size="sm" onClick={onClose} disabled={create.isPending}>
        Cancel
      </Button>
      <span className="text-label text-subtle">
        Renders automatically with your style defaults — no extra minutes.
      </span>
      {errorMessage && <span className="w-full text-xs text-danger">{errorMessage}</span>}
    </div>
  )
}

// Issue 307 — Long-form source mode; Issue 372 made it real: full-source
// player (streamed via /videos/{id}/stream) + searchable segment transcript
// replace the earlier honest placeholders, alongside the candidate-segment
// master timeline, ranked suggested clips, and chapters.
export function LongFormEditor({
  clips,
  videoId,
  video,
  onOpenClip,
  className,
}: {
  clips: ReviewClip[]
  videoId: string
  /** The videos-list row (shared ['videos'] cache) — carries duration_s and
   *  clippable (source presence) for proactive retention honesty. */
  video?: Video
  onOpenClip: (clipId: string) => void
  /** Sizing from the tool shell (Issue 389) — the parent owns the height. */
  className?: string
}) {
  // Issue 373: engine candidates keep the ranked "Suggested" list; creator-made
  // selections get their own honest group (never a fake fit tier).
  const engineClips = [...clips]
    .filter((c) => c.origin !== 'creator')
    .sort((a, b) => (a.rank ?? 999) - (b.rank ?? 999))
  const creatorClips = clips.filter((c) => c.origin === 'creator')
  const ranked = [...creatorClips, ...engineClips]
  // Chapters are generated on demand in the right-rail panel; once present they
  // also annotate the master timeline (see ChaptersPanel onChapters).
  const [chapters, setChapters] = useState<Chapter[]>([])
  // Proposed creator clip (Issue 373) — fed by timeline drag OR the transcript
  // panel's "Clip this"; confirmed via CreateClipCard.
  const [pendingSelection, setPendingSelection] = useState<SourceSelection | null>(null)

  const transcriptQuery = useQuery({
    queryKey: ['video-transcript', videoId],
    queryFn: () => api<VideoTranscript>(`/videos/${videoId}/transcript`),
  })

  // Source playhead — drives the transcript's active segment; transcript
  // clicks seek back. Same sync idiom as the short-form editor, at segment
  // granularity.
  const playerRef = useRef<VideoPlayerHandle>(null)
  const [currentTime, setCurrentTime] = useState(0)
  const [streamError, setStreamError] = useState(false)

  function seek(t: number) {
    if (playerRef.current) {
      playerRef.current.seek(t)
      setCurrentTime(t)
    }
  }

  // Retention honesty: clippable === false means the source was purged (or
  // never stored) — show the re-upload card proactively instead of a dead
  // player. A stream error (e.g. purge raced the page) lands the same way.
  const sourceAvailable = (video ? video.clippable : true) && !streamError

  // Issue 392 — gated on has_peaks so a video that will never have a waveform
  // costs zero requests instead of a 404 on every open. Note peaks OUTLIVE the
  // source: `sourceAvailable` can be false (media purged) while the waveform is
  // still served, which is the point — you can still see the shape of the audio.
  const { peaks } = useVideoPeaks(videoId, video?.has_peaks ?? false)

  // Real source duration (Issue 372): videos-list row → transcript span →
  // furthest clip end as the last-resort fallback so the timeline never
  // renders degenerate.
  const sourceDuration =
    video?.duration_s ??
    transcriptQuery.data?.duration_s ??
    clips.reduce((max, c) => Math.max(max, c.end_s), 0)

  return (
    <div
      className={cn(
        'grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_300px] lg:grid-rows-[minmax(0,1fr)]',
        className,
      )}
    >
      {/* The source player and its master timeline stay pinned; only the lists
          below them scroll, so the timeline you are selecting against cannot
          leave the viewport (Issue 389). */}
      <div className="flex min-h-0 flex-col gap-4">
        {sourceAvailable ? (
          <VideoPlayer
            ref={playerRef}
            src={`/videos/${videoId}/stream`}
            label="Long-form source"
            aspect="landscape"
            transport
            onTimeChange={setCurrentTime}
            onError={() => setStreamError(true)}
            className={cn(SOURCE_PLAYER_W, 'mx-auto shrink-0')}
          />
        ) : (
          <div
            className={cn(
              SOURCE_PLAYER_W,
              'mx-auto flex aspect-video shrink-0 flex-col items-center justify-center gap-3 rounded-xl border border-default bg-black/60 px-6 text-center text-sm text-subtle',
            )}
          >
            <span className="text-danger">Source media expired</span>
            <span className="text-xs">
              The original upload passed its retention window — re-upload the video to edit the
              full source. Rendered clips below stay playable.
            </span>
            <Link
              to="/dashboard"
              className="rounded-sm border border-strong bg-bg px-3 py-1.5 text-xs text-muted hover:bg-elevated hover:text-fg"
            >
              Upload again
            </Link>
          </div>
        )}

        {/* Chip scan callout */}
        <div className="flex shrink-0 items-center gap-3 rounded-md border border-accent-border bg-gradient-to-br from-accent-soft to-surface px-3.5 py-2.5">
          <Chip pose="magnify" size={46} className="flex-shrink-0" />
          <div className="text-small leading-relaxed text-fg">
            <strong className="text-accent-text">Chip:</strong> I scanned your source and surfaced{' '}
            {clips.length} clip-worthy {clips.length === 1 ? 'moment' : 'moments'}. The strong-fit ones
            are highlighted below — open either to refine it as a short.
          </div>
        </div>

        <MasterTimeline
          clips={ranked}
          chapters={chapters}
          sourceDuration={sourceDuration}
          onOpenClip={onOpenClip}
          onSelect={setPendingSelection}
          peaks={peaks}
        />

        {pendingSelection && (
          <CreateClipCard
            selection={pendingSelection}
            videoId={videoId}
            onClose={() => setPendingSelection(null)}
          />
        )}

        {/* Everything below the timeline is a list — it scrolls, the instrument
            above it does not. No tabIndex needed: every row carries an Open or
            seek button, which already satisfies scrollable-region-focusable. */}
        <section
          data-tool-scroll
          aria-label="Source clips and transcript"
          className="flex min-h-0 flex-1 flex-col gap-4 lg:overflow-y-auto"
        >
        {/* Your clips (Issue 373) — creator-made selections, honest provenance */}
        {creatorClips.length > 0 && (
          <div className="rounded-md border border-default bg-surface shadow-sm inset-shadow-highlight">
            <div className="flex items-center gap-2 border-b border-default px-4 py-3.5">
              <Chip pose="laptop" size={24} />
              <span className="text-h3 font-semibold text-fg">Your clips</span>
            </div>
            {creatorClips.map((c) => (
              <div
                key={c.id}
                className="grid grid-cols-[auto_1fr_auto] items-center gap-3.5 border-b border-default px-4 py-3.5 last:border-b-0"
              >
                <span className="font-mono text-label text-subtle">{fmtClock(c.start_s)}</span>
                <div>
                  <div className="text-small text-fg">Your selection</div>
                  <div className="text-label text-subtle">
                    {(c.end_s - c.start_s).toFixed(0)}s · not engine-scored
                  </div>
                </div>
                <Button variant="ghost" size="sm" onClick={() => onOpenClip(c.id)}>
                  Open <ArrowRight className={`${ICON_SIZE.md} ${ICON_INLINE}`} aria-hidden="true" />
                </Button>
              </div>
            ))}
          </div>
        )}

        {/* Suggested clips */}
        <div className="rounded-md border border-default bg-surface shadow-sm inset-shadow-highlight">
          <div className="flex items-center gap-2 border-b border-default px-4 py-3.5">
            <Chip pose="idea" size={24} />
            <span className="text-h3 font-semibold text-fg">Suggested clips</span>
          </div>
          {engineClips.length === 0 ? (
            <p className="px-4 py-4 text-small text-subtle">
              No clip candidates yet — generate clips for this source from the Dashboard.
            </p>
          ) : (
            engineClips.map((c) => {
              const tier = fitTier(c.score)
              return (
                <div
                  key={c.id}
                  className="grid grid-cols-[auto_1fr_auto_auto] items-center gap-3.5 border-b border-default px-4 py-3.5 last:border-b-0"
                >
                  <span className="font-mono text-label text-subtle">{fmtClock(c.start_s)}</span>
                  <div>
                    <div className="text-small text-fg">{c.principle || 'Clip candidate'}</div>
                    <div className="text-label text-subtle">
                      {(c.end_s - c.start_s).toFixed(0)}s · Clip #{c.rank ?? '—'}
                    </div>
                  </div>
                  <span className="font-mono text-label" style={{ color: TIER_TEXT[tier] }}>
                    {TIER_LABEL[tier]}
                  </span>
                  <Button variant="ghost" size="sm" onClick={() => onOpenClip(c.id)}>
                    Open <ArrowRight className={`${ICON_SIZE.md} ${ICON_INLINE}`} aria-hidden="true" />
                  </Button>
                </div>
              )
            })
          )}
        </div>

        <FullTranscriptPanel
          transcript={transcriptQuery.data}
          isPending={transcriptQuery.isPending}
          isError={transcriptQuery.isError}
          onRetry={() => void transcriptQuery.refetch()}
          currentTime={currentTime}
          onSeek={seek}
          onClipSegment={(seg) => setPendingSelection({ start_s: seg.start_s, end_s: seg.end_s })}
        />
        </section>
      </div>

      {/* Right rail: chapters (functional) + export (UI only) */}
      <section
        data-tool-scroll
        aria-label="Chapters and export"
        className="flex min-h-0 flex-col gap-4 lg:overflow-y-auto"
      >
        <ChaptersPanel videoId={videoId} onChapters={setChapters} />

        {/* Export (Issue 373): real per-clip artifacts — every rendered clip
            (engine + your selections) downloads through the existing authed
            endpoint. A single-file source-edit export does not exist in the
            render pipeline; we say so instead of faking it (DECISIONS 2026-07-30). */}
        <div className="rounded-md border border-default bg-surface shadow-sm inset-shadow-highlight">
          <div className="border-b border-default px-4 py-3.5 text-h3 font-semibold text-fg">Export</div>
          <div className="flex flex-col gap-2.5 px-4 py-3.5">
            {ranked.filter((c) => c.render_status === 'done').length === 0 ? (
              <p className="text-small text-subtle">
                Nothing rendered yet — rendered clips show up here for download.
              </p>
            ) : (
              ranked
                .filter((c) => c.render_status === 'done')
                .map((c) => (
                  <div key={c.id} className="flex items-center justify-between gap-2.5">
                    <div className="min-w-0">
                      <div className="truncate text-small text-fg">
                        {c.origin === 'creator' ? 'Your selection' : `Clip #${c.rank ?? '—'}`}
                      </div>
                      <div className="font-mono text-label text-subtle">
                        {(c.end_s - c.start_s).toFixed(0)}s · {c.aspect} · MP4
                      </div>
                    </div>
                    <a
                      href={`/clips/${c.id}/download?disposition=attachment`}
                      className="shrink-0 rounded-sm border border-strong bg-bg px-2.5 py-1.5 text-xs text-muted hover:bg-elevated hover:text-fg"
                    >
                      Download
                    </a>
                  </div>
                ))
            )}
            <p className="border-t border-default pt-2.5 text-label text-subtle">
              Full source-edit export isn’t available — export individual rendered clips.
            </p>
          </div>
        </div>
      </section>
    </div>
  )
}
