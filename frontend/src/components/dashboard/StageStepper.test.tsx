import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { StageStepper } from './StageStepper'

// Issue 210 — StageStepper unit tests.
// Guards: stage label mapping, coarse ETA copy, stale-signal messaging,
// error state with safe reason, idle badge fallback, no-virality copy.

describe('StageStepper', () => {
  it('renders the human label for each known stage', () => {
    const stages: [string, string][] = [
      ['ingest', 'Ingesting'],
      ['transcribe', 'Transcribing'],
      ['signals', 'Analysing signals'],
      ['render', 'Rendering'],
      ['clean', 'Cleaning'],
    ]
    for (const [stage, expected] of stages) {
      const { unmount } = render(
        <StageStepper stage={stage} label={null} status="streaming" />,
      )
      expect(screen.getByText(expected)).toBeInTheDocument()
      unmount()
    }
  })

  it('shows coarse ETA copy while streaming (never a countdown)', () => {
    render(<StageStepper stage="ingest" label={null} status="streaming" />)
    // ETA copy must be vague — no specific seconds/minutes count
    const etaEl = screen.getByText('usually a few minutes')
    expect(etaEl).toBeInTheDocument()
    // Absolutely no virality promise
    expect(document.body.textContent ?? '').not.toMatch(/viral/i)
  })

  it('prefers the step label over the fallback ETA copy when available', () => {
    render(
      <StageStepper stage="transcribe" label="Extracting word-level timestamps" status="streaming" />,
    )
    expect(screen.getByText('Extracting word-level timestamps')).toBeInTheDocument()
    expect(screen.queryByText('usually a few minutes')).not.toBeInTheDocument()
  })

  it('shows "taking longer than usual" when isStale is true', () => {
    render(
      <StageStepper stage="render" label="render_start" status="streaming" isStale />,
    )
    expect(screen.getByText('taking longer than usual')).toBeInTheDocument()
    expect(screen.queryByText('usually a few minutes')).not.toBeInTheDocument()
    expect(screen.queryByText('render_start')).not.toBeInTheDocument()
  })

  it('shows the safe failure reason on error state', () => {
    render(
      <StageStepper
        stage={null}
        label={null}
        status="error"
        failureReason="ingest failed — source media missing"
      />,
    )
    expect(screen.getByText('ingest failed — source media missing')).toBeInTheDocument()
    // Error badge is shown
    expect(screen.getByText('failed')).toBeInTheDocument()
  })

  it('renders a muted badge (idle fallback) when status is idle', () => {
    render(<StageStepper stage={null} label={null} status="idle" />)
    expect(screen.getByText('pending')).toBeInTheDocument()
    // No streaming UI shown
    expect(screen.queryByText('Ingesting')).not.toBeInTheDocument()
    expect(screen.queryByText('usually a few minutes')).not.toBeInTheDocument()
  })

  it('renders the done badge when status is done', () => {
    render(<StageStepper stage={null} label={null} status="done" />)
    expect(screen.getByText('done')).toBeInTheDocument()
  })

  it('never promises virality in any state', () => {
    const states: Array<'idle' | 'streaming' | 'done' | 'error'> = [
      'idle', 'streaming', 'done', 'error',
    ]
    for (const status of states) {
      const { unmount } = render(
        <StageStepper stage="ingest" label="step" status={status} />,
      )
      expect(document.body.textContent ?? '').not.toMatch(/viral/i)
      unmount()
    }
  })
})
