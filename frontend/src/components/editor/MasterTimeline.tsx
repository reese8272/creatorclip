import { useState } from 'react'
import { TimelineRail } from '@/components/editor/TimelineRail'
import { Waveform } from '@/components/editor/Waveform'
import { fitTier } from '@/lib/fit'
import type { FitTier } from '@/components/ui/fit-badge'
import { fmtClock, parseClock } from '@/lib/timecode'
import { WAVEFORM_UNAVAILABLE_MESSAGE } from '@/lib/peaks'
import type { WaveformPeaks } from '@/lib/peaks'
import type { Chapter, ReviewClip } from '@/types'

// A drag shorter than this reads as a click (segment open / stray tap), not a
// selection (Issue 373 — same threshold idea as the short editor's MIN_CUT_S).
const MIN_SELECT_S = 1.0

export interface SourceSelection {
  start_s: number
  end_s: number
}

const TIER_LABEL: Record<FitTier, string> = {
  strong: 'Strong',
  moderate: 'Moderate',
  exploratory: 'Exploratory',
}

const TIER_SEGMENT: Record<FitTier, { background: string; borderColor: string }> = {
  strong: { background: 'oklch(30% 0.09 150 / 0.55)', borderColor: 'var(--color-fit-strong)' },
  moderate: { background: 'oklch(32% 0.09 75 / 0.5)', borderColor: 'var(--color-fit-moderate)' },
  exploratory: { background: 'oklch(28% 0.01 285 / 0.5)', borderColor: 'var(--color-fit-exploratory)' },
}

/**
 * MasterTimeline — the long-form source timeline (Issues 307/372/373, rebuilt 390).
 *
 * Extracted from LongFormEditor and rebuilt on the shared `TimelineRail`, so it
 * gets pointer/touch input, zoom, scroll and the adaptive ruler that the
 * short-form timeline has. That is not symmetry for its own sake: THIS is the
 * surface Issue 390 cites as its motivating evidence — a 22-minute source where
 * one pixel was roughly one second, which made precise work impossible.
 *
 * Candidate segments are `<button>`s painted over the waveform, so a drag that
 * starts on one has to not also open it. The rail handles that by suppressing
 * the trailing click after a real drag; a short press still falls through and
 * opens the clip.
 */
export function MasterTimeline({
  clips,
  chapters,
  sourceDuration,
  onOpenClip,
  onSelect,
  peaks,
}: {
  clips: ReviewClip[]
  chapters: Chapter[]
  sourceDuration: number
  onOpenClip: (clipId: string) => void
  onSelect: (sel: SourceSelection) => void
  /** Real audio peaks, or null → the honest flat track (Issue 392). */
  peaks: WaveformPeaks | null
}) {
  const dur = sourceDuration > 0 ? sourceDuration : 1
  const [preview, setPreview] = useState<{ from: number; to: number } | null>(null)

  // Chapter marks from the right-rail panel; only those inside the derived span.
  const ticks = chapters
    .map((c) => ({ title: c.title, at: parseClock(c.timestamp_formatted) }))
    .filter((t) => t.at >= 0 && t.at <= dur)

  return (
    <div>
      <div className="mb-2 text-label uppercase tracking-[0.06em] text-muted">Source timeline</div>

      <TimelineRail
        duration={dur}
        currentTime={0}
        cuts={[]}
        label="Source timeline"
        laneHeight={96}
        railTestId="master-timeline-bar"
        onDrag={(d) => setPreview({ from: d.fromTime, to: d.toTime })}
        onDragEnd={(d) => {
          setPreview(null)
          const lo = Math.min(d.fromTime, d.toTime)
          const hi = Math.max(d.fromTime, d.toTime)
          if (hi - lo >= MIN_SELECT_S) onSelect({ start_s: lo, end_s: hi })
        }}
      >
        {(viewport) => (
          <>
            <div className="pointer-events-none absolute inset-0 px-1.5 text-strong">
              <Waveform
                peaks={peaks}
                startS={0}
                endS={dur}
                ariaLabel={peaks ? 'Source audio waveform' : 'Waveform unavailable'}
              />
            </div>

            {/* Chapter marks live INSIDE the rail so they track zoom and scroll
                with everything else; as a separate row above it they would drift
                out of alignment the moment the timeline was zoomed. */}
            {ticks.map((t, i) => (
              <div
                key={`${t.at}-${i}`}
                aria-hidden="true"
                className="pointer-events-none absolute inset-y-0 w-px bg-strong/70"
                style={{ left: `${viewport.timeToX(t.at)}px` }}
              >
                <span className="absolute top-0 left-1 whitespace-nowrap text-label text-muted">
                  {t.title}
                </span>
              </div>
            ))}

            {clips.map((c) => {
              const tier = fitTier(c.score)
              const isCreator = c.origin === 'creator'
              const left = viewport.timeToX(c.start_s)
              // A floor of 3px keeps a very short candidate clickable on a
              // 22-minute source zoomed all the way out.
              const width = Math.max(3, viewport.timeToX(c.end_s) - left)
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
                          left: `${left}px`,
                          width: `${width}px`,
                          background: 'oklch(22% 0.04 285 / 0.5)',
                          borderColor: 'var(--color-accent)',
                          borderStyle: 'dashed',
                        }
                      : { left: `${left}px`, width: `${width}px`, ...TIER_SEGMENT[tier] }
                  }
                />
              )
            })}

            {preview && (
              <div
                aria-hidden="true"
                className="pointer-events-none absolute bottom-0 top-0 rounded-[3px] border border-accent bg-accent-soft/40"
                style={{
                  left: `${viewport.timeToX(Math.min(preview.from, preview.to))}px`,
                  width: `${Math.abs(viewport.timeToX(preview.to) - viewport.timeToX(preview.from))}px`,
                }}
              />
            )}
          </>
        )}
      </TimelineRail>

      <p className="mt-[5px] text-label text-subtle">
        Green = strong fit · Amber = moderate · Gray = exploratory · Dashed = your selection. Click a
        segment to open it, or drag an empty stretch to create your own clip.
      </p>
      {/* Say it outright when there is no audio data. A silent flat line could be
          mistaken for a silent source; this distinguishes "we don't have it" from
          "your audio is quiet" (Issue 392). */}
      {!peaks && <p className="mt-1 text-label text-subtle">{WAVEFORM_UNAVAILABLE_MESSAGE}</p>}
    </div>
  )
}
