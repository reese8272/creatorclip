import { Fragment, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { fitTier } from '@/lib/fit'
import { EDITOR_PLAYER_W } from '@/lib/toolLayout'
import { Timeline } from '@/components/editor/Timeline'
import { FitBadge } from '@/components/ui/fit-badge'
import { Button } from '@/components/ui/button'
import { CaptionStylePanel } from '@/components/review/CaptionStylePanel'
import { CleanPassPanel } from '@/components/review/CleanPassPanel'
import { CollapsibleTool } from '@/components/review/CollapsibleTool'
import { Chip } from '@/components/Chip'
import {
  cutFromRange,
  cutWordIndices,
  mergeAdjacent,
  normalizeCut,
  removeCutById,
} from '@/lib/editorCuts'
import { useCleanedUriPoll } from '@/hooks/useCleanedUriPoll'
import { useVideoPeaks } from '@/hooks/useVideoPeaks'
import type { ClipTranscript, EditorCut, ReviewClip, TranscriptWord } from '@/types'
import { ArrowLeft, TriangleAlert, X } from '@/components/ui/icon'
import { ICON_INLINE, ICON_SIZE } from '@/components/ui/iconSizes'
import { VideoPlayer, type VideoPlayerHandle } from '@/components/ui/video-player'
import { Card } from '@/components/ui/card'

// ── Constants ────────────────────────────────────────────────────────────────

const WARNING_REMOVED_PCT = 40
const storageKey = (clipId: string) => `clip:${clipId}:cuts`

// ── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Read persisted cuts, repairing anything a previous version wrote.
 *
 * Every cut stored before Issue 390 lacks an `id`; `normalizeCut` backfills one
 * and drops entries that cannot be a cut at all, so one NaN in the blob cannot
 * brick the editor.
 */
function loadCuts(clipId: string): EditorCut[] {
  try {
    const raw = localStorage.getItem(storageKey(clipId))
    const parsed = raw ? JSON.parse(raw) : []
    if (!Array.isArray(parsed)) return []
    return parsed.map(normalizeCut).filter((c): c is EditorCut => c !== null)
  } catch {
    return []
  }
}

// ── Transcript word hit-detection for playhead sync ──────────────────────────

function activeWordIndex(words: TranscriptWord[], currentTime: number): number {
  for (let i = 0; i < words.length; i++) {
    if (currentTime >= words[i].start_s && currentTime <= words[i].end_s) return i
  }
  return -1
}

/**
 * ShortFormEditor — the single-clip refine surface.
 *
 * Extracted from Editor.tsx in Issue 389, mirroring LongFormEditor. Editor.tsx is
 * now route plumbing and the mode toggle; this owns all short-form cut state and
 * the workspace layout. The split is deliberate batch sequencing: Issues 390
 * (Timeline v2), 391 (edit document) and 392 (real waveform peaks) all rewrite
 * this surface, and before the split they would all have rewritten the same
 * 700-line Editor.tsx.
 *
 * The waveform is real server-computed peaks for the parent source, windowed to
 * this clip (Issue 392). It replaced a client-side WebAudio decode of the
 * rendered mp4.
 */
export function ShortFormEditor({
  clip,
  videoId,
  hasPeaks = false,
  className,
}: {
  clip: ReviewClip
  videoId: string
  /** The parent video's `has_peaks`. Gating on it means a source that will never
   *  have a waveform costs zero requests instead of a 404 per clip open. */
  hasPeaks?: boolean
  className?: string
}) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  // ── Transcript ───────────────────────────────────────────────────────────

  const { data: transcriptData, isPending: txPending, isError: txError } = useQuery({
    queryKey: ['transcript', clip.id],
    queryFn: () => api<ClipTranscript>(`/clips/${clip.id}/transcript`),
  })

  const words: TranscriptWord[] = transcriptData?.words ?? []
  const clipDuration = transcriptData?.clip_duration_s ?? clip.end_s - clip.start_s

  // ── Playhead state ───────────────────────────────────────────────────────

  const playerRef = useRef<VideoPlayerHandle>(null)
  // Page-level time state is retained deliberately: it is what the transcript
  // highlight and Timeline read today, and it is behaviourally identical to the
  // old onTimeUpdate wiring. Pushing the subscription down into those components
  // is #390's job — doing it here would collide with its prop rewrite.
  const [currentTime, setCurrentTime] = useState(0)

  function handleSeek(t: number) {
    playerRef.current?.seek(t)
    setCurrentTime(t)
  }

  // ── Waveform ─────────────────────────────────────────────────────────────

  // Issue 392 replaced a client-side WebAudio decode here. That path fetched the
  // ENTIRE rendered mp4 with credentials on every clip switch, buffered it,
  // decodeAudioData'd it and built a Float32Array of every sample (~8 MB for a
  // 40s clip) in a main-thread loop, while racing the <video> element for the
  // same bytes. The server now precomputes an ~8-bit envelope once per source.
  //
  // The peaks belong to the SOURCE, so the timeline windows them to this clip's
  // span. The render origin is setup_start_s when set — the engine starts the
  // clip at the setup — so that, not start_s, is the window offset.
  const { peaks } = useVideoPeaks(clip.video_id, hasPeaks)
  const sourceStartS = clip.setup_start_s ?? clip.start_s

  // ── Cut state ────────────────────────────────────────────────────────────

  const [cuts, setCuts] = useState<EditorCut[]>(() => loadCuts(clip.id))
  const [undo, setUndo] = useState<EditorCut[] | null>(null)
  const [applying, setApplying] = useState(false)
  const [status, setStatus] = useState('')

  // Re-load cuts when the clip changes.
  useEffect(() => {
    // Intentional: reset local cut state to the newly-selected clip's persisted
    // cuts. The canonical alternative is a `key` on this subtree (parent-owned);
    // tracked for the lint sweep in OFF_COURSE_BUGS.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setCuts(loadCuts(clip.id))
  }, [clip.id])

  // Persist cuts so they survive a reload. Issue 391 replaces this with a
  // server-side edit document; until then localStorage IS the source of truth,
  // which is the defect that issue exists to fix.
  useEffect(() => {
    try {
      localStorage.setItem(storageKey(clip.id), JSON.stringify(cuts))
    } catch {
      /* quota — recoverable */
    }
  }, [cuts, clip.id])

  const cleanedUri = useCleanedUriPoll(clip.video_id, clip.id, applying)

  // ── Cut management ───────────────────────────────────────────────────────

  function addTimeCut(start_s: number, end_s: number) {
    setUndo(cuts)
    setCuts(mergeAdjacent([...cuts, cutFromRange(words, start_s, end_s)]))
  }

  function removeCut(id: string) {
    setUndo(cuts)
    setCuts(removeCutById(cuts, id))
  }

  function onTranscriptMouseUp() {
    const sel = window.getSelection()
    if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return
    const range = sel.getRangeAt(0)
    const startEl = ancestorWord(range.startContainer)
    let endEl = ancestorWord(range.endContainer)
    if (!endEl && range.endContainer.previousSibling) {
      endEl = ancestorWord(range.endContainer.previousSibling)
    }
    if (!startEl || !endEl) return
    let i = Number(startEl.dataset.index)
    let j = Number(endEl.dataset.index)
    if (i > j) [i, j] = [j, i]
    window.getSelection()?.removeAllRanges()
    const start_s = words[i]?.start_s ?? 0
    const end_s = words[j]?.end_s ?? 0
    if (end_s <= start_s) return
    setUndo(cuts)
    setCuts(mergeAdjacent([...cuts, cutFromRange(words, start_s, end_s)]))
  }

  async function apply() {
    if (!cuts.length) {
      setStatus('No cuts to apply.')
      return
    }
    setStatus('Submitting cuts…')
    try {
      await api(`/clips/${clip.id}/cuts`, {
        method: 'POST',
        body: { segments: cuts.map((c) => ({ start_s: c.start_s, end_s: c.end_s })) },
      })
      setApplying(true)
      setStatus('Editing your clip — come back in ~20s.')
    } catch (e) {
      const detail = (e as { message?: string }).message
      setStatus(detail || 'Submit failed — try again.')
    }
  }

  async function confirmFinal() {
    try {
      await api(`/clips/${clip.id}/clean/confirm`, { method: 'POST' })
      try {
        localStorage.removeItem(storageKey(clip.id))
      } catch {
        /* ignore */
      }
      setCuts([])
      setApplying(false)
      setStatus('Edited version is now the main render.')
      queryClient.invalidateQueries({ queryKey: ['review-clips', clip.video_id] })
    } catch {
      setStatus('Swap failed — try again.')
    }
  }

  // Issue 364: discard must clear cleaned_render_uri server-side, or the next
  // clean/edit 409s with pending_clean_or_edit until a version the creator
  // explicitly rejected is confirmed instead.
  async function discardFinal() {
    try {
      await api(`/clips/${clip.id}/clean/discard`, { method: 'POST' })
      setApplying(false)
      setStatus('Keeping original render.')
      queryClient.invalidateQueries({ queryKey: ['review-clips', clip.video_id] })
    } catch {
      setStatus('Discard failed — try again.')
    }
  }

  // ── Cut computation helpers ──────────────────────────────────────────────

  const cutIndices = cutWordIndices(cuts)
  const removedS = cuts.reduce((acc, c) => acc + (c.end_s - c.start_s), 0)
  const pct = clipDuration > 0 ? (100 * removedS) / clipDuration : 0
  const activeIdx = activeWordIndex(words, currentTime)

  const mediaSrc = `/clips/${clip.id}/download?disposition=inline`

  return (
    <div className={cn('flex flex-col gap-3', className)}>
      {/* Three regions at lg: evidence (transcript) · the media · actions.
          grid-rows-[minmax(0,1fr)] is the grid analogue of min-h-0 — without it
          an `auto` track grows to its content and overflows the clipped shell. */}
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 lg:grid-cols-[minmax(280px,1.4fr)_auto_minmax(272px,340px)] lg:grid-rows-[minmax(0,1fr)]">
        {/* ── A · Transcript — the evidence panel ── */}
        <section
          aria-label="Clip transcript"
          className="flex min-h-0 flex-col gap-2 lg:overflow-hidden"
        >
          {/* The engine's case for this clip sits with the evidence, not on the
              player. In a height-constrained column every pixel spent beside the
              media comes straight out of the player's height. */}
          {clip.reasoning && (
            <p className="flex shrink-0 items-start gap-2 rounded-md border border-default bg-surface px-3 py-2 text-small leading-relaxed text-muted inset-shadow-highlight">
              <Chip pose="think" size={26} className="mt-0.5 flex-shrink-0" />
              <span>{clip.reasoning}</span>
            </p>
          )}
          <h2 className="flex shrink-0 items-center gap-2 text-xs font-medium uppercase tracking-[0.06em] text-muted">
            <Chip pose="papers" size={22} />
            Transcript
          </h2>
          {txError && (
            <p className="text-xs text-danger">
              Couldn’t load the transcript — cuts by word selection are unavailable until it loads.
              Try reopening the clip in a moment.
            </p>
          )}
          {words.length === 0 && !txPending && !txError && (
            <p className="text-xs text-subtle">No transcript available for this clip.</p>
          )}
          {words.length > 0 && (
            <div
              data-tool-scroll
              role="textbox"
              aria-multiline="true"
              aria-readonly="true"
              aria-label="Clip transcript — drag to select words for removal"
              // tabIndex is required, not decorative: this scroll region holds only
              // plain spans, so without it axe's scrollable-region-focusable fails
              // at SERIOUS impact (WCAG 2.1.1) and both tool routes are gated on it.
              // Before 389 the region was capped at 200px and only passed because
              // the test fixture was two words long.
              tabIndex={0}
              onMouseUp={onTranscriptMouseUp}
              // bg-bg inside a panel is an L0 WELL — a scroll region reads as recessed on
              // dark by dropping a rung, not by adding a shadow.
              // Below lg the page scrolls, so the region keeps a viewport-relative
              // cap (it replaced a hard max-h-[200px]); at lg the flex row owns
              // its height and the cap must come off or it fights min-h-0.
              className="max-h-[45vh] min-h-0 flex-1 select-text overflow-y-auto rounded-md border border-default bg-bg px-3 py-2 text-body leading-[1.9] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent lg:max-h-none"
            >
              {words.map((w, i) => (
                <Fragment key={i}>
                  {i > 0 && ' '}
                  <span
                    data-index={i}
                    className={cn(
                      'ed-word cursor-text rounded-sm px-px transition-colors',
                      cutIndices.has(i) && 'text-subtle line-through opacity-45',
                      i === activeIdx && !cutIndices.has(i) && 'bg-accent-soft text-accent-text',
                    )}
                  >
                    {w.word}
                  </span>
                </Fragment>
              ))}
            </div>
          )}
        </section>

        {/* ── B · Viewer — the single primary panel (L2), on an `auto` track ──
            The dead region is gone because the track is `auto` and the card is
            `w-fit`: the card is exactly as wide as the player, so there is no
            enclosed empty space to frame. Everything that is not the media went
            elsewhere — the case for the clip to column A, the transport to the
            dock — because in a height-constrained column, pixels spent next to
            the player come out of the player. */}
        <section aria-label="Clip preview" className="flex min-h-0 justify-center lg:overflow-y-auto">
          <Card level="primary" className="flex h-fit w-fit flex-col gap-2 p-3">
            {clip.render_uri ? (
              <VideoPlayer
                ref={playerRef}
                // Keyed on the artifact, not just the clip: a confirmed clean
                // swap changes render_uri but not the download src, so without
                // a key change the element would keep playing the old media.
                mediaKey={clip.render_uri ?? undefined}
                src={mediaSrc}
                label="Clip preview"
                // compact: scrubbing belongs to the Timeline dock below, not to a
                // full transport bar competing with it.
                density="compact"
                transport
                onTimeChange={setCurrentTime}
                className={cn(EDITOR_PLAYER_W, 'shadow-accent-glow')}
              />
            ) : (
              <div
                className={cn(
                  EDITOR_PLAYER_W,
                  'flex aspect-[9/16] items-center justify-center rounded-xl border border-default bg-black text-xs text-subtle',
                )}
              >
                Not yet rendered
              </div>
            )}

            {/* One compact row, not a stack: a ~110px meta column under the
                player would cost the player ~110px of height, and height is what
                sets its width at 9:16. */}
            <div
              className={cn(
                EDITOR_PLAYER_W,
                'flex shrink-0 flex-wrap items-center justify-between gap-x-2 gap-y-1',
              )}
            >
              <span className="font-mono text-mono text-muted">
                Clip #{clip.rank ?? '—'} ·{' '}
                {(clip.end_s - (clip.setup_start_s ?? clip.start_s)).toFixed(1)}s
              </span>
              <FitBadge tier={fitTier(clip.score)} />
              <Button
                variant="ghost"
                size="sm"
                onClick={() => navigate(`/review?video_id=${videoId}`)}
              >
                <ArrowLeft className={`${ICON_SIZE.md} ${ICON_INLINE}`} aria-hidden="true" /> Back to
                Review
              </Button>
            </div>

            {/* cleanedUri is the readiness signal only; playback goes through
                the authed download endpoint (the raw URI is s3:// in prod R2). */}
            {cleanedUri && (
              <div className={cn(EDITOR_PLAYER_W, 'flex flex-col gap-2')}>
                <VideoPlayer
                  src={`/clips/${clip.id}/download?variant=cleaned&disposition=inline`}
                  label="Edited clip preview"
                  density="compact"
                  className="w-full"
                />
                <div className="flex gap-2">
                  <Button size="sm" onClick={confirmFinal}>
                    Use edited version
                  </Button>
                  <Button variant="secondary" size="sm" onClick={discardFinal}>
                    Keep original
                  </Button>
                </div>
              </div>
            )}

            {status && <div className={cn(EDITOR_PLAYER_W, 'text-xs text-subtle')}>{status}</div>}
          </Card>
        </section>

        {/* ── C · Render options — the actions panel ── */}
        <section
          data-tool-scroll
          aria-label="Render options"
          className="flex min-h-0 flex-col gap-4 lg:overflow-y-auto"
        >
          <h2 className="text-xs font-medium uppercase tracking-[0.06em] text-muted">
            Render options
          </h2>
          <CollapsibleTool title="Caption style" defaultOpen>
            <CaptionStylePanel clip={clip} />
          </CollapsibleTool>
          <CollapsibleTool title="Clean filler + silence">
            <CleanPassPanel clip={clip} />
          </CollapsibleTool>
        </section>
      </div>

      {/* ── Timeline dock — full width, pinned below the regions ──
          Full-bleed rather than trapped in a 1fr column: at 1440px this is a
          ~1100px timeline instead of ~630px, which is also the width Issue 390's
          zoom-to-fit and playhead-in-view arithmetic measures against. */}
      <div className="grid shrink-0 grid-cols-1 gap-4 border-t border-default pt-3 lg:grid-cols-[minmax(0,1fr)_280px]">
        <div className="min-w-0">
          <Timeline
            duration={clipDuration}
            currentTime={currentTime}
            cuts={cuts}
            onSeek={handleSeek}
            onSelection={({ start_s, end_s }) => addTimeCut(start_s, end_s)}
            peaks={peaks}
            sourceStartS={sourceStartS}
          />
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              disabled={!undo}
              onClick={() => {
                if (undo) {
                  setCuts(undo)
                  setUndo(null)
                }
              }}
            >
              Undo
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                setUndo(cuts)
                setCuts([])
                setStatus('Cleared all pending cuts.')
              }}
            >
              Clear all
            </Button>
            <Button size="sm" onClick={apply} disabled={applying}>
              {applying ? 'Applying…' : 'Apply cuts'}
            </Button>
            {/* Content the creator must actually read: on the semantic scale and
                on text-muted, not 10px text-subtle. */}
            <span className="text-small text-muted">Click to seek · Drag to mark a cut region</span>
          </div>
        </div>

        <div className="flex min-h-0 flex-col gap-1">
          <div className="shrink-0 text-small text-muted">
            {cuts.length} cut(s) · {removedS.toFixed(2)}s removed ({pct.toFixed(0)}%)
          </div>
          {pct >= WARNING_REMOVED_PCT && (
            <div className="flex shrink-0 items-center gap-1.5 text-xs font-semibold text-danger">
              <TriangleAlert className={`${ICON_SIZE.xs} shrink-0`} aria-hidden="true" />
              This removes {pct.toFixed(0)}% of your clip.
            </div>
          )}
          {cuts.length > 0 && (
            /* No tabIndex needed — the Remove-cut buttons already make this
               scroll region keyboard-reachable (scrollable-region-focusable). */
            <div
              data-tool-scroll
              className="max-h-[30vh] min-h-0 flex-1 overflow-y-auto rounded-sm border border-default bg-bg p-2 text-xs lg:max-h-none"
            >
              {cuts.map((c, idx) => (
                <div
                  key={c.id}
                  className="flex items-center justify-between border-b border-default py-1 last:border-b-0"
                >
                  <span className="text-subtle line-through">
                    {c.indices
                      ? words
                          .slice(c.indices[0], c.indices[1] + 1)
                          .map((w) => w.word)
                          .join(' ')
                          .slice(0, 60)
                      : 'Selected range'}{' '}
                    <span className="font-mono">· {(c.end_s - c.start_s).toFixed(2)}s</span>
                  </span>
                  <button
                    onClick={() => removeCut(c.id)}
                    aria-label={`Remove cut ${idx + 1}`}
                    className="inline-flex h-[22px] w-[22px] items-center justify-center rounded-sm border border-strong text-muted hover:border-danger hover:text-danger"
                  >
                    <X className={ICON_SIZE.sm} aria-hidden="true" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── DOM helpers ──────────────────────────────────────────────────────────────

function ancestorWord(node: Node | null): HTMLElement | null {
  let n: Node | null = node
  while (n && n !== document.body) {
    if (n.nodeType === 1 && (n as HTMLElement).classList?.contains('ed-word')) return n as HTMLElement
    n = n.parentNode
  }
  return null
}
