import { useEffect, useState } from 'react'
import { subscribeToTaskStream } from '@/lib/taskStream'

export type TaskStatus = 'idle' | 'streaming' | 'done' | 'error'

export interface TaskStreamState {
  /** Rolling, human-readable progress buffer (steps + streamed tokens). */
  buffer: string
  /** Ordered list of step labels emitted by the worker via SSE `step` events. */
  steps: string[]
  status: TaskStatus
  error: string | null
}

// Declarative React wrapper around subscribeToTaskStream (the SSE consumer for
// long-running worker jobs: DNA rebuild, insights, analysis). Opens an
// EventSource when a stream URL is supplied and tears it down on unmount or when
// the URL changes — the canonical EventSource-in-useEffect cleanup pattern, so
// pages stop hand-wiring connection lifecycles (the bug class the vanilla
// progressStream.js + activeTasks.js juggled). Pass `null` to stay idle.
//
// SSE is intentionally not modelled as a TanStack Query (queries are
// promise-based, not persistent connections); when a page needs the streamed
// result to land in the query cache it bridges on `done` via
// queryClient.setQueryData — kept out of this generic hook on purpose.
const initialState = (url: string | null): TaskStreamState => ({
  buffer: '',
  steps: [],
  status: url ? 'streaming' : 'idle',
  error: null,
})

export function useTaskStream(url: string | null): TaskStreamState {
  const [state, setState] = useState<TaskStreamState>(() => initialState(url))
  const [trackedUrl, setTrackedUrl] = useState(url)

  // Reset DURING RENDER when the url changes — React's recommended alternative
  // to resetting state inside an effect (avoids the cascading-render the
  // react-hooks lint rule guards against). The subscription itself lives in the
  // effect below; only its lifecycle (open/close) belongs there.
  if (url !== trackedUrl) {
    setTrackedUrl(url)
    setState(initialState(url))
  }

  useEffect(() => {
    if (!url) return
    const sub = subscribeToTaskStream(url, {
      onRender: (buffer) => setState((s) => ({ ...s, buffer })),
      onStep: (label) =>
        setState((s) => ({ ...s, steps: label ? [...s.steps, label] : s.steps })),
      onDone: () => setState((s) => ({ ...s, status: 'done' })),
      onError: (message) => setState((s) => ({ ...s, status: 'error', error: message })),
    })
    return () => sub.close()
  }, [url])

  return state
}
