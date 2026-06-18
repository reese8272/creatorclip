import { useState } from 'react'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Card, CardBody, CardHeader } from '@/components/ui/card'
import type { AnalysisMode } from '@/types'

const MODES: { value: AnalysisMode; label: string; help: string }[] = [
  { value: 'auto', label: 'Auto', help: 'New uploads from your channel ingest automatically (current default).' },
  {
    value: 'selective',
    label: 'Selective',
    help: 'You pick which catalog videos to analyze — one click per video, minutes spent on demand.',
  },
  {
    value: 'manual',
    label: 'Manual',
    help: 'Only files you upload directly are processed. Linked videos sit in the catalog until you queue them.',
  },
]

export function IntakeModeSection({ initialMode }: { initialMode: AnalysisMode }) {
  const [mode, setMode] = useState<AnalysisMode>(initialMode)
  const [status, setStatus] = useState<{ text: string; tone: 'muted' | 'success' | 'danger' } | null>(null)

  const save = async () => {
    setStatus({ text: 'Saving…', tone: 'muted' })
    try {
      await api('/creators/me/analysis-mode', { method: 'PATCH', body: { analysis_mode: mode } })
      setStatus({ text: 'Saved.', tone: 'success' })
    } catch {
      setStatus({ text: 'Could not save — try again.', tone: 'danger' })
    }
  }

  return (
    <Card>
      <CardHeader
        title="Video intake"
        description="How AutoClip handles new videos. Transcription and clip generation cost minutes — these modes decide when that meter starts."
      />
      <CardBody className="flex flex-col gap-3">
        {MODES.map((m) => (
          <label
            key={m.value}
            className={cn(
              'flex cursor-pointer gap-3 rounded-md border px-4 py-3 transition-colors',
              mode === m.value ? 'border-accent bg-accent-soft' : 'border-default hover:border-strong',
            )}
          >
            <input
              type="radio"
              name="analysis_mode"
              checked={mode === m.value}
              onChange={() => setMode(m.value)}
              className="mt-1 accent-[color:var(--color-accent)]"
            />
            <span>
              <span className="block text-sm font-medium text-fg">{m.label}</span>
              <span className="block text-xs text-muted">{m.help}</span>
            </span>
          </label>
        ))}
        <div className="flex items-center gap-3">
          <Button onClick={save}>Save mode</Button>
          {status && (
            <span
              className={
                status.tone === 'success'
                  ? 'text-sm text-success'
                  : status.tone === 'danger'
                    ? 'text-sm text-danger'
                    : 'text-sm text-muted'
              }
            >
              {status.text}
            </span>
          )}
        </div>
      </CardBody>
    </Card>
  )
}
