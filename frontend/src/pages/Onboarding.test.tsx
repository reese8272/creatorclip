import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Onboarding } from './Onboarding'

// Minimal EventSource stub — the same pattern used in useTaskStream.test.ts. The
// page uses EventSource only when a stream URL is already set (e.g. from
// sessionStorage), so tests that never set a URL don't need the stub.
class FakeEventSource {
  static instances: FakeEventSource[] = []
  url: string
  closed = false
  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }
  addEventListener(_type: string, _cb: unknown) {}
  close() { this.closed = true }
}

// Route the page's several reads by URL. `identity` is the load-bearing knob:
// the Build-DNA step stays locked until an identity row exists (Issue 100).
function mockFetch(opts: { identity?: unknown; ready?: boolean } = {}) {
  const json = (body: unknown) => ({ status: 200, ok: true, json: async () => body })
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/auth/me')) return json({ channel_title: 'Reese TV' })
    if (url.endsWith('/billing/balance')) return json({ minutes_balance: 50, low_balance: false })
    if (url.endsWith('/data-gate'))
      return json({
        long_form_videos: opts.ready ? 12 : 1,
        shorts: opts.ready ? 6 : 3,
        long_form_ready: !!opts.ready,
        shorts_ready: !!opts.ready,
        // Server-computed unlock deltas (Issue 203) — thresholds 10 long / 5 short.
        remaining_long_form: opts.ready ? 0 : 9,
        remaining_shorts: opts.ready ? 0 : 2,
        ready: !!opts.ready,
      })
    if (url.endsWith('/creators/me/dna')) return json({ profile: null })
    if (url.endsWith('/creators/me/identity')) return json({ identity: opts.identity ?? null })
    if (url.endsWith('/creators/niches')) return json({ options: [{ id: 'gaming', label: 'Gaming' }] })
    return json({})
  })
}

function renderOnboarding() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter basename="/app" initialEntries={['/app/onboarding']}>
        <Routes>
          <Route path="onboarding" element={<Onboarding />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  // Wipe sessionStorage between tests so re-attach tests start clean.
  sessionStorage.clear()
  FakeEventSource.instances = []
})
afterEach(() => vi.unstubAllGlobals())

describe('Onboarding', () => {
  it('shows the connected channel, the honesty disclaimer, and data-gate readiness', async () => {
    vi.stubGlobal('fetch', mockFetch({ ready: true }))
    renderOnboarding()
    expect(await screen.findByText(/Connected as Reese TV/)).toBeInTheDocument()
    expect(screen.getByText(/does not promise virality/i)).toBeInTheDocument()
    expect(await screen.findByText('Ready to build your Creator DNA.')).toBeInTheDocument()
  })

  // Issue 203: per-kind unlock deltas are server-computed and rendered verbatim,
  // and the not-ready state offers an honest clip-now path instead of a waiting room.
  it('renders per-kind unlock deltas and a clip-now CTA when the gate is not ready', async () => {
    vi.stubGlobal('fetch', mockFetch({ ready: false }))
    renderOnboarding()
    expect(
      await screen.findByText(/9 more published long-form videos to unlock Creator DNA/),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/2 more published Shorts to unlock Creator DNA/),
    ).toBeInTheDocument()
    // Honest small-catalog copy: clipping works now, personalisation comes later.
    expect(
      screen.getByText(/clip videos right now with signal-based scoring/i),
    ).toBeInTheDocument()
    const cta = screen.getByRole('link', { name: /Clip a video now/ })
    expect(cta).toHaveAttribute('href', '/app/dashboard')
  })

  it('drops the deltas and the clip-now CTA once the gate is ready', async () => {
    vi.stubGlobal('fetch', mockFetch({ ready: true }))
    renderOnboarding()
    expect(await screen.findByText('Ready to build your Creator DNA.')).toBeInTheDocument()
    expect(screen.queryByText(/to unlock Creator DNA/)).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Clip a video now/ })).not.toBeInTheDocument()
  })

  // Escape hatch: a connected creator can skip setup and go straight to the
  // dashboard (setup is resumable). The link is gated on a resolved user.
  it('offers a skip-to-dashboard link once the channel is connected', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderOnboarding()
    const skip = await screen.findByRole('link', { name: /Skip to dashboard/ })
    expect(skip).toHaveAttribute('href', '/app/dashboard')
  })

  // OAuth-verification gate (Issue 153): this first-run flow sits outside AppChrome,
  // so it must carry the ToS/Privacy footer links itself.
  it('exposes the ToS and Privacy footer links', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderOnboarding()
    expect(await screen.findByRole('link', { name: 'Terms' })).toHaveAttribute(
      'href',
      '/static/tos.html',
    )
    expect(screen.getByRole('link', { name: 'Privacy' })).toHaveAttribute(
      'href',
      '/static/privacy.html',
    )
  })

  // Issue 204: intake is genuinely optional — Build-DNA must NOT be gated on an
  // identity row, and the copy must not contradict the "optional" label.
  it('keeps Build-DNA available without an identity, framing intake as optional', async () => {
    vi.stubGlobal('fetch', mockFetch({ identity: null }))
    renderOnboarding()
    const build = await screen.findByRole('button', { name: 'Build Creator DNA' })
    expect(build).toBeEnabled()
    expect(screen.queryByText(/Finish step 3 first/)).not.toBeInTheDocument()
    expect(screen.getByText(/build from your video data now/i)).toBeInTheDocument()
  })

  it('drops the optional-intake nudge once the creator has an identity on file', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch({ identity: { version: 1, niches: ['gaming'], audience_summary: 'gamers' } }),
    )
    renderOnboarding()
    // Build-DNA is always available now; the sync point is the identity query
    // resolving, after which the optional-intake nudge drops.
    await waitFor(() =>
      expect(screen.queryByText(/build from your video data now/i)).not.toBeInTheDocument(),
    )
    expect(screen.getByRole('button', { name: 'Build Creator DNA' })).toBeEnabled()
  })

  // Issue 214: TaskStepper replaces StreamConsole; re-attach via sessionStorage.
  it('re-attaches to a previously started catalog stream from sessionStorage on remount', async () => {
    vi.stubGlobal('fetch', mockFetch())
    vi.stubGlobal('EventSource', FakeEventSource)
    // Simulate a URL written by a previous session.
    sessionStorage.setItem('onboarding:catalogUrl', '/tasks/123/events')
    renderOnboarding()
    // The page initialises catalogUrl from sessionStorage, so useTaskStream opens an
    // EventSource for /tasks/123/events on mount. Assert the connection was created.
    expect(FakeEventSource.instances).toHaveLength(1)
    expect(FakeEventSource.instances[0].url).toBe('/tasks/123/events')
  })

  it('does not render a countdown or fabricated ETA during streaming', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderOnboarding()
    // There should never be any text like "ETA", "minutes remaining", or a countdown pattern.
    expect(screen.queryByText(/ETA/i)).toBeNull()
    expect(screen.queryByText(/remaining/i)).toBeNull()
    expect(screen.queryByText(/\d+:\d{2}/)).toBeNull()
  })
})
