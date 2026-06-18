import { useState } from 'react'
import { api, ApiError } from '@/lib/api'
import { useTaskResult } from '@/hooks/useTaskResult'
import { Button } from '@/components/ui/button'
import { AnalysisPanel, StatusChip } from '@/components/analysis/AnalysisPanel'
import type { HookReport } from '@/types'

// Bespoke flow (not useStreamAction): the POST can return 200 {status:"no_data"}
// when there's no retention curve yet, which is a normal state, not an error.
export function HookAnalyzer({ videoId }: { videoId: string }) {
  const [streamUrl, setStreamUrl] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [noData, setNoData] = useState<string | null>(null)
  const [posting, setPosting] = useState(false)
  const stream = useTaskResult<{ report: HookReport }>(streamUrl)
  const report = stream.result?.report

  async function analyze() {
    setError(null)
    setNoData(null)
    setStreamUrl(null)
    setPosting(true)
    try {
      const data = await api<{ status?: string; message?: string; stream_url: string | null }>(
        `/creators/me/videos/${videoId}/hook-analysis`,
        { method: 'POST' },
      )
      if (data.status === 'no_data') setNoData(data.message || 'Retention data not yet available.')
      else if (data.stream_url) setStreamUrl(data.stream_url)
      else setError('Could not connect to the live stream. Try again shortly.')
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Network error — please try again.')
    } finally {
      setPosting(false)
    }
  }

  const busy = posting || stream.status === 'streaming'

  return (
    <AnalysisPanel
      title="Hook Analyzer"
      chip={<StatusChip status={stream.status} step={stream.step} error={error} />}
    >
      <Button className="mb-4" disabled={busy} onClick={analyze}>
        {busy ? 'Analyzing…' : stream.status === 'done' ? 'Re-analyze' : 'Analyze hook'}
      </Button>

      {error && <p className="text-sm text-danger">{error}</p>}
      {noData && <p className="text-sm text-subtle">{noData}</p>}

      {report && (
        <div className="flex flex-col gap-4">
          <span
            className={`inline-flex w-fit items-center gap-2 rounded-md border border-default bg-bg px-3 py-2 font-mono text-xs ${
              report.retention_drop_at_s != null ? 'text-danger' : 'text-success'
            }`}
          >
            {report.retention_drop_at_s != null
              ? `Drop at ${report.retention_drop_at_s.toFixed(1)}s — ${((report.retention_at_drop ?? 0) * 100).toFixed(1)}% retention`
              : 'No significant retention drop in first 30s'}
          </span>
          <HookSection label="Diagnosis" body={report.diagnosis} />
          {report.transcript_at_drop && (
            <div>
              <div className="mb-1 text-xs uppercase tracking-[0.06em] text-muted">
                Transcript at drop
              </div>
              <div className="rounded-r-md border-l-[3px] border-accent bg-bg px-3 py-2 font-mono text-xs text-subtle">
                {report.transcript_at_drop}
              </div>
            </div>
          )}
          <HookSection label="Rewrite suggestion" body={report.rewrite_suggestion} />
          {report.honesty_disclaimer && (
            <p className="border-t border-default pt-3 text-xs text-subtle">
              {report.honesty_disclaimer}
            </p>
          )}
        </div>
      )}
    </AnalysisPanel>
  )
}

function HookSection({ label, body }: { label: string; body: string | null }) {
  if (!body) return null
  return (
    <div>
      <div className="mb-1 text-xs uppercase tracking-[0.06em] text-muted">{label}</div>
      <div className="text-sm leading-relaxed text-fg">{body}</div>
    </div>
  )
}
