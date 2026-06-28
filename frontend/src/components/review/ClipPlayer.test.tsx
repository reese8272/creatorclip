import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
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
}

function renderPlayer(clip: ReviewClip) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <ClipPlayer
        clip={clip}
        trimStart={0}
        trimEnd={20}
        onTrimChange={() => {}}
        onNext={() => {}}
      />
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
})
