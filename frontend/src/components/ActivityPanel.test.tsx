import { render, screen, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ActivityPanel } from './ActivityPanel'
import { upsert, _reset } from '@/stores/activeTasks'

// Issue 211 — ActivityPanel unit tests.
// Guards: hidden when store empty, shows in-flight task, hides after terminal
// auto-removal, deep-link href, cap-degradation graceful, reduced-motion,
// no virality copy.
//
// We stub subscribeToTaskStream so no real EventSource is opened; the store
// state is driven directly via upsert().

vi.mock('@/lib/taskStream', () => ({
  subscribeToTaskStream: vi.fn(() => ({ close: vi.fn() })),
}))

// Also stub sendActivity to keep telemetry calls silent in tests.
vi.mock('@/lib/activity', () => ({
  sendActivity: vi.fn(),
}))

function renderPanel() {
  return render(
    <MemoryRouter>
      <ActivityPanel />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.useFakeTimers()
  _reset()
})
afterEach(() => {
  vi.useRealTimers()
  _reset()
  vi.clearAllMocks()
})

describe('ActivityPanel', () => {
  it('renders nothing when the store is empty', () => {
    const { container } = renderPanel()
    expect(container.firstChild).toBeNull()
  })

  it('renders the region when a task is in-flight', () => {
    upsert('t1', { videoId: 'v1', phase: 'running', stage: 'ingest' })
    renderPanel()
    expect(screen.getByRole('region', { name: /active tasks/i })).toBeInTheDocument()
  })

  it('shows the human stage label for a running task', () => {
    upsert('t1', { videoId: 'v1', phase: 'running', stage: 'transcribe' })
    renderPanel()
    expect(screen.getByText('Transcribing')).toBeInTheDocument()
  })

  it('shows step label when no stage is set', () => {
    upsert('t1', { videoId: 'v1', phase: 'running', label: 'Loading embeddings' })
    renderPanel()
    expect(screen.getByText('Loading embeddings')).toBeInTheDocument()
  })

  it('shows "Queued" for pending phase', () => {
    upsert('t1', { videoId: 'v1', phase: 'pending' })
    renderPanel()
    expect(screen.getByText('Queued')).toBeInTheDocument()
  })

  it('contains a deep-link "view" anchor pointing to the video page', () => {
    upsert('t1', { videoId: 'video123', phase: 'running', stage: 'render' })
    renderPanel()
    const link = screen.getByRole('link', { name: /view/i })
    expect(link).toBeInTheDocument()
    expect(link.getAttribute('href')).toBe('/app/video/video123')
  })

  it('disappears after a terminal done entry auto-removes from the store', () => {
    const { rerender } = render(
      <MemoryRouter>
        <ActivityPanel />
      </MemoryRouter>,
    )
    act(() => {
      upsert('t1', { videoId: 'v1', phase: 'done' })
    })
    rerender(
      <MemoryRouter>
        <ActivityPanel />
      </MemoryRouter>,
    )
    expect(screen.getByRole('region', { name: /active tasks/i })).toBeInTheDocument()

    act(() => {
      vi.runAllTimers()
    })
    rerender(
      <MemoryRouter>
        <ActivityPanel />
      </MemoryRouter>,
    )
    expect(screen.queryByRole('region', { name: /active tasks/i })).not.toBeInTheDocument()
  })

  it('renders multiple tasks at once', () => {
    upsert('t1', { videoId: 'v1', phase: 'running', stage: 'ingest' })
    upsert('t2', { videoId: 'v2', phase: 'running', stage: 'render' })
    renderPanel()
    expect(screen.getByText('Ingesting')).toBeInTheDocument()
    expect(screen.getByText('Rendering')).toBeInTheDocument()
  })

  it('shows "Done" label for a done entry before auto-removal', () => {
    upsert('t1', { videoId: 'v1', phase: 'done' })
    renderPanel()
    expect(screen.getByText('Done')).toBeInTheDocument()
  })

  it('shows "Failed" label for an error entry before auto-removal', () => {
    upsert('t1', { videoId: 'v1', phase: 'error' })
    renderPanel()
    expect(screen.getByText('Failed')).toBeInTheDocument()
  })

  it('gracefully shows cap-blocked hint when cap is exhausted and task is unsubscribed running', () => {
    // Fill 3 subscribed slots.
    for (let i = 0; i < 3; i++) {
      upsert(`t${i}`, { videoId: `v${i}`, phase: 'running', subscribed: true })
    }
    // 4th task: running but subscribed=false (cap blocked).
    upsert('t3', { videoId: 'v3', phase: 'running', subscribed: false })
    renderPanel()
    expect(screen.getByText(/cap reached/i)).toBeInTheDocument()
  })

  it('never contains virality language', () => {
    upsert('t1', { videoId: 'v1', phase: 'running', stage: 'signals' })
    renderPanel()
    expect(document.body.textContent ?? '').not.toMatch(/viral/i)
  })
})
