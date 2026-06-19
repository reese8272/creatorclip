import { useStreamAction } from '@/hooks/useStreamAction'
import { Button } from '@/components/ui/button'
import { AnalysisPanel, StatusChip } from '@/components/analysis/AnalysisPanel'
import type { ThumbnailConcept } from '@/types'

export function ThumbnailConcepts({ videoId }: { videoId: string }) {
  const { stream, error, busy, start } = useStreamAction<{ concepts: ThumbnailConcept[] }>()
  const concepts = stream.result?.concepts ?? []

  return (
    <AnalysisPanel
      title="Thumbnail Concepts"
      chip={<StatusChip status={stream.status} step={stream.step} error={error} />}
    >
      <Button
        className="mb-4"
        disabled={busy}
        onClick={() => start(`/creators/me/videos/${videoId}/thumbnail-concepts`)}
      >
        {busy ? 'Generating…' : stream.status === 'done' ? 'Regenerate' : 'Generate concepts'}
      </Button>

      {error && <p className="text-sm text-danger">{error}</p>}

      <div className="flex flex-col gap-3">
        {concepts.map((c, i) => (
          <div key={i} className="rounded-md border border-default bg-bg p-4">
            <div className="mb-2 flex items-center justify-between">
              <span className="font-mono text-xs text-subtle">#{i + 1}</span>
              {c.dominant_emotion && (
                <span className="rounded-sm bg-accent-soft px-1.5 font-mono text-xs text-accent-text">
                  {c.dominant_emotion}
                </span>
              )}
            </div>
            <div className="mb-2 text-sm font-medium leading-snug text-fg">{c.composition}</div>
            <div className="mb-2 flex flex-wrap gap-2">
              {c.text_overlay && (
                <span className="rounded-sm border border-default bg-surface px-1.5 font-mono text-xs text-subtle">
                  text: {c.text_overlay}
                </span>
              )}
              {c.color_direction && (
                <span className="rounded-sm border border-default bg-surface px-1.5 font-mono text-xs text-subtle">
                  colors: {c.color_direction}
                </span>
              )}
            </div>
            {c.predicted_ctr_rationale && (
              <div className="text-xs leading-relaxed text-subtle">{c.predicted_ctr_rationale}</div>
            )}
            {c.based_on_pattern && (
              <div className="mt-1 text-xs italic text-subtle">Based on: {c.based_on_pattern}</div>
            )}
          </div>
        ))}
      </div>

      {concepts.length > 0 && (
        <p className="mt-4 border-t border-default pt-3 text-xs text-subtle">
          These concepts are estimates grounded in your channel's visual patterns and current niche
          trends. AutoClip cannot guarantee specific CTR outcomes.
        </p>
      )}
    </AnalysisPanel>
  )
}
