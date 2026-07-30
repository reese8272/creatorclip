import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useSearchParams } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { VideoPickerLanding } from './VideoPickerLanding'

// The landing's InlineUploadFlow pulls in the SSE hook; never open a real stream.
vi.mock('@/lib/taskStream', () => ({
  subscribeToTaskStream: vi.fn(() => ({ close: vi.fn() })),
}))

function video(over: Record<string, unknown> = {}) {
  return {
    id: 'v1', youtube_video_id: null, title: 'Done with clips', kind: 'video',
    ingest_status: 'done', failure_reason: null, duration_s: 300,
    created_at: '2026-07-01T00:00:00Z', origin: 'upload', clippable: true,
    ...over,
  }
}

const VIDEOS = [
  video(),
  video({ id: 'v2', title: 'Done no clips' }),
  video({ id: 'v3', title: 'Still running', ingest_status: 'running' }),
  video({ id: 'v4', title: 'Broken', ingest_status: 'failed', failure_reason: 'audio track missing' }),
  video({ id: 'v5', title: 'Linked only', ingest_status: 'pending', clippable: false }),
]

const COUNTS = {
  counts: [
    { video_id: 'v1', total: 3, rendered: 2 },
    { video_id: 'v2', total: 0, rendered: 0 },
  ],
}

const GENERATED_CLIP = {
  id: 'c1', video_id: 'v2', setup_start_s: 1, start_s: 0, end_s: 20, peak_s: 8,
  score: 0.8, rank: 1, principle: 'Curiosity gap', reasoning: 'Hook.',
  render_status: 'pending', render_uri: null, cleaned_render_uri: null,
  applied_title: null, applied_description: null,
}

function mockFetch(overrides: Record<string, () => unknown> = {}) {
  const json = (body: unknown) => ({ status: 200, ok: true, json: async () => body })
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    for (const [suffix, respond] of Object.entries(overrides)) {
      if (url.endsWith(suffix)) return respond()
    }
    if (url.endsWith('/videos')) return json({ videos: VIDEOS, state: 'populated' })
    if (url.endsWith('/videos/clips/counts')) return json(COUNTS)
    if (url.endsWith('/clips/generate')) return json({ clips: [GENERATED_CLIP] })
    return json({})
  })
}

// Observes the search param the landing sets on row selection.
function Probe() {
  const [params] = useSearchParams()
  return <div data-testid="picked">{params.get('video_id') ?? ''}</div>
}

function renderLanding() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter basename="/app" initialEntries={['/app/review']}>
        <Routes>
          <Route
            path="review"
            element={
              <>
                <VideoPickerLanding tool="review" />
                <Probe />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('VideoPickerLanding', () => {
  it('renders the honest per-row state matrix', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderLanding()
    // done + clips → open CTA (exactly one — only v1 qualifies)
    expect(await screen.findAllByRole('button', { name: 'Review clips' })).toHaveLength(1)
    // done + 0 clips → generate CTA + transparency link
    expect(screen.getByRole('button', { name: 'Generate clips' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Why no clips\?/ })).toBeInTheDocument()
    // in-flight → static copy, no live stepper, not selectable
    expect(screen.getByText(/Processing — a few minutes/)).toBeInTheDocument()
    // failed → reason + retry
    expect(screen.getByText('audio track missing')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry analysis' })).toBeInTheDocument()
    // no stored source → honest upload hint
    expect(screen.getByText('Upload source file to clip')).toBeInTheDocument()
  })

  it('sets ?video_id= when a ready row is picked', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderLanding()
    await userEvent.click(await screen.findByRole('button', { name: 'Review clips' }))
    expect(screen.getByTestId('picked')).toHaveTextContent('v1')
  })

  it('generates clips in place and advances when clips come back', async () => {
    const fetchMock = mockFetch()
    vi.stubGlobal('fetch', fetchMock)
    renderLanding()
    await userEvent.click(await screen.findByRole('button', { name: 'Generate clips' }))
    await waitFor(() => expect(screen.getByTestId('picked')).toHaveTextContent('v2'))
    expect(
      fetchMock.mock.calls.filter(([u]) => String(u).endsWith('/videos/v2/clips/generate')),
    ).toHaveLength(1)
  })

  it('stays put with honest copy when generation returns zero clips', async () => {
    vi.stubGlobal('fetch', mockFetch({
      '/clips/generate': () => ({ status: 200, ok: true, json: async () => ({ clips: [] }) }),
    }))
    renderLanding()
    await userEvent.click(await screen.findByRole('button', { name: 'Generate clips' }))
    expect(
      await screen.findByText(/No strong moments matched your clipping principles/),
    ).toBeInTheDocument()
    expect(screen.getByTestId('picked')).toHaveTextContent('')
  })

  it('surfaces the server-authored billing error verbatim on generate failure', async () => {
    vi.stubGlobal('fetch', mockFetch({
      '/clips/generate': () => ({
        status: 402, ok: false,
        json: async () => ({ detail: 'Not enough minutes: this video needs 5, you have 2.' }),
      }),
    }))
    renderLanding()
    await userEvent.click(await screen.findByRole('button', { name: 'Generate clips' }))
    expect(
      await screen.findByText('Not enough minutes: this video needs 5, you have 2.'),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry generate' })).toBeInTheDocument()
  })

  it('shows a retry card — not the empty state — when the videos query fails', async () => {
    vi.stubGlobal('fetch', mockFetch({
      '/videos': () => ({ status: 500, ok: false, json: async () => ({ detail: 'boom' }) }),
    }))
    renderLanding()
    expect(await screen.findByText(/Couldn.t load your videos/)).toBeInTheDocument()
    expect(screen.queryByText(/No videos yet/)).toBeNull()
  })

  it('never renders virality promise language', async () => {
    vi.stubGlobal('fetch', mockFetch())
    const { container } = renderLanding()
    await screen.findByText(/Pick a video to review/i)
    const text = container.textContent ?? ''
    expect(text).not.toMatch(/\bpromises virality\b|\bguarantees performance\b|\bgo viral\b/i)
  })
})
