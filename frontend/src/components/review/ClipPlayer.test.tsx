import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ClipPlayer } from './ClipPlayer'
import type { ReviewClip } from '@/types'

const BASE: ReviewClip = {
  id: 'c1',
  video_id: 'v1',
  setup_start_s: 2,
  start_s: 0,
  end_s: 20,
  peak_s: 10,
  score: 0.9,
  rank: 1,
  principle: 'Curiosity gap',
  reasoning: 'Strong hook.',
  render_status: 'done',
  render_uri: 'http://x/c1.mp4',
  cleaned_render_uri: null,
  applied_title: null,
  applied_description: null,
}

function renderPlayer(clip: ReviewClip) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ClipPlayer
          clip={clip}
          trimStart={0}
          trimEnd={20}
          onTrimChange={() => {}}
          onNext={() => {}}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('ClipPlayer render states', () => {
  it('plays the video when the clip is rendered', () => {
    renderPlayer(BASE)
    expect(document.querySelector('video')).toBeInTheDocument()
    expect(screen.queryByText(/Render this clip/)).not.toBeInTheDocument()
  })

  it('autoplays muted with preload so Chrome does not block on a black frame (Issue 359d)', () => {
    // Chrome blocks unmuted autoplay: the element would stay paused on a black
    // first frame until a user gesture — the "black render" symptom.
    renderPlayer(BASE)
    const video = document.querySelector('video')!
    expect(video).toHaveAttribute('autoplay')
    expect(video.muted).toBe(true)
    expect(video).toHaveAttribute('preload', 'auto')
  })

  it('shows a "Rendering…" status while a render is in flight (no manual button)', () => {
    renderPlayer({ ...BASE, render_status: 'running', render_uri: null })
    expect(screen.getByText(/Rendering your clip/)).toBeInTheDocument()
    expect(screen.queryByText(/Render this clip/)).not.toBeInTheDocument()
  })

  it('offers a manual render button when pending, and POSTs on click', async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) => ({
        status: 202,
        ok: true,
        json: async () => ({}),
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    renderPlayer({ ...BASE, render_status: 'pending', render_uri: null })

    const btn = screen.getByText(/Render this clip/)
    await userEvent.click(btn)

    await waitFor(() => {
      const called = fetchMock.mock.calls.some(
        (c) => String(c[0]).endsWith('/clips/c1/render') && c[1]?.method === 'POST',
      )
      expect(called).toBe(true)
    })
  })

  it('offers a retry when a render failed', () => {
    renderPlayer({ ...BASE, render_status: 'failed', render_uri: null })
    expect(screen.getByText(/Render failed/)).toBeInTheDocument()
    expect(screen.getByText(/Retry render/)).toBeInTheDocument()
  })

  it('does not latch the spinner after a render request settles (the render-loop bug)', async () => {
    // Regression: a render that can never produce a render_uri (e.g. source media
    // purged → render_status stays 'failed') must NOT spin forever. Previously
    // triggerRender set `requesting=true` and never reset it on the 202/409 path,
    // so the "Rendering your clip…" spinner latched permanently.
    const fetchMock = vi.fn(async () => ({ status: 202, ok: true, json: async () => ({}) }))
    vi.stubGlobal('fetch', fetchMock)
    // Server truth for this clip is terminal-failed (source gone); render_uri never lands.
    renderPlayer({ ...BASE, render_status: 'failed', render_uri: null })

    await userEvent.click(screen.getByText(/Retry render/))

    // Once the request settles, the spinner clears and the server's failed state shows
    // through again — not a perpetual "Rendering…".
    await waitFor(() => {
      expect(screen.queryByText(/Rendering your clip/)).not.toBeInTheDocument()
    })
    expect(screen.getByText(/Render failed/)).toBeInTheDocument()
  })

  it('shows the source-expired card (not a spinner) on the structured 409', async () => {
    // 409 {code: source_expired}: the retention purge removed the source, so a
    // retry can never succeed — a dedicated card with a re-upload CTA replaces
    // the invalidate-and-poll path.
    const fetchMock = vi.fn(async () => ({
      status: 409,
      ok: false,
      json: async () => ({
        detail: {
          code: 'source_expired',
          message:
            'Source media expired (72-hour retention) — re-upload the video to render this clip.',
        },
      }),
    }))
    vi.stubGlobal('fetch', fetchMock)
    renderPlayer({ ...BASE, render_status: 'pending', render_uri: null })

    await userEvent.click(screen.getByText(/Render this clip/))

    expect(await screen.findByText('Source media expired')).toBeInTheDocument()
    const cta = screen.getByRole('link', { name: /Upload again/ })
    expect(cta).toHaveAttribute('href', '/dashboard')
    expect(screen.queryByText(/Rendering your clip/)).not.toBeInTheDocument()
    // Never the raw object rendering that bit the old ApiError.
    expect(document.body.textContent).not.toContain('[object Object]')
  })

  it('still treats a plain-string 409 as render-already-running (regression)', async () => {
    const fetchMock = vi.fn(async () => ({
      status: 409,
      ok: false,
      json: async () => ({ detail: 'Render already in progress' }),
    }))
    vi.stubGlobal('fetch', fetchMock)
    renderPlayer({ ...BASE, render_status: 'pending', render_uri: null })

    await userEvent.click(screen.getByText(/Render this clip/))

    // Invalidate-and-poll path: no error text and no source-expired card.
    await waitFor(() => {
      expect(screen.queryByText(/Rendering your clip/)).not.toBeInTheDocument()
    })
    expect(screen.queryByText(/Render already in progress/)).not.toBeInTheDocument()
    expect(screen.queryByText('Source media expired')).not.toBeInTheDocument()
  })
})
