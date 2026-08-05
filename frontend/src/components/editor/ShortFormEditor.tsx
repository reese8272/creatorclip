import { Fragment, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { STAGE_CELL } from '@/lib/toolLayout'
import { WAVEFORM_UNAVAILABLE_MESSAGE } from '@/lib/peaks'
import { Timeline } from '@/components/editor/Timeline'
import { Button } from '@/components/ui/button'
import { CaptionStylePanel } from '@/components/review/CaptionStylePanel'
import { CleanPassPanel } from '@/components/review/CleanPassPanel'
import { CollapsibleTool } from '@/components/review/CollapsibleTool'
import { Chip } from '@/components/Chip'
import { ShortStage } from '@/components/stage/ShortStage'
import { Switch } from '@/components/ui/switch'
import {
  cutFromRange,
  cutWordIndices,
  mergeAdjacent,
  MIN_CUT_S,
  removeCutById,
  toDocumentCuts,
  withIndices,
} from '@/lib/editorCuts'
import { SaveStatus } from '@/components/editor/SaveStatus'
import { useCleanedUriPoll } from '@/hooks/useCleanedUriPoll'
import { useEditDocument } from '@/hooks/useEditDocument'
import { useEditorShortcuts } from '@/hooks/useEditorShortcuts'
import { useVideoPeaks } from '@/hooks/useVideoPeaks'
import type {
  ClipTranscript,
  EditDocument,
  EditorCut,
  ReviewClip,
  TranscriptWord,
} from '@/types'
import { TriangleAlert, X } from '@/components/ui/icon'
import { ICON_SIZE } from '@/components/ui/iconSizes'
import type { VideoPlayerHandle } from '@/components/ui/video-player'

// ── Constants ────────────────────────────────────────────────────────────────

const WARNING_REMOVED_PCT = 40
const SNAP_PREF_KEY = 'editor:snap'

/** What the edit document looks like once `/clean/confirm` has emptied it. */
const EMPTY_EDIT_DOCUMENT: EditDocument = { version: 1, cuts: [], last_applied_at: null }

/** Snapping is on by default — it is what makes cuts land on word edges. */
function readSnapPreference(): boolean {
  try {
    return localStorage.getItem(SNAP_PREF_KEY) !== 'off'
  } catch {
    return true
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
  hasPeaks = false,
  className,
}: {
  clip: ReviewClip
  /** The parent video's `has_peaks`. Gating on it means a source that will never
   *  have a waveform costs zero requests instead of a 404 per clip open. */
  hasPeaks?: boolean
  className?: string
}) {
  const queryClient = useQueryClient()

  // ── Transcript ───────────────────────────────────────────────────────────

  const { data: transcriptData, isPending: txPending, isError: txError } = useQuery({
    queryKey: ['transcript', clip.id],
    queryFn: () => api<ClipTranscript>(`/clips/${clip.id}/transcript`),
  })

  // Memoised because the `?? []` fallback would otherwise mint a new array
  // every render, which would make the cut-span recomputation below re-run on
  // every keystroke instead of only when the transcript or the cuts change.
  const words: TranscriptWord[] = useMemo(() => transcriptData?.words ?? [], [transcriptData])
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

  // The document is server-authoritative (Issue 391). The parent keys this
  // component on clip.id, so switching clips REMOUNTS rather than resetting
  // state in an effect — which also fixes the cross-clip undo bug the old
  // single slot had.
  const {
    doc,
    saveState,
    saveError,
    conflict,
    canUndo,
    canRedo,
    commit,
    undo,
    redo,
    flush,
    retry,
    keepMine,
    takeTheirs,
    getRevision,
    adoptServerDocument,
  } = useEditDocument(clip.id)

  const [applying, setApplying] = useState(false)
  const [status, setStatus] = useState('')
  // Persisted per creator, not per clip: it is a working preference, and having
  // it reset every time you open a different clip would be its own annoyance.
  const [snapEnabled, setSnapEnabled] = useState(readSnapPreference)
  const [selectedCutId, setSelectedCutId] = useState<string | null>(null)
  // The in-point set by `I`, waiting for its `O`. Null means "no range open".
  const [inPoint, setInPoint] = useState<number | null>(null)

  // The word span is recomputed from the stored times rather than persisted —
  // the transcript is server-owned and mutable, so a stored span would go stale
  // silently and could index past the end of `words`.
  const cuts: EditorCut[] = useMemo(() => withIndices(doc.cuts, words), [doc.cuts, words])

  /** Replace the cut list as one undoable step. */
  function setCuts(next: EditorCut[]) {
    commit((d) => ({ ...d, cuts: toDocumentCuts(next) }))
  }

  const cleanedUri = useCleanedUriPoll(clip.video_id, clip.id, applying)

  // ── Cut management ───────────────────────────────────────────────────────

  function addTimeCut(start_s: number, end_s: number) {
    setCuts(mergeAdjacent([...cuts, cutFromRange(words, start_s, end_s)]))
  }

  function removeCut(id: string) {
    setCuts(removeCutById(cuts, id))
  }

  function toggleSnap(next: boolean) {
    setSnapEnabled(next)
    try {
      localStorage.setItem(SNAP_PREF_KEY, next ? 'on' : 'off')
    } catch {
      // A preference that cannot be persisted still works for this session;
      // there is nothing to recover and nothing to report.
    }
  }

  // Editing keys bind HERE because this component owns the cuts; zoom and
  // scrubbing bind in TimelineRail, which owns the viewport. Both go through the
  // one shortcut bus rather than each adding a document listener — two listeners
  // racing is how a single keypress deletes two cuts.
  //
  // I/O is the cross-NLE convention (Kdenlive documents I and O for frame-
  // accurate in/out): I marks the in-point, O closes the range into a cut.
  useEditorShortcuts({
    i: () => {
      setInPoint(currentTime)
      setStatus(`In point at ${currentTime.toFixed(2)}s — press O to close the range.`)
      return true
    },
    o: () => {
      if (inPoint === null) {
        setStatus('Press I to set an in point first.')
        return true
      }
      const lo = Math.min(inPoint, currentTime)
      const hi = Math.max(inPoint, currentTime)
      setInPoint(null)
      if (hi - lo < MIN_CUT_S) {
        setStatus('That range is too short to cut.')
        return true
      }
      addTimeCut(lo, hi)
      setStatus('')
      return true
    },
    s: () => (toggleSnap(!snapEnabled), true),
    Delete: () => deleteSelected(),
    Backspace: () => deleteSelected(),
    // Undo/redo bind on the SAME bus as the editing keys rather than adding a
    // second document listener — two listeners racing is how one keypress
    // undoes twice. `shortcutKey` already normalises Cmd/Ctrl to `mod` and
    // lowercases the key, which is what makes Shift+Cmd+Z (where e.key is 'Z')
    // match. Ctrl+Y is the Windows convention for redo.
    'mod+z': () => (undo(), true),
    'mod+shift+z': () => (redo(), true),
    'mod+y': () => (redo(), true),
  })

  function deleteSelected(): boolean {
    if (!selectedCutId) return false
    removeCut(selectedCutId)
    setSelectedCutId(null)
    return true
  }

  /**
   * Commit an edge drag. Merging happens HERE, once, rather than during the
   * gesture — absorbing a neighbour mid-drag would retire the id the pointer is
   * holding, and the drag would silently jump to a different cut.
   */
  function applyCutEdit(next: EditorCut[]) {
    setCuts(mergeAdjacent(next))
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
    setCuts(mergeAdjacent([...cuts, cutFromRange(words, start_s, end_s)]))
  }

  async function apply() {
    if (!cuts.length) {
      setStatus('No cuts to apply.')
      return
    }
    setStatus('Submitting cuts…')
    // AWAITED, not fire-and-forget. The server renders whatever `base_revision`
    // points at, so returning while the matching PUT is still in flight would
    // spend a paid render on the document as it was BEFORE the last edits.
    await flush()
    try {
      // The cut list is no longer sent. The server reads its own document at
      // this revision, which is what makes "what I exported" and "what I last
      // saved" the same thing by construction rather than by hoping the two
      // agreed. A mismatch comes back as 409 stale_revision.
      await api(`/clips/${clip.id}/cuts`, {
        method: 'POST',
        body: { base_revision: getRevision() },
      })
      setApplying(true)
      setStatus('Editing your clip — come back in ~20s.')
    } catch (e) {
      const err = e as { code?: string; message?: string }
      setStatus(
        err.code === 'stale_revision'
          ? 'Your edit changed somewhere else. Reload the page, then export again.'
          : err.message || 'Submit failed — try again.',
      )
    }
  }

  async function confirmFinal() {
    try {
      const resp = await api<{ edit_revision: number | null }>(
        `/clips/${clip.id}/clean/confirm`,
        { method: 'POST' },
      )
      // Confirming BAKES the edit into the render, so the SERVER clears the
      // document — leaving it would make the next export cut the same spans a
      // second time out of an already-shortened render. We adopt the revision it
      // reports rather than clearing locally, because the server's bump would
      // otherwise 409 our next autosave against a change we caused ourselves.
      // (`clean/discard` deliberately does NOT clear — the creator rejected that
      // render, and their cuts still describe an unapplied edit.)
      if (resp.edit_revision !== null) {
        adoptServerDocument(EMPTY_EDIT_DOCUMENT, resp.edit_revision)
      }
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

  return (
    <div className={cn('flex flex-col gap-3', className)}>
      {/* Three regions at lg: evidence (transcript) · the STAGE · actions.
          DOM order is mobile-first (stage → transcript → options); explicit
          col starts place them transcript | stage | options at lg.
          grid-rows-[minmax(0,1fr)] is the grid analogue of min-h-0 — without it
          a track can grow to its content and overflow the clipped shell. */}
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 lg:grid-cols-[minmax(280px,1.4fr)_minmax(0,1fr)_minmax(272px,340px)] lg:grid-rows-[minmax(0,1fr)]">
        {/* ── B · The stage — the single primary panel (L2). STAGE_CELL makes
            this cell a size container so the stage derives the media size from
            the cell's own height budget (the Issue 424 sizing inversion) —
            the Timeline dock below simply shrinks the budget, no re-measured
            subtraction constant involved. */}
        <section
          aria-label="Clip preview"
          className={cn(
            'flex min-h-0 justify-center lg:col-start-2 lg:row-start-1 lg:overflow-y-auto',
            STAGE_CELL,
          )}
        >
          <ShortStage
            clip={clip}
            // compact: scrubbing belongs to the Timeline dock below, not to a
            // full transport bar competing with it.
            density="compact"
            transport
            playerRef={playerRef}
            onTimeChange={setCurrentTime}
            // cleanedUri is the readiness signal only; playback goes through
            // the authed download endpoint (the raw URI is s3:// in prod R2).
            // The in-stage tab swap replaces the old second stacked player.
            cleanedUri={cleanedUri}
            onConfirmCleaned={confirmFinal}
            onDiscardCleaned={discardFinal}
            statusLine={status || undefined}
          />
        </section>

        {/* ── A · Transcript — the evidence panel ── */}
        <section
          aria-label="Clip transcript"
          className="flex min-h-0 flex-col gap-2 lg:col-start-1 lg:row-start-1 lg:overflow-hidden"
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

        {/* ── C · Render options — the actions panel ── */}
        <section
          data-tool-scroll
          aria-label="Render options"
          className="flex min-h-0 flex-col gap-4 lg:col-start-3 lg:row-start-1 lg:overflow-y-auto"
        >
          <h2 className="text-xs font-medium uppercase tracking-[0.06em] text-muted">
            Render options
          </h2>
          {/* Collapsed by default (Issue 425): the strongest lever for making a
              secondary panel recede is not being open — the Editor-side answer
              to "captioning takes too much space". */}
          <CollapsibleTool title="Caption style">
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
            onCutsChange={applyCutEdit}
            words={words}
            snapEnabled={snapEnabled}
            selectedCutId={selectedCutId}
            onSelectCut={setSelectedCutId}
            peaks={peaks}
            sourceStartS={sourceStartS}
          />
          {/* The long-form timeline says this outright; before Issue 410 the
              short-form rail only said it via ariaLabel, so sighted users saw
              an unexplained empty band. */}
          {!peaks && (
            <p className="mt-1 text-label text-subtle">{WAVEFORM_UNAVAILABLE_MESSAGE}</p>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Button variant="secondary" size="sm" disabled={!canUndo} onClick={undo}>
              Undo
            </Button>
            <Button variant="secondary" size="sm" disabled={!canRedo} onClick={redo}>
              Redo
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
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
            <label className="ml-auto flex shrink-0 items-center gap-2 text-small text-muted">
              <Switch checked={snapEnabled} onCheckedChange={toggleSnap} aria-label="Snap to words" />
              Snap to words
            </label>
          </div>
        </div>

        <div className="flex min-h-0 flex-col gap-1">
          <SaveStatus
            state={saveState}
            error={saveError}
            conflict={conflict}
            onRetry={retry}
            onKeepMine={keepMine}
            onTakeTheirs={takeTheirs}
            className="shrink-0"
          />
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
