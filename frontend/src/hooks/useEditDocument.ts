import { useQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { ApiError, api } from '@/lib/api'
import {
  type EditHistory,
  canRedo as canRedoOf,
  canUndo as canUndoOf,
  commit as commitTo,
  initHistory,
  redo as redoOf,
  undo as undoOf,
} from '@/lib/editCommands'
import { clearLegacyCuts, readCache, readLegacyCuts, writeCache } from '@/lib/editDocCache'
import { createSaveScheduler } from '@/lib/saveScheduler'
import type { DocumentProducer } from '@/lib/editCommands'
import type { EditDocument, EditDocumentResponse } from '@/types'

// Server-side edit persistence + autosave (Issue 391).
//
// THE QUERY IS THE SEED, NOT THE STATE. This deliberately does NOT use the
// `invalidateQueries`-in-`onSuccess` idiom that `useApplyClipMetadata` uses and
// that the TanStack docs recommend. That idiom is correct for a one-shot,
// user-committed write; autosave is its inverse. Between a PUT leaving and its
// 200 arriving the creator has typically made two more edits, so refetching
// would replace the cache with the state as of the PREVIOUS save and visibly
// revert them — localStorage-as-source-of-truth reintroduced over the network.
// So: the query hydrates once, the command stack's `present` is live, and the
// response's revision goes into a ref. The server document is never written
// back into the present.

export type SaveState = 'idle' | 'dirty' | 'saving' | 'saved' | 'conflict' | 'error'

/** Backoff ladder for transient save failures, in ms. */
const BACKOFF_MS = [1000, 2000, 4000, 10_000]

/** A 429 is a different failure from a network blip and deserves a longer wait. */
const RATE_LIMIT_BACKOFF_MS = 15_000

const EMPTY_DOC: EditDocument = { version: 1, cuts: [], last_applied_at: null }

export interface ConflictInfo {
  serverDoc: EditDocument
  serverRevision: number
}

interface Seed {
  history: EditHistory
  saveState: SaveState
  conflict: ConflictInfo | null
  revision: number
  /** True when legacy localStorage cuts were folded in and must be saved up. */
  importedLegacy: boolean
}

/**
 * Decide the starting document from the server response + what this browser has.
 *
 * Pure, so it can be computed during render instead of assigned in an effect.
 */
function deriveSeed(clipId: string, data: EditDocumentResponse): Seed {
  // THE REVISION-0 GUARD IS THE ANTI-RESURRECTION RULE. A stored row is always
  // at revision >= 1, so revision 0 is the only state in which importing a
  // legacy browser-local cut list is safe. If the creator cleared their cuts on
  // device B, the server holds `cuts: []` at revision >= 1 and this branch never
  // runs — device A's stale blob is discarded rather than merged back in.
  const legacy = data.revision === 0 ? readLegacyCuts(clipId) : null
  if (legacy) {
    return {
      history: initHistory({ ...data.doc, cuts: legacy }),
      saveState: 'dirty',
      conflict: null,
      revision: data.revision,
      importedLegacy: true,
    }
  }

  const cached = readCache(clipId)
  if (cached?.dirty && cached.revision === data.revision) {
    // Unsaved work from this device, and the server has not moved on. Offer it
    // as an explicit choice rather than merging it or silently dropping it.
    return {
      history: initHistory(data.doc),
      saveState: 'conflict',
      conflict: { serverDoc: cached.doc, serverRevision: data.revision },
      revision: data.revision,
      importedLegacy: false,
    }
  }

  return {
    history: initHistory(data.doc),
    saveState: 'idle',
    conflict: null,
    revision: data.revision,
    importedLegacy: false,
  }
}

export interface UseEditDocumentResult {
  doc: EditDocument
  isLoading: boolean
  saveState: SaveState
  /** Set when saveState is 'error' — a human-readable reason. */
  saveError: string | null
  /** Set when saveState is 'conflict'; the creator must choose. */
  conflict: ConflictInfo | null
  canUndo: boolean
  canRedo: boolean
  commit: (produce: DocumentProducer) => void
  undo: () => void
  redo: () => void
  /** Force a save now (Apply/Export, tab hidden, unmount). */
  flush: () => void
  /** Retry after a terminal error, at the creator's request. */
  retry: () => void
  /** Conflict resolution — explicit, never automatic. */
  keepMine: () => void
  takeTheirs: () => void
}

export function useEditDocument(clipId: string): UseEditDocumentResult {
  const query = useQuery({
    queryKey: ['edit-document', clipId],
    queryFn: () => api<EditDocumentResponse>(`/clips/${clipId}/edit-document`),
    // Hydrate once. Pinned locally rather than left to the 30s global because
    // here it is CORRECTNESS, not a preference: a background refetch would race
    // the creator's in-flight edits. See the note at the top of this file.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnMount: false,
    retry: 1,
  })

  // HYDRATION IS DERIVED, NOT ASSIGNED IN AN EFFECT. The seed is a pure function
  // of the server response plus what is in localStorage, so it is computed
  // during render and the creator's own edits override it once they exist.
  // Copying it into state via an effect would make a second source of truth for
  // the same value. The caller remounts this hook per clip (`key={clip.id}`), so
  // the overrides never have to be invalidated by clip id.
  const seed = useMemo(
    () => (query.data ? deriveSeed(clipId, query.data) : null),
    [clipId, query.data],
  )

  const [editedHistory, setEditedHistory] = useState<EditHistory | null>(null)
  const [stateOverride, setStateOverride] = useState<SaveState | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  // `undefined` = untouched (use the seed's); `null` = explicitly resolved.
  const [conflictOverride, setConflictOverride] = useState<ConflictInfo | null | undefined>(
    undefined,
  )

  const history = editedHistory ?? seed?.history ?? null
  const saveState: SaveState = stateOverride ?? seed?.saveState ?? 'idle'
  const conflict = conflictOverride === undefined ? (seed?.conflict ?? null) : conflictOverride

  // Everything the save path reads without wanting to re-create itself. The
  // revision in particular advances on every successful save, and rebuilding
  // the scheduler each time would drop a pending timer.
  const revisionRef = useRef(0)
  const historyRef = useRef<EditHistory | null>(null)
  const conflictRef = useRef<ConflictInfo | null>(null)
  const inFlightRef = useRef(false)
  const pendingAfterFlightRef = useRef(false)
  const attemptRef = useRef(0)
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const clipIdRef = useRef(clipId)

  // Latest-value refs are written in an effect, never during render — a render
  // can be discarded, and a ref written during one would survive it.
  useEffect(() => {
    historyRef.current = history
    conflictRef.current = conflict
    clipIdRef.current = clipId
  })

  // The seed's revision is the base for the first save of this clip. Later
  // saves advance it themselves, so this must not clobber those — hence the
  // dependency on the seed identity rather than on every render.
  useEffect(() => {
    if (seed) revisionRef.current = seed.revision
  }, [seed])

  const doc = history?.present ?? query.data?.doc ?? EMPTY_DOC

  // ── Save ───────────────────────────────────────────────────────────────────

  // `save` is an ordinary function redefined each render and published to a ref
  // in an effect — the same latest-ref shape `useEditorShortcuts` uses. It is
  // NOT a useCallback: it needs to re-enter itself (the pending-after-flight
  // re-fire) and to be reachable from a timer, and a self-referencing
  // useCallback cannot be memoised. Going through the ref also means the
  // scheduler never has to be rebuilt when the save closure changes, which
  // would silently drop a pending timer.
  const saveRef = useRef<() => Promise<void>>(async () => {})

  function handleSaveFailure(e: unknown, id: string, current: EditDocument) {
    // The document is not lost — keep the local copy flagged dirty so a reload
    // can offer it back rather than silently discarding the work.
    writeCache(id, current, { dirty: true, revision: revisionRef.current })

    if (e instanceof ApiError && e.status === 409) {
      // A stale revision is an EXPLICIT USER CHOICE, never an auto-merge:
      // merging two divergent cut lists is how you produce edits nobody made.
      // The 409 body carries the server's document so no second GET is needed.
      const detail = e.detail as { revision?: number; doc?: EditDocument } | undefined
      setConflictOverride({
        serverDoc: detail?.doc ?? EMPTY_DOC,
        serverRevision: detail?.revision ?? 0,
      })
      setStateOverride('conflict')
      // The server has spoken, so the legacy key can go — see clearLegacyCuts.
      clearLegacyCuts(id)
      return
    }

    if (e instanceof ApiError && e.status === 422) {
      // Terminal: the client produced a document the server will never take.
      // Retrying is pure waste and would hide the bug behind a spinner.
      setSaveError('This edit could not be saved. Reload the page to continue.')
      setStateOverride('error')
      return
    }

    const attempt = attemptRef.current
    const isRateLimited = e instanceof ApiError && e.status === 429
    if (attempt >= BACKOFF_MS.length) {
      setSaveError(
        isRateLimited
          ? 'Still saving too often. Wait a moment, then retry.'
          : 'Could not save your edit. Check your connection, then retry.',
      )
      setStateOverride('error')
      return
    }
    attemptRef.current = attempt + 1
    const delay = isRateLimited ? RATE_LIMIT_BACKOFF_MS : BACKOFF_MS[attempt]
    setSaveError(isRateLimited ? 'Saving too often — retrying shortly.' : 'Save failed — retrying.')
    setStateOverride('error')
    retryTimerRef.current = setTimeout(() => void saveRef.current(), delay)
  }

  async function save() {
    const current = historyRef.current?.present
    if (!current) return

    // SINGLE FLIGHT. Two concurrent PUTs would 409 against each other — one
    // would necessarily carry a stale base_revision — so a save that starts
    // while another is in flight is recorded and re-fired on settle with the
    // revision the first one produced. Designed out, not discovered.
    if (inFlightRef.current) {
      pendingAfterFlightRef.current = true
      return
    }
    inFlightRef.current = true
    setStateOverride('saving')

    const id = clipIdRef.current
    try {
      const resp = await api<EditDocumentResponse>(`/clips/${id}/edit-document`, {
        method: 'PUT',
        body: { base_revision: revisionRef.current, doc: current },
      })
      revisionRef.current = resp.revision
      attemptRef.current = 0
      setSaveError(null)
      setStateOverride('saved')
      writeCache(id, current, { dirty: false, revision: resp.revision })
      clearLegacyCuts(id)
    } catch (e) {
      handleSaveFailure(e, id, current)
    } finally {
      inFlightRef.current = false
      if (pendingAfterFlightRef.current) {
        pendingAfterFlightRef.current = false
        void saveRef.current()
      }
    }
  }

  useEffect(() => {
    saveRef.current = save
  })

  // ── Scheduling and flush points ────────────────────────────────────────────

  // The scheduler is created in an effect, not a memo: it owns timers, so it is
  // a subscription, and building it during render would leak one per discarded
  // render. Callers reach it through the stable wrappers below.
  const schedulerRef = useRef<ReturnType<typeof createSaveScheduler> | null>(null)

  useEffect(() => {
    const s = createSaveScheduler(() => void saveRef.current())
    schedulerRef.current = s

    // `visibilitychange → hidden` is the last event a page can reliably observe
    // (MDN), which `beforeunload` is not — especially on mobile Safari.
    const onHidden = () => {
      if (document.visibilityState === 'hidden') s.flush()
    }
    document.addEventListener('visibilitychange', onHidden)

    const retryTimer = retryTimerRef
    return () => {
      document.removeEventListener('visibilitychange', onHidden)
      // Unmount is a flush point: the creator navigating away must not lose the
      // last 800ms of work.
      s.flush()
      schedulerRef.current = null
      if (retryTimer.current !== null) clearTimeout(retryTimer.current)
    }
  }, [])

  const schedule = useCallback(() => schedulerRef.current?.schedule(), [])
  const flush = useCallback(() => schedulerRef.current?.flush(), [])

  // A legacy import is unsaved work by definition — get it to the server.
  useEffect(() => {
    if (seed?.importedLegacy) schedule()
  }, [seed, schedule])

  // ── Actions ────────────────────────────────────────────────────────────────

  const applyHistory = useCallback(
    (next: EditHistory) => {
      setEditedHistory(next)
      writeCache(clipIdRef.current, next.present, {
        dirty: true,
        revision: revisionRef.current,
      })
      // An unresolved conflict outranks 'dirty': the creator still has a choice
      // to make, and overwriting the state would hide the prompt.
      if (!conflictRef.current) setStateOverride('dirty')
      schedule()
    },
    [schedule],
  )

  const commit = useCallback(
    (produce: DocumentProducer) => {
      const h = historyRef.current
      if (!h) return
      const next = commitTo(h, produce)
      if (next === h) return
      applyHistory(next)
    },
    [applyHistory],
  )

  const undo = useCallback(() => {
    const h = historyRef.current
    if (h) applyHistory(undoOf(h))
  }, [applyHistory])

  const redo = useCallback(() => {
    const h = historyRef.current
    if (h) applyHistory(redoOf(h))
  }, [applyHistory])

  const retry = useCallback(() => {
    attemptRef.current = 0
    void saveRef.current()
  }, [])

  const keepMine = useCallback(() => {
    // Adopt the server's revision so the next compare-and-set succeeds, while
    // keeping OUR document as the content.
    const c = conflictRef.current
    if (c) revisionRef.current = c.serverRevision
    setConflictOverride(null)
    attemptRef.current = 0
    setStateOverride('dirty')
    schedule()
  }, [schedule])

  const takeTheirs = useCallback(() => {
    const c = conflictRef.current
    if (!c) return
    revisionRef.current = c.serverRevision
    setEditedHistory(initHistory(c.serverDoc))
    writeCache(clipIdRef.current, c.serverDoc, { dirty: false, revision: c.serverRevision })
    setConflictOverride(null)
    attemptRef.current = 0
    setStateOverride('idle')
  }, [])

  return {
    doc,
    isLoading: query.isPending,
    saveState,
    saveError,
    conflict,
    canUndo: history ? canUndoOf(history) : false,
    canRedo: history ? canRedoOf(history) : false,
    commit,
    undo,
    redo,
    flush,
    retry,
    keepMine,
    takeTheirs,
  }
}
