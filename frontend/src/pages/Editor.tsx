import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { fitTier } from '@/lib/fit'
import { DisclaimerBand } from '@/components/DisclaimerBand'
import { Timeline } from '@/components/editor/Timeline'
import { FitBadge } from '@/components/ui/fit-badge'
import { Button } from '@/components/ui/button'
import { CaptionStylePanel } from '@/components/review/CaptionStylePanel'
import { CleanPassPanel } from '@/components/review/CleanPassPanel'
import { CollapsibleTool } from '@/components/review/CollapsibleTool'
import { useCleanedUriPoll } from '@/hooks/useCleanedUriPoll'
import type {
  ClipTranscript,
  EditorCut,
  ReviewClip,
  ReviewClipListResponse,
  TranscriptWord,
} from '@/types'

// ── Constants ────────────────────────────────────────────────────────────────

const ADJACENT_MERGE_S = 0.05
const WARNING_REMOVED_PCT = 40
const storageKey = (clipId: string) => `clip:${clipId}:cuts`

// ── Helpers ──────────────────────────────────────────────────────────────────

function loadCuts(clipId: string): EditorCut[] {
  try {
    const raw = localStorage.getItem(storageKey(clipId))
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function mergeAdjacent(cuts: EditorCut[]): EditorCut[] {
  const sorted = cuts.slice().sort((a, b) => a.start_s - b.start_s)
  if (!sorted.length) return sorted
  const out = [sorted[0]]
  for (let k = 1; k < sorted.length; k++) {
    const last = out[out.length - 1]
    const cur = sorted[k]
    if (cur.start_s <= last.end_s + ADJACENT_MERGE_S) {
      last.end_s = Math.max(last.end_s, cur.end_s)
      last.indices = [last.indices[0], Math.max(last.indices[1], cur.indices[1])]
    } else {
      out.push(cur)
    }
  }
  return out
}

/** Snap a cut time-range to the nearest enclosing word indices. */
function timeRangeToIndices(
  words: TranscriptWord[],
  startS: number,
  endS: number,
): [number, number] | null {
  let first = -1
  let last = -1
  for (let i = 0; i < words.length; i++) {
    const w = words[i]
    if (w.start_s <= endS && w.end_s >= startS) {
      if (first === -1) first = i
      last = i
    }
  }
  if (first === -1) return null
  return [first, last]
}

// ── Transcript word hit-detection for playhead sync ──────────────────────────

function activeWordIndex(words: TranscriptWord[], currentTime: number): number {
  for (let i = 0; i < words.length; i++) {
    if (currentTime >= words[i].start_s && currentTime <= words[i].end_s) return i
  }
  return -1
}

// ── Editor page ──────────────────────────────────────────────────────────────

/**
 * Editor — Issue 188.
 *
 * Full-timeline single-clip edit surface:
 *  - Top: preview player (9:16 or chosen aspect)
 *  - Center: Timeline (waveform + playhead + cut overlays)
 *  - Below timeline: synced transcript (active word highlighted; drag-select → cut)
 *  - Right rail: Caption style + Clean pass tools (relocated from Review)
 *  - Footer: Apply cuts → existing submit_cuts / validate_user_cuts path
 *
 * This page is opened via the "Refine →" button added to Review.tsx for the
 * current clip. It does NOT replace Review; it deepens the edit for a clip the
 * creator has already decided to keep.
 *
 * Waveform rendering: client-side WebAudio decode (fetch the clip media, decode
 * via AudioContext.decodeAudioData, down-mix to mono Float32Array). This works
 * without any backend waveform asset, satisfying the AC without a staged ffmpeg
 * environment. The backend ffmpeg showwavespic path is deferred (render-env).
 */
export function Editor() {
  const [params] = useSearchParams()
  const videoId = params.get('video_id')
  const clipId = params.get('clip_id')
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  // ── Clip data ────────────────────────────────────────────────────────────

  const { data: clipsData, isPending: clipsPending } = useQuery({
    queryKey: ['review-clips', videoId],
    queryFn: () => api<ReviewClipListResponse>(`/videos/${videoId}/clips`),
    enabled: !!videoId,
  })

  const clip: ReviewClip | undefined = clipId
    ? clipsData?.clips.find((c) => c.id === clipId)
    : clipsData?.clips[0]

  // ── Transcript ───────────────────────────────────────────────────────────

  const { data: transcriptData, isPending: txPending } = useQuery({
    queryKey: ['transcript', clip?.id ?? ''],
    queryFn: () => api<ClipTranscript>(`/clips/${clip!.id}/transcript`),
    enabled: !!clip,
  })

  const words: TranscriptWord[] = transcriptData?.words ?? []
  const clipDuration = transcriptData?.clip_duration_s ?? (clip ? clip.end_s - clip.start_s : 0)

  // ── Playhead state ───────────────────────────────────────────────────────

  const videoRef = useRef<HTMLVideoElement>(null)
  const [currentTime, setCurrentTime] = useState(0)

  function handleTimeUpdate() {
    if (videoRef.current) setCurrentTime(videoRef.current.currentTime)
  }

  function handleSeek(t: number) {
    if (videoRef.current) {
      videoRef.current.currentTime = t
      setCurrentTime(t)
    }
  }

  // ── Waveform (client-side WebAudio decode) ───────────────────────────────

  const [waveformData, setWaveformData] = useState<Float32Array | null>(null)

  const decodeWaveform = useCallback(async (mediaSrc: string) => {
    try {
      const resp = await fetch(mediaSrc, { credentials: 'include' })
      if (!resp.ok) return
      const buf = await resp.arrayBuffer()
      // AudioContext is only available in browser; vitest jsdom stubs it.
      const AudioCtx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
      if (!AudioCtx) return
      const audioCtx = new AudioCtx()
      const decoded = await audioCtx.decodeAudioData(buf)
      // Down-mix all channels to mono by averaging.
      const length = decoded.length
      const mono = new Float32Array(length)
      for (let ch = 0; ch < decoded.numberOfChannels; ch++) {
        const channel = decoded.getChannelData(ch)
        for (let i = 0; i < length; i++) mono[i] += channel[i]
      }
      for (let i = 0; i < length; i++) mono[i] /= decoded.numberOfChannels
      setWaveformData(mono)
      await audioCtx.close()
    } catch {
      // Waveform decode failure is non-fatal — placeholder is shown instead.
    }
  }, [])

  useEffect(() => {
    if (!clip) return
    const src = `/clips/${clip.id}/download?disposition=inline`
    decodeWaveform(src)
  }, [clip, decodeWaveform])

  // ── Cut state (mirrors TranscriptEditor, shares localStorage key) ────────

  const [cuts, setCuts] = useState<EditorCut[]>(() => (clip ? loadCuts(clip.id) : []))
  const [undo, setUndo] = useState<EditorCut[] | null>(null)
  const [applying, setApplying] = useState(false)
  const [status, setStatus] = useState('')

  // Re-load cuts when the clip changes.
  useEffect(() => {
    if (clip) setCuts(loadCuts(clip.id))
  }, [clip?.id])

  // Persist cuts to localStorage so TranscriptEditor in Review stays in sync.
  useEffect(() => {
    if (!clip) return
    try {
      localStorage.setItem(storageKey(clip.id), JSON.stringify(cuts))
    } catch {
      /* quota — recoverable */
    }
  }, [cuts, clip?.id])

  const cleanedUri = useCleanedUriPoll(clip?.video_id ?? '', clip?.id ?? '', applying)

  // ── Cut management ───────────────────────────────────────────────────────

  function addTimeCut(start_s: number, end_s: number) {
    const idxRange = timeRangeToIndices(words, start_s, end_s)
    const newCut: EditorCut = {
      start_s,
      end_s,
      indices: idxRange ?? [0, 0],
    }
    setUndo(cuts)
    setCuts(mergeAdjacent([...cuts, newCut]))
  }

  function removeCut(idx: number) {
    setUndo(cuts)
    setCuts(cuts.filter((_, k) => k !== idx))
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
    setCuts(
      mergeAdjacent([...cuts, { start_s, end_s, indices: [i, j] }]),
    )
  }

  async function apply() {
    if (!clip || !cuts.length) {
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
    if (!clip) return
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

  // ── Cut computation helpers ──────────────────────────────────────────────

  const cutIndices = new Set<number>()
  cuts.forEach((c) => {
    for (let i = c.indices[0]; i <= c.indices[1]; i++) cutIndices.add(i)
  })
  const removedS = cuts.reduce((acc, c) => acc + (c.end_s - c.start_s), 0)
  const pct = clipDuration > 0 ? (100 * removedS) / clipDuration : 0
  const activeIdx = activeWordIndex(words, currentTime)

  // ── Guard states ────────────────────────────────────────────────────────

  function message(text: string) {
    return (
      <>
        <DisclaimerBand>
          AutoClip predicts fit with your style and audience — it does not promise virality. All
          scores are estimates grounded in your own channel data.
        </DisclaimerBand>
        <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-10">
          <p className="text-center text-sm text-muted">{text}</p>
        </main>
      </>
    )
  }

  if (!videoId || !clipId) return message('No clip selected — open from the Review page.')
  if (clipsPending || txPending) return message('Loading…')
  if (!clip) return message('Clip not found.')

  const mediaSrc = `/clips/${clip.id}/download?disposition=inline`

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <>
      <DisclaimerBand>
        AutoClip predicts fit with your style and audience — it does not promise virality. All scores
        are estimates grounded in your own channel data.
      </DisclaimerBand>

      <main className="mx-auto grid w-full max-w-6xl flex-1 grid-cols-1 gap-6 px-4 py-6 lg:grid-cols-[1fr_320px]">
        {/* ── Left: player + timeline + transcript ── */}
        <div className="flex flex-col gap-4">
          {/* Player */}
          <div className="flex items-start gap-4">
            {clip.render_uri ? (
              <video
                ref={videoRef}
                key={clip.id}
                src={mediaSrc}
                controls
                playsInline
                onTimeUpdate={handleTimeUpdate}
                className="aspect-[9/16] w-[180px] shrink-0 rounded-xl border border-default bg-black shadow-accent-glow"
              />
            ) : (
              <div className="flex aspect-[9/16] w-[180px] shrink-0 items-center justify-center rounded-xl border border-default bg-black text-xs text-subtle">
                Not yet rendered
              </div>
            )}

            {/* Clip meta + fit badge */}
            <div className="flex flex-col gap-3 pt-2">
              <div className="text-center font-mono text-xs text-muted">
                Clip #{clip.rank ?? '—'} ·{' '}
                {(clip.end_s - (clip.setup_start_s ?? clip.start_s)).toFixed(1)}s
              </div>
              <FitBadge tier={fitTier(clip.score)} />
              <p className="text-xs text-muted">{clip.reasoning}</p>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => navigate(`/review?video_id=${videoId}`)}
              >
                ← Back to Review
              </Button>
            </div>
          </div>

          {/* Timeline */}
          <div>
            <h2 className="mb-2 text-xs font-medium uppercase tracking-[0.06em] text-muted">
              Timeline
            </h2>
            <Timeline
              duration={clipDuration}
              currentTime={currentTime}
              cuts={cuts}
              onSeek={handleSeek}
              onSelection={({ start_s, end_s }) => addTimeCut(start_s, end_s)}
              waveformData={waveformData}
            />
            <p className="mt-1 text-[10px] text-subtle">
              Click to seek · Drag to mark a cut region
            </p>
          </div>

          {/* Transcript synced to playhead */}
          <div>
            <h2 className="mb-2 text-xs font-medium uppercase tracking-[0.06em] text-muted">
              Transcript
            </h2>
            {words.length === 0 && !txPending && (
              <p className="text-xs text-subtle">No transcript available for this clip.</p>
            )}
            {words.length > 0 && (
              <div
                role="textbox"
                aria-multiline="true"
                aria-readonly="true"
                aria-label="Clip transcript — drag to select words for removal"
                onMouseUp={onTranscriptMouseUp}
                className="max-h-[200px] select-text overflow-y-auto rounded-md border border-default bg-surface px-3 py-2 text-sm leading-[1.9]"
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

            {/* Cut queue */}
            <div className="mt-2 text-xs text-subtle">
              {cuts.length} cut(s) · {removedS.toFixed(2)}s removed ({pct.toFixed(0)}%)
            </div>
            {pct >= WARNING_REMOVED_PCT && (
              <div className="text-xs font-semibold text-danger">
                ⚠ This removes {pct.toFixed(0)}% of your clip.
              </div>
            )}

            {cuts.length > 0 && (
              <div className="mt-2 max-h-[120px] overflow-y-auto rounded-sm border border-default p-2 text-xs">
                {cuts.map((c, idx) => (
                  <div
                    key={idx}
                    className="flex items-center justify-between border-b border-default py-1 last:border-b-0"
                  >
                    <span className="text-subtle line-through">
                      {words
                        .slice(c.indices[0], c.indices[1] + 1)
                        .map((w) => w.word)
                        .join(' ')
                        .slice(0, 60)}{' '}
                      <span className="font-mono">· {(c.end_s - c.start_s).toFixed(2)}s</span>
                    </span>
                    <button
                      onClick={() => removeCut(idx)}
                      aria-label="Remove cut"
                      className="h-[22px] w-[22px] rounded-sm border border-strong text-muted hover:border-danger hover:text-danger"
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
            )}

            {/* Cut actions */}
            <div className="mt-2 flex flex-wrap gap-2">
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
            </div>

            {cleanedUri && (
              <div className="mt-3">
                <video
                  src={cleanedUri}
                  controls
                  className="w-full rounded-sm border border-default"
                />
                <div className="mt-2 flex gap-2">
                  <Button size="sm" onClick={confirmFinal}>
                    Use edited version
                  </Button>
                  <Button variant="secondary" size="sm" onClick={() => setApplying(false)}>
                    Keep original
                  </Button>
                </div>
              </div>
            )}

            {status && <div className="mt-2 text-xs text-subtle">{status}</div>}
          </div>
        </div>

        {/* ── Right rail: caption + clean tools (relocated from Review) ── */}
        <div className="flex flex-col gap-4">
          <h2 className="text-xs font-medium uppercase tracking-[0.06em] text-muted">
            Render options
          </h2>
          <CollapsibleTool title="Caption style" defaultOpen>
            <CaptionStylePanel clip={clip} />
          </CollapsibleTool>
          <CollapsibleTool title="Clean filler + silence">
            <CleanPassPanel clip={clip} />
          </CollapsibleTool>
        </div>
      </main>
    </>
  )
}

// ── DOM helpers ──────────────────────────────────────────────────────────────

function ancestorWord(node: Node | null): HTMLElement | null {
  let n: Node | null = node
  while (n && n !== document.body) {
    if (n.nodeType === 1 && (n as HTMLElement).classList?.contains('ed-word'))
      return n as HTMLElement
    n = n.parentNode
  }
  return null
}
