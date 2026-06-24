import { useEffect, useMemo } from 'react'
import { useStreamAction } from '@/hooks/useStreamAction'
import { Button } from '@/components/ui/button'
import { AnalysisPanel, CopyButton, StatusChip } from '@/components/analysis/AnalysisPanel'
import type { Chapter } from '@/types'

// `onChapters` (optional) lets a parent observe generated chapters without
// changing this panel's self-managed behavior — used by the long-form Editor to
// draw chapter ticks on the master timeline.
export function ChaptersPanel({
  videoId,
  onChapters,
}: {
  videoId: string
  onChapters?: (chapters: Chapter[]) => void
}) {
  const { stream, error, busy, start } = useStreamAction<{
    chapters: Chapter[]
    description_block: string
  }>()
  const chapters = useMemo(() => stream.result?.chapters ?? [], [stream.result])
  const descriptionBlock = stream.result?.description_block ?? ''

  useEffect(() => {
    if (onChapters && chapters.length > 0) onChapters(chapters)
  }, [onChapters, chapters])

  return (
    <AnalysisPanel
      title="Chapter Markers"
      chip={<StatusChip status={stream.status} step={stream.step} error={error} />}
    >
      <Button
        className="mb-4"
        disabled={busy}
        onClick={() => start(`/creators/me/videos/${videoId}/chapters`)}
      >
        {busy ? 'Generating…' : stream.status === 'done' ? 'Regenerate' : 'Generate chapters'}
      </Button>

      {error && <p className="text-sm text-danger">{error}</p>}

      {chapters.length > 0 && (
        <>
          <ul className="mb-4">
            {chapters.map((c, i) => (
              <li
                key={i}
                className="flex gap-3 border-b border-default py-2 text-sm last:border-b-0"
              >
                <span className="min-w-[48px] pt-0.5 font-mono text-xs text-accent-text">
                  {c.timestamp_formatted}
                </span>
                <span className="text-fg">{c.title}</span>
              </li>
            ))}
          </ul>
          <pre className="mb-3 overflow-x-auto rounded-md border border-default bg-bg p-3 font-mono text-xs text-subtle">
            {descriptionBlock}
          </pre>
          <CopyButton text={descriptionBlock} label="Copy to clipboard" />
        </>
      )}
    </AnalysisPanel>
  )
}
