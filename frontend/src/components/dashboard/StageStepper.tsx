// Pipeline-stage progress indicator (Issue 210). Purely presentational — all
// state comes from the useStageStream hook. Renders a coarse stage label and
// an ETA copy line. Never shows a countdown (pipeline duration is variable);
// signals staleness with "taking longer than usual" when the hook sets it.
// Falls back to a Badge-style muted pill when status is idle.

import { Badge } from '@/components/ui/badge'

// Human-readable copy for each coarse stage enum the worker emits.
const STAGE_LABELS: Record<string, string> = {
  ingest: 'Ingesting',
  transcribe: 'Transcribing',
  signals: 'Analysing signals',
  render: 'Rendering',
  clean: 'Cleaning',
}

function stageLabel(stage: string): string {
  return STAGE_LABELS[stage] ?? stage
}

export interface StageStepperProps {
  /** Coarse machine-readable stage from the worker step event. */
  stage: string | null
  /** Latest human-readable step label from the worker step event. */
  label: string | null
  /** Connection status driven by the SSE hook. */
  status: 'idle' | 'streaming' | 'done' | 'error'
  /** Set when last step event is stale (hook detected staleness by timestamp). */
  isStale?: boolean
  /** Safe one-line failure reason surfaced on error state. */
  failureReason?: string | null
}

export function StageStepper({ stage, label, status, isStale, failureReason }: StageStepperProps) {
  // Idle: show a muted "pending" badge — no live connection.
  if (status === 'idle') {
    return <Badge variant="muted">pending</Badge>
  }

  if (status === 'error') {
    return (
      <div className="flex flex-col gap-0.5">
        <Badge variant="danger">failed</Badge>
        {failureReason && (
          <span className="font-mono text-xs text-danger" aria-live="polite">
            {failureReason}
          </span>
        )}
      </div>
    )
  }

  if (status === 'done') {
    return <Badge variant="success">done</Badge>
  }

  // streaming — show stage + ETA hint
  return (
    <div className="flex flex-col gap-0.5" aria-live="polite">
      <Badge variant="warning">{stage ? stageLabel(stage) : 'running'}</Badge>
      {isStale ? (
        <span className="font-mono text-xs text-subtle">taking longer than usual</span>
      ) : (
        <span className="font-mono text-xs text-subtle">
          {label ?? 'usually a few minutes'}
        </span>
      )}
    </div>
  )
}
