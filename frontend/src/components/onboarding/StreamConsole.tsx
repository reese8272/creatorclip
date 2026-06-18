import { useEffect, useRef } from 'react'

// Terminal-style live progress view for an SSE task stream (catalog sync, DNA
// build). Renders nothing until there's a buffer; auto-scrolls to the latest
// line as the worker emits. The buffer is produced by useTaskStream.
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
