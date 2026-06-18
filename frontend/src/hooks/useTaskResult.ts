import { useEffect, useState } from 'react'
import { subscribeToTaskStream } from '@/lib/taskStream'
import type { TaskStatus } from '@/hooks/useTaskStream'

// Richer companion to useTaskStream for the analysis/insights LLM features
// (Issue 85e). Where useTaskStream flattens everything into one buffer (good
// for a progress log), this separates the three things those pages actually
// render: streamed prose `tokens` (video-analysis narrative), the latest `step`
// label (status chip), and the final `done` `result` payload (titles /
// concepts / hook report / chapters). Pass `null` to stay idle; pass a stream
// URL to open an EventSource (torn down on unmount or URL change).
export interface TaskResultState<T> {
  status: TaskStatus
  step: string | null
  tokens: string
  result: T | null
  error: string | null
}

const initialState = <T>(url: string | null): TaskResultState<T> => ({
  status: url ? 'streaming' : 'idle',
  step: null,
  tokens: '',
  result: null,
  error: null,
})

export function useTaskResult<T = Record<string, unknown>>(
  url: string | null,
): TaskResultState<T> {
  const [state, setState] = useState<TaskResultState<T>>(() => initialState<T>(url))
  const [trackedUrl, setTrackedUrl] = useState(url)

  // Reset during render when the url changes (same pattern as useTaskStream).
  if (url !== trackedUrl) {
    setTrackedUrl(url)
    setState(initialState<T>(url))
  }

  useEffect(() => {
    if (!url) return
    const sub = subscribeToTaskStream(url, {
      onToken: (chunk) => setState((s) => ({ ...s, tokens: s.tokens + chunk })),
      onStep: (label) => setState((s) => ({ ...s, step: label })),
      onDone: (data) => setState((s) => ({ ...s, status: 'done', result: data as T })),
      onError: (message) => setState((s) => ({ ...s, status: 'error', error: message })),
    })
    return () => sub.close()
  }, [url])

  return state
}
