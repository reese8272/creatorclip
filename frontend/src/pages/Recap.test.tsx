/**
 * Tests for Issue 192 — Recap page.
 *
 * Covers:
 *  - Empty state: "Create recap" CTA enabled, no player
 *  - Request flow: POST fired, render followed over the task SSE (step → done
 *    invalidates the summaries query)
 *  - Rendered state: 16:9 player streams /summaries/{id}/download?disposition=inline
 *  - Segments render chronologically with FitBadge tier + rationale (never raw score)
 *  - CTA gating: disabled while a render is in flight
 *  - Honest 4xx detail surfaced; no virality language (structural honesty check)
 */
import { render, screen, act, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Recap } from './Recap'
import type { Summary } from '@/types'

// FakeEventSource (VideoTable.test idiom) — emits named SSE events by hand.
class FakeEventSource {
  static instances: FakeEventSource[] = []
  url: string
  closed = false
  private listeners: Record<string, ((e: MessageEvent) => void)[]> = {}

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, cb: (e: MessageEvent) => void) {
    ;(this.listeners[type] ??= []).push(cb)
  }

  emit(type: string, data: unknown) {
    const evt = { data: JSON.stringify(data) } as MessageEvent
    ;(this.listeners[type] ?? []).forEach((cb) => cb(evt))
  }

  close() {
    this.closed = true
  }
}

function makeSummary(over: Partial<Summary> = {}): Summary {
  return {
    id: 's1',
    video_id: 'v1',
    status: 'ready',
    render_status: 'done',
    target_duration_s: 600,
    segments: [
      {
        start_s: 620,
        end_s: 680,
        score: 0.5,
        principle: 'PAYOFF_PROXIMITY',
        rationale: 'The payoff lands within the window.',
      },
      {
        start_s: 15,
        end_s: 75,
        score: 0.9,
        principle: 'HOOK_SETUP',
        rationale: 'Clear setup into a strong beat.',
      },
    ],
    render_uri: 's3://bucket/recaps/s1.mp4',
    created_at: '2026-07-01T00:00:00Z',
    ...over,
  }
}

function mockFetch(summaries: Summary[], postResponse?: { status: number; body: unknown }) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const json = (body: unknown, status = 200) => ({
      status,
      ok: status < 400,
      json: async () => body,
    })
    if (url.includes('/api/activity')) return json({})
    if (init?.method === 'POST' && url.match(/\/videos\/[^/]+\/summaries$/)) {
      if (postResponse) return json(postResponse.body, postResponse.status)
      return json({ summary_id: 's-new', status: 'queued', stream_url: '/tasks/s-new/events' }, 202)
    }
    if (url.match(/\/videos\/[^/]+\/summaries$/)) return json({ summaries })
    return json({})
  })
}

function renderRecap(videoId = 'v1') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter basename="/app" initialEntries={[`/app/video/${videoId}/recap`]}>
        <Routes>
          <Route path="video/:videoId/recap" element={<Recap />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  FakeEventSource.instances = []
  vi.stubGlobal('EventSource', FakeEventSource)
})
afterEach(() => vi.unstubAllGlobals())

describe('Recap', () => {
  it('shows the Create recap CTA (enabled) and no player when no recap exists', async () => {
    vi.stubGlobal('fetch', mockFetch([]))
    const { baseElement } = renderRecap()
    const btn = await screen.findByRole('button', { name: 'Create recap' })
    expect(btn).toBeEnabled()
    expect(baseElement.querySelector('video')).toBeNull()
  })

  it('requesting a recap POSTs, follows the SSE, and invalidates on done', async () => {
    const fetchMock = mockFetch([])
    vi.stubGlobal('fetch', fetchMock)
    renderRecap()
    await userEvent.click(await screen.findByRole('button', { name: 'Create recap' }))

    // POST fired at the video-scoped route.
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          ([u, init]) =>
            String(u).endsWith('/videos/v1/summaries') &&
            (init as RequestInit | undefined)?.method === 'POST',
        ),
      ).toBe(true)
    })

    // The returned stream_url is followed live.
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    const es = FakeEventSource.instances[0]
    expect(es.url).toBe('/tasks/s-new/events')

    act(() => es.emit('step', { label: 'Cutting segments' }))
    expect(await screen.findByText('Cutting segments')).toBeInTheDocument()

    const getsBefore = fetchMock.mock.calls.filter(
      ([u, init]) =>
        String(u).endsWith('/videos/v1/summaries') &&
        (init as RequestInit | undefined)?.method !== 'POST',
    ).length
    act(() => es.emit('done', {}))
    // done bridges back into the query cache — the summaries list refetches.
    await waitFor(() => {
      const getsAfter = fetchMock.mock.calls.filter(
        ([u, init]) =>
          String(u).endsWith('/videos/v1/summaries') &&
          (init as RequestInit | undefined)?.method !== 'POST',
      ).length
      expect(getsAfter).toBeGreaterThan(getsBefore)
    })
  })

  it('streams the rendered recap through the inline download endpoint in a 16:9 player', async () => {
    vi.stubGlobal('fetch', mockFetch([makeSummary()]))
    const { baseElement } = renderRecap()
    await waitFor(() => {
      const video = baseElement.querySelector('video')
      expect(video).not.toBeNull()
      expect(video?.getAttribute('src')).toBe('/summaries/s1/download?disposition=inline')
      expect(video?.className).toContain('aspect-video')
    })
  })

  it('renders segments chronologically with the fit tier badge and rationale', async () => {
    vi.stubGlobal('fetch', mockFetch([makeSummary()]))
    renderRecap()
    const list = await screen.findByRole('list')
    const rows = list.querySelectorAll('li')
    expect(rows).toHaveLength(2)
    // Chronological, not score order: the 0:15 segment leads despite arriving second.
    expect(rows[0].textContent).toContain('0:15–1:15')
    expect(rows[0].textContent).toContain('Strong channel fit')
    expect(rows[0].textContent).toContain('Clear setup into a strong beat.')
    expect(rows[1].textContent).toContain('10:20–11:20')
    expect(rows[1].textContent).toContain('Moderate channel fit')
    // Raw scores are never the headline.
    expect(list.textContent).not.toContain('0.9')
  })

  it('disables the CTA while a render is in flight', async () => {
    vi.stubGlobal('fetch', mockFetch([makeSummary({ render_status: 'running', render_uri: null })]))
    renderRecap()
    const btn = await screen.findByRole('button', { name: 'Recap rendering…' })
    expect(btn).toBeDisabled()
  })

  it('polls while a render is in flight and settles without a reload, then stops', async () => {
    // The SSE is best-effort (null stream_url on a Redis blip; opening the page
    // mid-render never had a stream) — the summaries poll is the fallback that
    // clears "Recap rendering…" without a manual reload.
    vi.useFakeTimers()
    try {
      let summaries: Summary[] = [makeSummary({ render_status: 'running', render_uri: null })]
      const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.match(/\/videos\/[^/]+\/summaries$/))
          return { status: 200, ok: true, json: async () => ({ summaries }) }
        return { status: 200, ok: true, json: async () => ({}) }
      })
      vi.stubGlobal('fetch', fetchMock)
      renderRecap()
      const gets = () =>
        fetchMock.mock.calls.filter(([u]) => String(u).endsWith('/videos/v1/summaries')).length
      const tick = (ms: number) => act(async () => vi.advanceTimersByTimeAsync(ms))

      await tick(20)
      expect(screen.getByRole('button', { name: 'Recap rendering…' })).toBeDisabled()

      // In flight → the list refetches on the poll interval.
      const before = gets()
      await tick(4100)
      expect(gets()).toBeGreaterThan(before)

      // Server settles the render — the next poll clears the latched state.
      summaries = [makeSummary()]
      await tick(4100)
      expect(screen.getByRole('button', { name: 'Create a new recap' })).toBeEnabled()

      // Settled → polling stops.
      const settled = gets()
      await tick(9000)
      expect(gets()).toBe(settled)
    } finally {
      vi.useRealTimers()
    }
  })

  it('surfaces the honest server detail when the request is rejected', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch([], {
        status: 422,
        body: {
          detail:
            'Not enough scored material yet — generate clips for this video first, ' +
            'then request a recap.',
        },
      }),
    )
    renderRecap()
    await userEvent.click(await screen.findByRole('button', { name: 'Create recap' }))
    expect(await screen.findByText(/Not enough scored material yet/i)).toBeInTheDocument()
  })

  it('contains no virality language (honesty constraint)', async () => {
    vi.stubGlobal('fetch', mockFetch([makeSummary()]))
    const { baseElement } = renderRecap()
    await screen.findByRole('list')
    const text = baseElement.textContent ?? ''
    // Forbidden: positive virality promises. "not a guarantee" is the correct
    // honest framing (Issue 217 precedent), so only positive claims are banned.
    expect(text).not.toMatch(/\bviral\b/i)
    expect(text).not.toMatch(/\bvirality\b/i)
    expect(text).not.toMatch(/guaranteed to/i)
    expect(text).not.toMatch(/\bpromises?\b/i)
  })
})
