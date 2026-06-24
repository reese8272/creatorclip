// Per-video pipeline SSE hook (Issue 210). Wraps subscribeToTaskStream against
// /tasks/{videoId}/events and exposes the coarse `stage` + human `label` from
// worker step events. Consumes both onStep (label) and onStage (stage) handlers
// added to taskStream.ts in this same issue.
//
// Slot-exhaustion guard (critical): the hook only opens an EventSource when the
// video is actively in-flight (ingest_status === 'pending' | 'running'). Rows
// that are 'done' or 'failed' fall back to the Badge immediately — no
// connection is opened. This keeps the server-side 3-slot SSE cap (per
// progress.py::MAX_CONCURRENT_SSE_PER_CREATOR=3) from being exhausted by a
// dashboard showing 10 completed videos.
//
// Staleness detection: if no step event arrives within STALE_MS after the last
// one (or after mount for a streaming connection), isStale flips to true so the
// UI shows "taking longer than usual" — no countdown, no timer-based copy.

import { useEffect, useRef, useState } from 'react'
import { subscribeToTaskStream } from '@/lib/taskStream'
import type { IngestStatus } from '@/types'

/** After this many ms with no step event we surface "taking longer than usual". */
const STALE_MS = 90_000

export interface StageStreamState {
  stage: string | null
  label: string | null
  status: 'idle' | 'streaming' | 'done' | 'error'
  isStale: boolean
  failureReason: string | null
}

const IDLE_STATE: StageStreamState = {
  stage: null,
  label: null,
  status: 'idle',
  isStale: false,
  failureReason: null,
}

/**
 * Open an SSE connection to `/tasks/{videoId}/events` only while the video is
 * actively in-flight. Pass `videoId=null` or an already-settled status to stay
 * idle and never open a connection.
 */
export function useStageStream(
  videoId: string | null,
  ingestStatus: IngestStatus,
): StageStreamState {
  const isInFlight = ingestStatus === 'pending' || ingestStatus === 'running'
  // Only subscribe when the video is in-flight; null url = no EventSource.
  const url = videoId && isInFlight ? `/tasks/${videoId}/events` : null

  const [state, setState] = useState<StageStreamState>(IDLE_STATE)
  const staleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  function resetStaleTimer(): void {
    if (staleTimerRef.current) clearTimeout(staleTimerRef.current)
    staleTimerRef.current = setTimeout(() => {
      setState((s) => (s.status === 'streaming' ? { ...s, isStale: true } : s))
    }, STALE_MS)
  }

  useEffect(() => {
    if (!url) {
      // Not in-flight — reset to idle and make sure we have no dangling timer.
      // Intentional reset-on-condition-change; tracked for the lint sweep.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setState(IDLE_STATE)
      if (staleTimerRef.current) clearTimeout(staleTimerRef.current)
      return
    }

    setState({ stage: null, label: null, status: 'streaming', isStale: false, failureReason: null })
    resetStaleTimer()

    const sub = subscribeToTaskStream(url, {
      onStep: (stepLabel) => {
        setState((s) => ({ ...s, label: stepLabel, isStale: false }))
        resetStaleTimer()
      },
      onStage: (stage) => {
        setState((s) => ({ ...s, stage, isStale: false }))
        resetStaleTimer()
      },
      onDone: () => {
        if (staleTimerRef.current) clearTimeout(staleTimerRef.current)
        setState((s) => ({ ...s, status: 'done' }))
      },
      onError: (message) => {
        if (staleTimerRef.current) clearTimeout(staleTimerRef.current)
        setState((s) => ({ ...s, status: 'error', failureReason: message }))
      },
    })

    return () => {
      sub.close()
      if (staleTimerRef.current) clearTimeout(staleTimerRef.current)
    }
    // url encodes both videoId and ingestStatus — correct dep set.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url])

  return state
}
