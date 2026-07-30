import { useState } from 'react'
import { Chip } from '@/components/Chip'
import { QueryErrorState } from '@/components/QueryErrorState'
import { fmtClock } from '@/lib/timecode'
import type { VideoTranscript, VideoTranscriptSegment } from '@/types'

// Full-source transcript panel (Issue 372): search + click-to-seek over the
// segment-granular video transcript. Presentational — the query lives in
// LongFormEditor (which also needs duration_s for the master timeline).
// The active segment follows the source player's playhead.
export function FullTranscriptPanel({
  transcript,
  isPending,
  isError,
  onRetry,
  currentTime,
  onSeek,
  onClipSegment,
}: {
  transcript: VideoTranscript | undefined
  isPending: boolean
  isError: boolean
  onRetry: () => void
  currentTime: number
  onSeek: (t: number) => void
  /** Issue 373: pre-fill the create-clip popover from a segment. Optional so
   *  the panel renders standalone before that wiring exists. */
  onClipSegment?: (seg: VideoTranscriptSegment) => void
}) {
  const [query, setQuery] = useState('')
  const segments = transcript?.segments ?? []
  const q = query.trim().toLowerCase()
  const visible = q ? segments.filter((s) => s.text.toLowerCase().includes(q)) : segments

  return (
    <div className="rounded-md border border-default bg-surface shadow-sm shadow-inset">
      <div className="flex items-center gap-2 border-b border-default px-4 py-3.5">
        <Chip pose="papers" size={24} />
        <span className="text-h3 font-semibold text-fg">Full transcript</span>
      </div>

      {isPending ? (
        <p className="px-4 py-4 text-small text-subtle">Loading transcript…</p>
      ) : isError ? (
        <QueryErrorState
          title="Couldn’t load the transcript."
          onRetry={onRetry}
          className="m-4"
        />
      ) : segments.length === 0 ? (
        <p className="px-4 py-4 text-small leading-relaxed text-subtle">
          {transcript?.message ??
            'This video hasn’t been transcribed yet — transcripts are generated during analysis.'}
        </p>
      ) : (
        <>
          <div className="border-b border-default px-4 py-2.5">
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search the transcript…"
              aria-label="Search the transcript"
              className="h-9 w-full rounded-sm border border-strong bg-bg px-3 text-small text-fg placeholder:text-subtle focus:border-accent focus:outline-none"
            />
          </div>
          <div className="max-h-80 overflow-y-auto">
            {visible.length === 0 ? (
              <p className="px-4 py-4 text-small text-subtle">No matches for “{query}”.</p>
            ) : (
              visible.map((seg) => {
                const active = currentTime >= seg.start_s && currentTime < seg.end_s
                return (
                  <div
                    key={seg.index}
                    className={`group flex items-start gap-3 border-b border-default px-4 py-2 last:border-b-0 ${
                      active ? 'bg-accent-soft' : 'hover:bg-elevated'
                    }`}
                  >
                    <button
                      onClick={() => onSeek(seg.start_s)}
                      title="Jump the player to this moment"
                      className="mt-0.5 shrink-0 font-mono text-label text-subtle hover:text-accent-text"
                    >
                      {fmtClock(seg.start_s)}
                    </button>
                    <button
                      onClick={() => onSeek(seg.start_s)}
                      className={`flex-1 text-left text-small leading-relaxed ${
                        active ? 'text-fg' : 'text-muted'
                      }`}
                    >
                      {seg.text}
                    </button>
                    {onClipSegment && (
                      <button
                        onClick={() => onClipSegment(seg)}
                        title="Create a clip from this segment"
                        className="invisible shrink-0 rounded-sm border border-strong bg-bg px-2 py-0.5 text-[10px] text-muted hover:bg-elevated hover:text-fg group-hover:visible"
                      >
                        Clip this
                      </button>
                    )}
                  </div>
                )
              })
            )}
          </div>
        </>
      )}
    </div>
  )
}
