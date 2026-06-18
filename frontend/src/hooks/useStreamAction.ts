import { useState } from 'react'
import { api, ApiError } from '@/lib/api'
import { useTaskResult, type TaskResultState } from '@/hooks/useTaskResult'

export interface StreamAction<T> {
  stream: TaskResultState<T>
  error: string | null
  busy: boolean
  /** POST the endpoint, then stream its task. Sets an error if no stream_url. */
  start: (endpoint: string) => Promise<void>
}

// The uniform "POST → 202 {stream_url} → stream the result" pattern shared by
// the per-video analysis features (titles, chapters, thumbnail concepts —
// Issue 85e). Each renders its own `stream.result` payload; this owns the POST,
// the stream URL lifecycle, and the error surface. (Video-analysis and the hook
// analyzer have bespoke flows and don't use this.)
export function useStreamAction<T>(): StreamAction<T> {
  const [url, setUrl] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [posting, setPosting] = useState(false)
  const stream = useTaskResult<T>(url)

  async function start(endpoint: string) {
    setError(null)
    setUrl(null)
    setPosting(true)
    try {
      const data = await api<{ stream_url: string | null }>(endpoint, { method: 'POST' })
      if (data.stream_url) setUrl(data.stream_url)
      else setError('Could not connect to the live stream. Try again shortly.')
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Network error — please try again.')
    } finally {
      setPosting(false)
    }
  }

  return { stream, error, busy: posting || stream.status === 'streaming', start }
}
