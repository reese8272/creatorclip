import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api'
import { useTaskStream } from '@/hooks/useTaskStream'
import { Button } from '@/components/ui/button'
import { Chip } from '@/components/Chip'
import { ChipLookingItUp } from '@/components/chip/ChipStates'
import { StreamConsole } from '@/components/onboarding/StreamConsole'
import { Panel } from '@/components/insights/InsightsPanel'
import type { ImprovementBrief as Brief, TaskQueued } from '@/types'

// Async 202 + poll brief (Issue 78d). POST kicks off the Celery job and returns
// a stream URL for the live log; we then poll GET until status leaves `pending`.
export function ImprovementBrief() {
  const [streamUrl, setStreamUrl] = useState<string | null>(null)
  const [polling, setPolling] = useState(false)
  const [startError, setStartError] = useState<string | null>(null)
  const stream = useTaskStream(streamUrl)

  const briefQuery = useQuery({
    queryKey: ['improvement-brief'],
    queryFn: () => api<Brief>('/creators/me/improvement-brief'),
    enabled: polling,
    refetchInterval: (q) => (q.state.data?.status === 'pending' ? 3000 : false),
  })

  const status = briefQuery.data?.status
  const generating = polling && status !== 'ready' && status !== 'failed'

  async function generate() {
    setStartError(null)
    setStreamUrl(null)
    try {
      const { stream_url } = await api<TaskQueued>('/creators/me/improvement-brief', {
        method: 'POST',
      })
      if (stream_url) setStreamUrl(stream_url)
      setPolling(true)
    } catch (e) {
      setStartError(e instanceof ApiError ? e.message : 'Failed to start — try again.')
    }
  }

  const btnLabel = generating
    ? 'Generating…'
    : status === 'ready'
      ? 'Regenerate'
      : startError || status === 'failed'
        ? 'Retry'
        : 'Generate brief'

  return (
    <Panel
      title={
        <>
          <Chip pose="present" size={24} />
          Content improvement brief
        </>
      }
      sub="Live web research + your channel data. ~15s."
    >
      <Button onClick={generate} disabled={generating}>
        {btnLabel}
      </Button>

      {generating && stream.status === 'streaming' && (
        <>
          <div className="my-3 flex justify-center">
            <ChipLookingItUp size={64} />
          </div>
          <StreamConsole buffer={stream.buffer} />
        </>
      )}

      <div className="mt-4 min-h-[40px] whitespace-pre-wrap text-sm leading-relaxed text-fg">
        {startError && <span className="text-danger">{startError}</span>}
        {status === 'failed' && (
          <span className="text-danger">
            {briefQuery.data?.error || 'Brief generation failed — try again.'}
          </span>
        )}
        {status === 'ready' && (briefQuery.data?.brief || 'No brief available.')}
        {generating && !startError && (
          <span className="italic text-subtle">
            Generating your brief — this can take up to a minute…
          </span>
        )}
      </div>
    </Panel>
  )
}
