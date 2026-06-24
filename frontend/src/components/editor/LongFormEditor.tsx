import { fitTier } from '@/lib/fit'
import type { FitTier } from '@/components/ui/fit-badge'
import { Chip } from '@/components/Chip'
import { Button } from '@/components/ui/button'
import { ChaptersPanel } from '@/components/analysis/ChaptersPanel'
import type { ReviewClip } from '@/types'

// H:MM:SS (or M:SS) for source-relative timecodes, which can run to hours.
function fmtClock(s: number): string {
  const sec = Math.max(0, Math.floor(s))
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  const ss = (sec % 60).toString().padStart(2, '0')
  return h > 0 ? `${h}:${m.toString().padStart(2, '0')}:${ss}` : `${m}:${ss}`
}

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

// Master timeline: candidate clips drawn over a waveform placeholder, positioned
// by their source-relative start/end and coloured by fit tier. Source duration is
// derived from the furthest clip end (no source-media endpoint — scaffold scope).
function MasterTimeline({
  clips,
  sourceDuration,
  onOpenClip,
}: {
  clips: ReviewClip[]
  sourceDuration: number
  onOpenClip: (clipId: string) => void
}) {
  const dur = sourceDuration > 0 ? sourceDuration : 1
  return (
    <div>
      <div className="mb-2 text-label uppercase tracking-[0.06em] text-muted">Source timeline</div>
      <div className="relative overflow-hidden rounded-md border border-default bg-surface shadow-inset">
        <div className="relative h-24">
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
          {/* candidate segments */}
          {clips.map((c) => {
            const tier = fitTier(c.score)
            const left = `${Math.max(0, Math.min(100, (c.start_s / dur) * 100))}%`
            const width = `${Math.max(1.5, ((c.end_s - c.start_s) / dur) * 100)}%`
            return (
              <button
                key={c.id}
                onClick={() => onOpenClip(c.id)}
                title={`Open clip at ${fmtClock(c.start_s)} in the clip editor`}
                aria-label={`Open ${TIER_LABEL[tier]}-fit clip at ${fmtClock(c.start_s)}`}
                className="absolute bottom-0 top-0 cursor-pointer rounded-[3px] border"
                style={{ left, width, ...TIER_SEGMENT[tier] }}
              />
            )
          })}
        </div>
        <div className="flex justify-between border-t border-default px-2 py-[5px] font-mono text-[10px] text-muted">
          <span>0:00</span>
          <span>{fmtClock(dur / 2)}</span>
          <span>{fmtClock(dur)}</span>
        </div>
      </div>
      <p className="mt-[5px] text-label text-subtle">
        Green = strong fit · Amber = moderate · Gray = exploratory. Click a segment to open it in the
        clip editor.
      </p>
    </div>
  )
}

// Issue 307 — Long-form source mode. Functional: candidate-segment master
// timeline + ranked suggested clips (both from the clips list, which carries
// source-relative timecodes) + chapters (existing generate stream). Honest
// placeholders for the full-source player + searchable transcript, which have no
// backend endpoint yet (scope: scaffold honestly — see docs/DECISIONS.md).
export function LongFormEditor({
  clips,
  videoId,
  onOpenClip,
}: {
  clips: ReviewClip[]
  videoId: string
  onOpenClip: (clipId: string) => void
}) {
  const sourceDuration = clips.reduce((max, c) => Math.max(max, c.end_s), 0)
  const ranked = [...clips].sort((a, b) => (a.rank ?? 999) - (b.rank ?? 999))

  return (
    <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
      <div className="flex flex-col gap-4">
        {/* Source player — honest placeholder (no full-source media endpoint yet) */}
        <div className="flex aspect-video w-full items-center justify-center rounded-xl border border-default bg-black/60 text-center text-sm text-subtle">
          <span className="px-6">
            Full-source preview isn’t available here yet — open a suggested clip below to refine it as
            a short.
          </span>
        </div>

        {/* Chip scan callout */}
        <div className="flex items-center gap-3 rounded-md border border-accent-border bg-gradient-to-br from-accent-soft to-surface px-3.5 py-2.5">
          <Chip pose="magnify" size={46} className="flex-shrink-0" />
          <div className="text-small leading-relaxed text-fg">
            <strong className="text-accent-text">Chip:</strong> I scanned your source and surfaced{' '}
            {clips.length} clip-worthy {clips.length === 1 ? 'moment' : 'moments'}. The strong-fit ones
            are highlighted below — open either to refine it as a short.
          </div>
        </div>

        <MasterTimeline clips={ranked} sourceDuration={sourceDuration} onOpenClip={onOpenClip} />

        {/* Suggested clips */}
        <div className="rounded-md border border-default bg-surface shadow-sm shadow-inset">
          <div className="flex items-center gap-2 border-b border-default px-4 py-3.5">
            <Chip pose="idea" size={24} />
            <span className="text-h3 font-semibold text-fg">Suggested clips</span>
          </div>
          {ranked.length === 0 ? (
            <p className="px-4 py-4 text-small text-subtle">
              No clip candidates yet — generate clips for this source from the Dashboard.
            </p>
          ) : (
            ranked.map((c) => {
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
                    Open →
                  </Button>
                </div>
              )
            })
          )}
        </div>

        {/* Full transcript — honest placeholder (no source-transcript endpoint yet) */}
        <div className="rounded-md border border-default bg-surface shadow-sm shadow-inset">
          <div className="flex items-center gap-2 border-b border-default px-4 py-3.5">
            <Chip pose="papers" size={24} />
            <span className="text-h3 font-semibold text-fg">Full transcript</span>
          </div>
          <p className="px-4 py-4 text-small leading-relaxed text-subtle">
            Searchable full-source transcript and “create clip from selection” are coming. For now,
            open a suggested clip to edit its transcript word-by-word in the short-form editor.
          </p>
        </div>
      </div>

      {/* Right rail: chapters (functional) + export (UI only) */}
      <div className="flex flex-col gap-4">
        <ChaptersPanel videoId={videoId} />

        <div className="rounded-md border border-default bg-surface shadow-sm shadow-inset">
          <div className="border-b border-default px-4 py-3.5 text-h3 font-semibold text-fg">Export</div>
          <div className="flex flex-col gap-3 px-4 py-3.5">
            <div className="flex items-center justify-between gap-2.5">
              <span className="text-small text-muted">Format</span>
              <span className="rounded-sm border border-strong bg-bg px-2.5 py-1.5 text-xs text-subtle">
                MP4
              </span>
            </div>
            <div className="flex items-center justify-between gap-2.5">
              <span className="text-small text-muted">Quality</span>
              <span className="rounded-sm border border-strong bg-bg px-2.5 py-1.5 text-xs text-subtle">
                1080p
              </span>
            </div>
            <Button variant="secondary" size="sm" disabled title="Full-source export is coming">
              Export source edit (coming soon)
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
