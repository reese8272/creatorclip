import { useEffect, useRef } from 'react'

// RETIRED — Issue 214 replaced this raw terminal log with TaskStepper for
// the two long-running onboarding waits (Steps 2 and 4). No current callers
// remain in the codebase. Kept as a shell so import references in tests or
// storybook snapshots do not break until they are explicitly cleaned up.
export function StreamConsole({ buffer }: { buffer: string }) {
  const ref = useRef<HTMLPreElement>(null)

  useEffect(() => {
    const el = ref.current
    if (el) el.scrollTop = el.scrollHeight
  }, [buffer])

  if (!buffer) return null
  return (
    <pre
      ref={ref}
      className="mt-3 max-h-[180px] overflow-y-auto whitespace-pre-wrap break-words rounded-md border border-default bg-bg p-3 font-mono text-xs leading-normal text-muted"
    >
      {buffer}
    </pre>
  )
}
