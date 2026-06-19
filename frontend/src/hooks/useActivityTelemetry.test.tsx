import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useActivityTelemetry } from './useActivityTelemetry'

// Mounts the telemetry hook over a tiny two-route app so we can assert the three
// event types land on /api/activity (Issue 155).
function Harness() {
  useActivityTelemetry()
  return (
    <Routes>
      <Route path="a" element={<Link to="/b">Go B</Link>} />
      <Route path="b" element={<button>Click me</button>} />
    </Routes>
  )
}

function lastBody(fetchMock: ReturnType<typeof vi.fn>) {
  const call = fetchMock.mock.calls.at(-1)
  return JSON.parse(String((call?.[1] as RequestInit | undefined)?.body))
}

describe('useActivityTelemetry', () => {
  let fetchMock: ReturnType<typeof vi.fn>
  beforeEach(() => {
    fetchMock = vi.fn(async () => ({ ok: true, status: 204 }))
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('emits navigate on load + route change and click events to /api/activity', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter basename="/app" initialEntries={['/app/a']}>
        <Harness />
      </MemoryRouter>,
    )

    // navigate on the initial load
    expect(fetchMock).toHaveBeenCalledWith('/api/activity', expect.objectContaining({ method: 'POST' }))
    expect(lastBody(fetchMock)).toMatchObject({ event_type: 'navigate', target: '/a' })

    // following the link emits a click, then a navigate for the new route
    await user.click(screen.getByRole('link', { name: 'Go B' }))
    expect(lastBody(fetchMock)).toMatchObject({ event_type: 'navigate', target: '/b' })

    // a plain button click is captured via the delegated listener
    await user.click(screen.getByRole('button', { name: 'Click me' }))
    expect(lastBody(fetchMock)).toMatchObject({ event_type: 'click', target: 'Click me' })
  })
})
