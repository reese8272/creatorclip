import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { TaskStepper } from './TaskStepper'

// Unit tests for TaskStepper (Issue 214). Covers: step-label rendering, check/
// spinner icon state, elapsed-time formatting, interruption-safe copy, and the
// hard guarantee that no countdown or ETA is ever rendered.

describe('TaskStepper', () => {
  it('renders nothing when idle with no steps', () => {
    const { container } = render(
      <TaskStepper steps={[]} status="idle" elapsedMs={0} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('renders all step labels while streaming', () => {
    render(
      <TaskStepper steps={['Fetching videos', 'Embedding', 'Persisting']} status="streaming" elapsedMs={5000} />,
    )
    expect(screen.getByText('Fetching videos')).toBeInTheDocument()
    expect(screen.getByText('Embedding')).toBeInTheDocument()
    expect(screen.getByText('Persisting')).toBeInTheDocument()
  })

  it('shows a check icon for completed steps and a spinner for the active last step', () => {
    render(
      <TaskStepper steps={['Fetch', 'Embed', 'Persist']} status="streaming" elapsedMs={3000} />,
    )
    const checks = screen.getAllByText('✓')
    // First two steps done; last step still in-progress (spinner, not check).
    expect(checks).toHaveLength(2)
  })

  it('marks all steps completed when status is done', () => {
    render(
      <TaskStepper steps={['Fetch', 'Embed', 'Persist']} status="done" elapsedMs={90000} />,
    )
    const checks = screen.getAllByText('✓')
    expect(checks).toHaveLength(3)
  })

  it('formats elapsed seconds under 60 as "<n>s"', () => {
    render(<TaskStepper steps={['step']} status="streaming" elapsedMs={45000} />)
    expect(screen.getByText(/45s/)).toBeInTheDocument()
  })

  it('formats elapsed seconds >= 60 as "<m>m <s>s"', () => {
    render(<TaskStepper steps={['step']} status="streaming" elapsedMs={83000} />)
    expect(screen.getByText(/1m 23s/)).toBeInTheDocument()
  })

  it('formats an exact minute as "<m>m" with no seconds component', () => {
    render(<TaskStepper steps={['step']} status="streaming" elapsedMs={120000} />)
    expect(screen.getByText(/2m/)).toBeInTheDocument()
    expect(screen.queryByText(/2m 0s/)).toBeNull()
  })

  it('shows the interruption-safe copy while streaming', () => {
    render(<TaskStepper steps={['Fetch']} status="streaming" elapsedMs={15000} />)
    expect(
      screen.getByText(/you can leave and come back/i),
    ).toBeInTheDocument()
  })

  it('hides the interruption-safe copy after completion', () => {
    render(<TaskStepper steps={['Fetch']} status="done" elapsedMs={60000} />)
    expect(screen.queryByText(/you can leave and come back/i)).toBeNull()
  })

  it('never renders a countdown, ETA, or remaining-time string', () => {
    render(
      <TaskStepper steps={['Fetch']} status="streaming" elapsedMs={30000} />,
    )
    expect(screen.queryByText(/ETA/i)).toBeNull()
    expect(screen.queryByText(/remaining/i)).toBeNull()
    expect(screen.queryByText(/estimated/i)).toBeNull()
    // No "mm:ss" countdown pattern
    expect(screen.queryByText(/\d+:\d{2}/)).toBeNull()
  })

  it('renders step list with an accessible aria-label', () => {
    render(
      <TaskStepper steps={['Fetch', 'Embed']} status="streaming" elapsedMs={10000} />,
    )
    expect(screen.getByRole('list', { name: 'Progress steps' })).toBeInTheDocument()
  })
})
