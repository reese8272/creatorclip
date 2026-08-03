import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api'
import { fitTier } from '@/lib/fit'
import { fmtClock, parseClock } from '@/lib/timecode'
import type { FitTier } from '@/components/ui/fit-badge'
import { Chip } from '@/components/Chip'
import { Button } from '@/components/ui/button'
import { ChaptersPanel } from '@/components/analysis/ChaptersPanel'
import { FullTranscriptPanel } from '@/components/editor/FullTranscriptPanel'
import type { Chapter, ReviewClip, Video, VideoTranscript } from '@/types'
import { ArrowRight } from '@/components/ui/icon'
import { ICON_INLINE, ICON_SIZE } from '@/components/ui/iconSizes'

const TIER_LABEL: Record<FitTier, string> = {
  strong: 'Strong',
  moderate: 'Moderate',
  exploratory: 'Exploratory',
}
const TIER_SEGMENT: Record<FitTier, { background: string; borderColor: string }> = {
  strong: { background: 'oklch(20% 0.06 145 / 0.55)', borderColor: 'oklch(32% 0.09 145)' },
  moderate: { background: 'oklch(20% 0.05 75 / 0.5)', borderColor: 'oklch(32% 0.09 75)' },
  exploratory: { background: 'oklch(17% 0.01 285 / 0.6)', borderColor: 'var(--color-strong)' },
}
const TIER_TEXT: Record<FitTier, string> = {
  strong: 'oklch(72% 0.16 145)',
  moderate: 'oklch(78% 0.14 75)',
  exploratory: 'var(--color-muted)',
}

// A drag shorter than this reads as a click (segment open / stray tap), not a
// selection (Issue 373 — same threshold idea as the short editor's MIN_CUT_S).
const MIN_SELECT_S = 1.0

export interface SourceSelection {
  start_s: number
  end_s: number
}

// Master timeline: candidate clips drawn over a waveform placeholder, positioned
// by their source-relative start/end and coloured by fit tier. Issue 373 adds
// drag-to-select: dragging a range on the bar proposes a creator-made clip via
// onSelect (clicks on segments still open them — the 1s threshold disambiguates).
function MasterTimeline({
  clips,
  chapters,
  sourceDuration,
  onOpenClip,
  onSelect,
}: {
  clips: ReviewClip[]
  chapters: Chapter[]
  sourceDuration: number
  onOpenClip: (clipId: string) => void
  onSelect: (sel: SourceSelection) => void
}) {
  const dur = sourceDuration > 0 ? sourceDuration : 1
  const barRef = useRef<HTMLDivElement>(null)
  const [dragStart, setDragStart] = useState<number | null>(null)
  const [dragEnd, setDragEnd] = useState<number | null>(null)

  function xToTime(clientX: number): number {
    const el = barRef.current
    if (!el) return 0
    const rect = el.getBoundingClientRect()
    const frac = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width))
    return frac * dur
  }

  function finishDrag(clientX: number) {
    if (dragStart === null) return
    const t = xToTime(clientX)
    const lo = Math.min(dragStart, t)
    const hi = Math.max(dragStart, t)
    if (hi - lo >= MIN_SELECT_S) onSelect({ start_s: lo, end_s: hi })
    setDragStart(null)
    setDragEnd(null)
  }
  // Chapter ticks: drawn from real generated chapters (right-rail panel). Only
  // those that fall within the derived source span are positioned.
  const ticks = chapters
    .map((c) => ({ title: c.title, at: parseClock(c.timestamp_formatted) }))
    .filter((t) => t.at >= 0 && t.at <= dur)
  return (
    <div>
      <div className="mb-2 text-label uppercase tracking-[0.06em] text-muted">Source timeline</div>
      {ticks.length > 0 && (
        <div className="relative mb-1 h-4">
          {ticks.map((t, i) => (
            <span
              key={i}
              className="absolute -translate-x-1/2 whitespace-nowrap text-[10px] text-subtle"
              style={{ left: `${Math.min(100, (t.at / dur) * 100)}%` }}
              title={`${t.title} · ${fmtClock(t.at)}`}
            >
              {t.title}
            </span>
          ))}
        </div>
      )}
      <div className="relative overflow-hidden rounded-md border border-default bg-surface inset-shadow-highlight">
        <div
          ref={barRef}
          data-testid="master-timeline-bar"
          className="relative h-24 cursor-crosshair select-none"
          onMouseDown={(e) => {
            if (e.button !== 0) return
            setDragStart(xToTime(e.clientX))
            setDragEnd(null)
          }}
          onMouseMove={(e) => {
            if (dragStart !== null) setDragEnd(xToTime(e.clientX))
          }}
          onMouseUp={(e) => finishDrag(e.clientX)}
          onMouseLeave={(e) => {
            if (dragStart !== null && dragEnd !== null) finishDrag(e.clientX)
            else {
              setDragStart(null)
              setDragEnd(null)
            }
          }}
        >
          {/* chapter tick lines */}
          {ticks.map((t, i) => (
            <div
              key={i}
              className="absolute bottom-0 top-0 w-px bg-strong/70"
              style={{ left: `${Math.min(100, (t.at / dur) * 100)}%` }}
            />
          ))}
          {/* waveform placeholder */}
          <div className="absolute inset-0 flex items-center gap-px px-1.5">
            {Array.from({ length: 48 }, (_, i) => (
              <div
                key={i}
                className="flex-1 rounded-[1px] bg-strong/60"
                style={{ height: `${20 + ((i * 37) % 60)}%` }}
              />
            ))}
          </div>
          {/* candidate + creator segments */}
          {clips.map((c) => {
            const tier = fitTier(c.score)
            const isCreator = c.origin === 'creator'
            const left = `${Math.max(0, Math.min(100, (c.start_s / dur) * 100))}%`
            const width = `${Math.max(1.5, ((c.end_s - c.start_s) / dur) * 100)}%`
            return (
              <button
                key={c.id}
                onClick={() => onOpenClip(c.id)}
                title={
                  isCreator
                    ? `Open your selection at ${fmtClock(c.start_s)} in the clip editor`
                    : `Open clip at ${fmtClock(c.start_s)} in the clip editor`
                }
                aria-label={
                  isCreator
                    ? `Open your selection at ${fmtClock(c.start_s)}`
                    : `Open ${TIER_LABEL[tier]}-fit clip at ${fmtClock(c.start_s)}`
                }
                className="absolute bottom-0 top-0 cursor-pointer rounded-[3px] border"
                style={
                  isCreator
                    ? {
                        left,
                        width,
                        background: 'oklch(22% 0.04 285 / 0.5)',
                        borderColor: 'var(--color-accent)',
                        borderStyle: 'dashed',
                      }
                    : { left, width, ...TIER_SEGMENT[tier] }
                }
              />
            )
          })}
          {/* live drag-selection overlay */}
          {dragStart !== null && dragEnd !== null && (
            <div
              className="pointer-events-none absolute bottom-0 top-0 rounded-[3px] border border-accent bg-accent-soft/40"
              style={{
                left: `${(Math.min(dragStart, dragEnd) / dur) * 100}%`,
                width: `${(Math.abs(dragEnd - dragStart) / dur) * 100}%`,
              }}
            />
          )}
        </div>
        <div className="flex justify-between border-t border-default px-2 py-[5px] font-mono text-[10px] text-muted">
          <span>0:00</span>
          <span>{fmtClock(dur / 2)}</span>
          <span>{fmtClock(dur)}</span>
        </div>
      </div>
      <p className="mt-[5px] text-label text-subtle">
        Green = strong fit · Amber = moderate · Gray = exploratory · Dashed = your selection. Click a
        segment to open it, or drag an empty stretch to create your own clip.
      </p>
    </div>
  )
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
}: {
  clips: ReviewClip[]
  videoId: string
  /** The videos-list row (shared ['videos'] cache) — carries duration_s and
   *  clippable (source presence) for proactive retention honesty. */
  video?: Video
  onOpenClip: (clipId: string) => void
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
  const videoRef = useRef<HTMLVideoElement>(null)
  const [currentTime, setCurrentTime] = useState(0)
  const [streamError, setStreamError] = useState(false)

  function seek(t: number) {
    if (videoRef.current) {
      videoRef.current.currentTime = t
      setCurrentTime(t)
    }
  }

  // Retention honesty: clippable === false means the source was purged (or
  // never stored) — show the re-upload card proactively instead of a dead
  // player. A stream error (e.g. purge raced the page) lands the same way.
  const sourceAvailable = (video ? video.clippable : true) && !streamError

  // Real source duration (Issue 372): videos-list row → transcript span →
  // furthest clip end as the last-resort fallback so the timeline never
  // renders degenerate.
  const sourceDuration =
    video?.duration_s ??
    transcriptQuery.data?.duration_s ??
    clips.reduce((max, c) => Math.max(max, c.end_s), 0)

  return (
    <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
      <div className="flex flex-col gap-4">
        {sourceAvailable ? (
          <video
            ref={videoRef}
            src={`/videos/${videoId}/stream`}
            controls
            playsInline
            preload="metadata"
            onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
            onError={() => setStreamError(true)}
            className="aspect-video w-full rounded-xl border border-default bg-black"
          />
        ) : (
          <div className="flex aspect-video w-full flex-col items-center justify-center gap-3 rounded-xl border border-default bg-black/60 px-6 text-center text-sm text-subtle">
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
        <div className="flex items-center gap-3 rounded-md border border-accent-border bg-gradient-to-br from-accent-soft to-surface px-3.5 py-2.5">
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
        />

        {pendingSelection && (
          <CreateClipCard
            selection={pendingSelection}
            videoId={videoId}
            onClose={() => setPendingSelection(null)}
          />
        )}

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
      </div>

      {/* Right rail: chapters (functional) + export (UI only) */}
      <div className="flex flex-col gap-4">
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
      </div>
    </div>
  )
}
