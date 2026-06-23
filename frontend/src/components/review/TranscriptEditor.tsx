import { Fragment, useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useCleanedUriPoll } from '@/hooks/useCleanedUriPoll'
import { Button } from '@/components/ui/button'
import type { ClipTranscript, EditorCut, ReviewClip, TranscriptWord } from '@/types'

const WARNING_REMOVED_PCT = 40
const ADJACENT_MERGE_S = 0.05

const storageKey = (clipId: string) => `clip:${clipId}:cuts`

function loadCuts(clipId: string): EditorCut[] {
  try {
    const raw = localStorage.getItem(storageKey(clipId))
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

// Sort by start, then merge cuts whose gap is within the click-merge tolerance.
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

function ancestorWord(node: Node | null): HTMLElement | null {
  let n: Node | null = node
  while (n && n !== document.body) {
    if (n.nodeType === 1 && (n as HTMLElement).classList?.contains('ed-word')) return n as HTMLElement
    n = n.parentNode
  }
  return null
}

// Snap the current text selection to whole-word [start,end] indices, or null.
function selectionToIndices(): [number, number] | null {
  const sel = window.getSelection()
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null
  const range = sel.getRangeAt(0)
  const startWord = ancestorWord(range.startContainer)
  let endWord = ancestorWord(range.endContainer)
  if (!endWord && range.endContainer.previousSibling)
    endWord = ancestorWord(range.endContainer.previousSibling)
  if (!startWord || !endWord) return null
  let i = Number(startWord.dataset.index)
  let j = Number(endWord.dataset.index)
  if (i > j) [i, j] = [j, i]
  return [i, j]
}

// Issue 135 — Descript-style transcript editor. Parent keys this by clip.id so
// it remounts (and reloads localStorage cuts) per clip.
export function TranscriptEditor({ clip }: { clip: ReviewClip }) {
  const queryClient = useQueryClient()
  const { data, isPending, isError } = useQuery({
    queryKey: ['transcript', clip.id],
    queryFn: () => api<ClipTranscript>(`/clips/${clip.id}/transcript`),
  })
  const [cuts, setCuts] = useState<EditorCut[]>(() => loadCuts(clip.id))
  const [undo, setUndo] = useState<EditorCut[] | null>(null)
  const [applying, setApplying] = useState(false)
  const [status, setStatus] = useState('')
  const cleanedUri = useCleanedUriPoll(clip.video_id, clip.id, applying)

  useEffect(() => {
    try {
      localStorage.setItem(storageKey(clip.id), JSON.stringify(cuts))
    } catch {
      /* quota — losing cuts on refresh is recoverable */
    }
  }, [cuts, clip.id])

  const words: TranscriptWord[] = data?.words ?? []
  const clipDuration = data?.clip_duration_s ?? clip.end_s - clip.start_s

  const cutIndices = new Set<number>()
  cuts.forEach((c) => {
    for (let i = c.indices[0]; i <= c.indices[1]; i++) cutIndices.add(i)
  })
  const removedS = cuts.reduce((acc, c) => acc + (c.end_s - c.start_s), 0)
  const pct = clipDuration > 0 ? (100 * removedS) / clipDuration : 0

  function onMouseUp() {
    const range = selectionToIndices()
    if (!range) return
    const [i, j] = range
    window.getSelection()?.removeAllRanges()
    if (cuts.some((c) => c.indices[0] <= i && c.indices[1] >= j)) return // already cut
    setUndo(cuts)
    setCuts(mergeAdjacent([...cuts, { start_s: words[i].start_s, end_s: words[j].end_s, indices: [i, j] }]))
  }

  function removeCut(idx: number) {
    setUndo(cuts)
    setCuts(cuts.filter((_, k) => k !== idx))
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

  if (isPending) return <p className="text-sm text-subtle">Loading transcript…</p>
  if (isError) return <p className="text-sm text-danger">Failed to load transcript — try refreshing.</p>

  return (
    <div>
      <p className="mb-2 text-xs text-subtle">
        Drag across words to mark them for removal. Click <strong>×</strong> on a queued cut to undo
        it.
      </p>

      <div
        role="textbox"
        aria-multiline="true"
        aria-readonly="true"
        aria-label="Clip transcript"
        onMouseUp={onMouseUp}
        className="max-h-[180px] select-text overflow-y-auto rounded-md border border-default bg-surface px-3 py-2 text-sm leading-[1.9]"
      >
        {words.map((w, i) => (
          <Fragment key={i}>
            {i > 0 && ' '}
            <span
              data-index={i}
              className={cn(
                'ed-word cursor-text rounded-sm px-px',
                cutIndices.has(i) && 'text-subtle line-through opacity-45',
              )}
            >
              {w.word}
            </span>
          </Fragment>
        ))}
      </div>

      <div className="mt-2 text-sm text-subtle">
        {cuts.length} cut(s) · would remove {removedS.toFixed(2)}s ({pct.toFixed(0)}%)
      </div>
      {pct >= WARNING_REMOVED_PCT && (
        <div className="text-sm font-semibold text-danger">⚠ This removes {pct.toFixed(0)}% of your clip.</div>
      )}

      <div className="mt-2 max-h-[140px] overflow-y-auto rounded-sm border border-default p-2 text-xs">
        {cuts.length === 0 ? (
          <span className="italic text-subtle">
            No pending cuts. Drag-select words to mark them for removal.
          </span>
        ) : (
          cuts.map((c, idx) => (
            <div key={idx} className="flex items-center justify-between border-b border-default py-1 last:border-b-0">
              <span className="text-subtle line-through">
                {words.slice(c.indices[0], c.indices[1] + 1).map((w) => w.word).join(' ').slice(0, 60)}{' '}
                <span className="font-mono text-xs">· {(c.end_s - c.start_s).toFixed(2)}s</span>
              </span>
              <button
                onClick={() => removeCut(idx)}
                aria-label="Remove cut"
                className="h-[22px] w-[22px] rounded-sm border border-strong text-muted hover:border-danger hover:text-danger"
              >
                ×
              </button>
            </div>
          ))
        )}
      </div>

      <div className="mt-2 flex flex-wrap gap-2">
        <Button variant="secondary" size="sm" disabled={!undo} onClick={() => { if (undo) { setCuts(undo); setUndo(null) } }}>
          Undo
        </Button>
        <Button variant="secondary" size="sm" onClick={() => { setUndo(cuts); setCuts([]); setStatus('Cleared all pending cuts.') }}>
          Clear all
        </Button>
        <Button size="sm" onClick={apply}>
          Apply
        </Button>
      </div>

      {cleanedUri && (
        <div className="mt-3">
          <video src={cleanedUri} controls className="w-full rounded-sm border border-default" />
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
  )
}
