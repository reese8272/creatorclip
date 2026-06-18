import { useStreamAction } from '@/hooks/useStreamAction'
import { Button } from '@/components/ui/button'
import { AnalysisPanel, CopyButton, StatusChip } from '@/components/analysis/AnalysisPanel'
import type { TitleSuggestion } from '@/types'

const CTR_CLS: Record<string, string> = {
  up: 'text-success border-[color:var(--color-success-border)]',
  down: 'text-danger border-[color:var(--color-danger-border)]',
  neutral: 'text-muted border-strong',
}

export function TitleOptimizer({ videoId, videoTitle }: { videoId: string; videoTitle: string }) {
  const { stream, error, busy, start } = useStreamAction<{ suggestions: TitleSuggestion[] }>()
  const suggestions = stream.result?.suggestions ?? []

  return (
    <AnalysisPanel
      title="Title Optimizer"
      chip={<StatusChip status={stream.status} step={stream.step} error={error} />}
    >
      <p className="mb-4 text-xs text-subtle">
        {videoTitle ? (
          <>
            Video: <strong className="text-fg">{videoTitle}</strong>
          </>
        ) : (
          <>
            Video ID: <strong className="font-mono text-fg">{videoId}</strong>
          </>
        )}
      </p>
      <Button
        className="mb-4"
        disabled={busy}
        onClick={() => start(`/creators/me/videos/${videoId}/titles`)}
      >
        {busy ? 'Generating…' : stream.status === 'done' ? 'Regenerate' : 'Generate titles'}
      </Button>

      {error && <p className="text-sm text-danger">{error}</p>}

      <div className="flex flex-col gap-3">
        {suggestions.map((s, i) => {
          const chars = s.title?.length ?? 0
          return (
            <div key={i} className="rounded-md border border-default bg-bg p-4">
              <div className="mb-2 flex items-start justify-between gap-3">
                <div className="flex-1 text-sm font-medium leading-snug text-fg">{s.title}</div>
                <div className="flex flex-shrink-0 items-center gap-2">
                  {s.search_grounded && (
                    <span className="rounded-sm border border-default bg-surface px-1 font-mono text-[10px] text-subtle">
                      web-grounded
                    </span>
                  )}
                  <span
                    className={`rounded-sm border px-1.5 font-mono text-xs ${CTR_CLS[s.ctr_signal] ?? CTR_CLS.neutral}`}
                  >
                    {s.ctr_signal || 'neutral'}
                  </span>
                  <CopyButton text={s.title || ''} />
                </div>
              </div>
              <div className="text-xs leading-relaxed text-subtle">{s.rationale}</div>
              <div
                className={`mt-1 font-mono text-xs ${chars > 80 ? 'text-danger' : 'text-subtle'}`}
              >
                {chars}/100 chars
              </div>
            </div>
          )
        })}
      </div>

      {suggestions.length > 0 && (
        <p className="mt-4 border-t border-default pt-3 text-xs text-subtle">
          These suggestions are estimates grounded in your channel data and current search trends.
          AutoClip cannot guarantee specific CTR or view outcomes.
        </p>
      )}
    </AnalysisPanel>
  )
}
