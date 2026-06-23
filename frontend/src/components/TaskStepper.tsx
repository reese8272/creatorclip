import type { TaskStatus } from '@/hooks/useTaskStream'

// Formats elapsed milliseconds as a human-readable string: "0s", "1m 23s", etc.
function formatElapsed(ms: number): string {
  const totalSec = Math.floor(ms / 1000)
  if (totalSec < 60) return `${totalSec}s`
  const min = Math.floor(totalSec / 60)
  const sec = totalSec % 60
  return sec === 0 ? `${min}m` : `${min}m ${sec}s`
}

// Labeled stepper for long-running SSE task streams (catalog sync, DNA build).
// Accepts step labels as they arrive from useTaskStream and renders each as a
// row with a check or spinner icon. Displays elapsed time and a persistent
// "you can leave and come back" copy for waits > 10 s (NN/g guidance). No
// fabricated ETA or countdown — only what the worker actually reports.
//
// Pure-presentational: all state lives in the parent (Onboarding.tsx). The
// component is shared between the catalog-sync and DNA-build cards.
export function TaskStepper({
  steps,
  status,
  elapsedMs,
}: {
  steps: string[]
  status: TaskStatus
  elapsedMs: number
}) {
  if (status === 'idle' && steps.length === 0) return null

  const isStreaming = status === 'streaming'
  const isDone = status === 'done'
  const isError = status === 'error'

  return (
    <div className="mt-3 rounded-md border border-default bg-bg p-3 text-xs">
      {/* Step list */}
      {steps.length > 0 && (
        <ul className="mb-2 space-y-1" role="list" aria-label="Progress steps">
          {steps.map((label, i) => {
            const isLast = i === steps.length - 1
            // While streaming, the last step is still in-progress.
            const completed = isDone || !isLast || isError
            return (
              <li key={i} className="flex items-center gap-2">
                {completed ? (
                  <span className="text-success" aria-hidden="true">
                    ✓
                  </span>
                ) : (
                  <span
                    className="inline-block h-3 w-3 animate-spin rounded-full border border-current border-t-transparent text-accent"
                    aria-hidden="true"
                  />
                )}
                <span className={completed ? 'text-fg' : 'text-muted'}>{label}</span>
              </li>
            )
          })}
        </ul>
      )}

      {/* Elapsed time — shown while streaming or on completion */}
      {(isStreaming || isDone) && (
        <p className="text-muted">
          {isStreaming ? 'Running' : 'Completed'} — {formatElapsed(elapsedMs)}
        </p>
      )}

      {/* Interruption-safe copy — always visible while streaming (NN/g pattern) */}
      {isStreaming && (
        <p className="mt-1 text-muted">
          This takes a few minutes — you can leave and come back.
        </p>
      )}
    </div>
  )
}
