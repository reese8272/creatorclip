import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiError } from '@/lib/api'
import { useAuth } from '@/hooks/useAuth'
import { useTaskResult } from '@/hooks/useTaskResult'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { StatusChip } from '@/components/analysis/AnalysisPanel'
import type { AnalysisStart } from '@/types'

const inputCls =
  'w-full rounded-md border border-strong bg-bg px-3 py-2 text-sm text-fg placeholder:text-subtle focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent-soft'

// "Ask why a video performed the way it did" — streams a narrative answer over
// SSE (token-by-token). Port of static/analysis.html's startAnalysis().
export function AnalysisQuery() {
  const { balance } = useAuth()
  const [url, setUrl] = useState('')
  const [query, setQuery] = useState('')
  const [streamUrl, setStreamUrl] = useState<string | null>(null)
  const [context, setContext] = useState<{ title: string | null; analytics: boolean } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [posting, setPosting] = useState(false)
  const stream = useTaskResult(streamUrl)

  async function analyze() {
    if (!url.trim() || !query.trim()) return
    setError(null)
    setContext(null)
    setStreamUrl(null)
    setPosting(true)
    try {
      const data = await api<AnalysisStart>('/creators/me/video-analysis', {
        method: 'POST',
        body: { youtube_url: url.trim(), query: query.trim() },
      })
      const analytics = !!(data.analytics_available ?? data.has_metrics)
      setContext({ title: data.video_title, analytics })
      if (data.stream_url) setStreamUrl(data.stream_url)
      else setError('Could not connect to the live stream. Try again shortly.')
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Network error — please try again.')
    } finally {
      setPosting(false)
    }
  }

  const busy = posting || stream.status === 'streaming'
  const showPanel = busy || stream.status !== 'idle' || error

  return (
    <>
      <div className="mb-6 rounded-md border border-default bg-surface p-5 shadow-sm shadow-inset">
        <label className="mb-2 block text-xs uppercase tracking-[0.06em] text-muted">
          YouTube URL or video ID
        </label>
        <input
          className={`${inputCls} mb-4`}
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://youtu.be/dQw4w9WgXcQ  or  dQw4w9WgXcQ"
          spellCheck={false}
          autoComplete="off"
        />
        <label className="mb-2 block text-xs uppercase tracking-[0.06em] text-muted">
          Your question
        </label>
        <textarea
          className={`${inputCls} min-h-[72px] resize-y`}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          maxLength={500}
          placeholder="Why did this video underperform? What made this Short take off?"
        />
        <p className="mt-1 text-xs text-subtle">
          Ask something specific — the more focused your question, the sharper the answer.
        </p>
        <div className="mt-3 flex items-center justify-between gap-3">
          <div className="text-xs text-subtle">
            {context?.title && (
              <>
                <span className="text-muted">Video: </span>
                {context.title}{' '}
                <Badge variant={context.analytics ? 'success' : 'warning'}>
                  {context.analytics ? 'analytics available' : 'analytics unavailable'}
                </Badge>
              </>
            )}
          </div>
          <Button onClick={analyze} disabled={busy}>
            {busy ? 'Analyzing…' : 'Analyze →'}
          </Button>
        </div>

        {balance?.low_balance && (
          <div className="mt-3 rounded-md border border-warning-border bg-[color:var(--color-warning-soft)] px-4 py-3 text-sm text-fg">
            Low balance — <strong className="font-mono">{balance.minutes_balance} min</strong> left.{' '}
            <Link to="/pricing" className="text-accent hover:text-accent-hover">
              Add minutes
            </Link>{' '}
            before running another analysis.
          </div>
        )}

        {context && !context.analytics && (
          <div className="mt-3 rounded-md border border-default border-l-2 border-l-warning bg-bg px-4 py-3 text-sm text-muted">
            <strong className="text-fg">Full analytics unavailable</strong> — this video isn't in
            your ingested catalog yet, so the analysis runs in metadata-only mode (title +
            description + transcript). Ingest it to unlock retention, audience activity, and
            engagement signals on the next run.
          </div>
        )}
      </div>

      {showPanel && (
        <div className="mb-6 rounded-md border border-default bg-surface p-5 shadow-sm shadow-inset">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-sm font-medium uppercase tracking-[0.06em] text-muted">Analysis</h2>
            <StatusChip status={stream.status} step={stream.step} error={error} />
          </div>
          {error ? (
            <p className="text-sm text-danger">{error}</p>
          ) : (
            <div className="min-h-[48px] whitespace-pre-wrap break-words text-sm leading-relaxed text-fg">
              {stream.tokens || <span className="italic text-subtle">Starting…</span>}
              {stream.status === 'streaming' && <span className="ml-px animate-pulse text-accent">▊</span>}
            </div>
          )}
          {stream.status === 'done' && (
            <p className="mt-4 border-t border-default pt-4 text-xs text-subtle">
              This analysis is grounded in your channel data. AutoClip does not promise virality or
              specific growth outcomes.
            </p>
          )}
        </div>
      )}
    </>
  )
}
