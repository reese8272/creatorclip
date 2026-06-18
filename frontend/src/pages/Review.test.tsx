import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Review } from './Review'

function mockFetch() {
  const json = (body: unknown) => ({ status: 200, ok: true, json: async () => body })
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/videos/v1/clips'))
      return json({
        clips: [
          {
            id: 'c1', video_id: 'v1', setup_start_s: 2, start_s: 0, end_s: 20, peak_s: 10,
            score: 0.91, rank: 1, principle: 'Curiosity gap', reasoning: 'Strong hook in 3s.',
            render_status: 'done', render_uri: 'http://x/c1.mp4', cleaned_render_uri: null,
          },
        ],
      })
    if (url.endsWith('/clips/c1/transcript'))
      return json({
        clip_id: 'c1',
        clip_duration_s: 20,
        words: [
          { word: 'Hello', start_s: 0, end_s: 1, index: 0 },
          { word: 'world', start_s: 1, end_s: 2, index: 1 },
        ],
      })
    if (url.endsWith('/clips/c1/feedback')) return json({ ok: true })
    return json({})
  })
}

function renderReview(entry: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter basename="/app" initialEntries={[entry]}>
        <Routes>
          <Route path="review" element={<Review />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('Review', () => {
  it('prompts to pick a video when no video_id is present', () => {
    vi.stubGlobal('fetch', mockFetch())
    renderReview('/app/review')
    expect(screen.getByText(/No video selected/)).toBeInTheDocument()
  })

  it('loads the clip: player meta, why-this-clip reasoning, transcript words, honesty disclaimer', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderReview('/app/review?video_id=v1')
    expect(await screen.findByText(/Clip #1/)).toBeInTheDocument()
    expect(screen.getByText('Strong hook in 3s.')).toBeInTheDocument() // Why-this-clip is default-open
    expect(await screen.findByText('Hello')).toBeInTheDocument() // transcript word span (async load)
    expect(screen.getByText(/does not promise virality/i)).toBeInTheDocument()
  })

  it('opens the tag-feedback panel when Keep is clicked', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderReview('/app/review?video_id=v1')
    await screen.findByText(/Clip #1/)
    await userEvent.click(screen.getByRole('button', { name: '👍 Keep' }))
    expect(screen.getByText('Why are you keeping this?')).toBeInTheDocument()
  })
})
