import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Editor } from './Editor'

// Stub AudioContext so WebAudio waveform decode does not throw in jsdom.
;(globalThis as unknown as Record<string, unknown>).AudioContext = undefined
;(globalThis as unknown as Record<string, unknown>).webkitAudioContext = undefined

const BASE_CLIP = {
  id: 'c1',
  video_id: 'v1',
  setup_start_s: 2,
  start_s: 0,
  end_s: 20,
  peak_s: 10,
  score: 0.82,
  rank: 1,
  principle: 'Curiosity gap',
  reasoning: 'Strong hook.',
  render_status: 'done',
  render_uri: 'http://cdn/c1.mp4',
  cleaned_render_uri: null,
  applied_title: null,
  applied_description: null,
}

const TRANSCRIPT = {
  clip_id: 'c1',
  clip_duration_s: 20,
  words: [
    { word: 'Hello', start_s: 0, end_s: 1, index: 0 },
    { word: 'world', start_s: 1, end_s: 2, index: 1 },
    { word: 'this', start_s: 2, end_s: 3, index: 2 },
    { word: 'is', start_s: 3, end_s: 4, index: 3 },
    { word: 'a', start_s: 4, end_s: 5, index: 4 },
    { word: 'clip', start_s: 5, end_s: 6, index: 5 },
  ],
}

// Row for the standalone picker landing (no-param /editor).
const BASE_VIDEO = {
  id: 'v1',
  youtube_video_id: 'yt1',
  title: 'My stream VOD',
  kind: 'video',
  ingest_status: 'done',
  failure_reason: null,
  duration_s: 300,
  created_at: '2026-07-01T00:00:00Z',
  origin: 'upload',
  clippable: true,
}

function mockFetch() {
  const json = (body: unknown) => ({ status: 200, ok: true, json: async () => body })
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/videos')) return json({ videos: [BASE_VIDEO], state: 'populated' })
    if (url.endsWith('/videos/clips/counts'))
      return json({ counts: [{ video_id: 'v1', total: 1, rendered: 1 }] })
    if (url.includes('/videos/v1/clips')) return json({ clips: [BASE_CLIP], personalization: null })
    if (url.includes('/clips/c1/transcript')) return json(TRANSCRIPT)
    if (url.includes('/clips/c1/download')) return new Response(new ArrayBuffer(0), { status: 200 })
    return json({})
  })
}

function renderEditor(entry: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter basename="/app" initialEntries={[entry]}>
        <Routes>
          <Route path="editor" element={<Editor />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('Editor', () => {
  it('shows the standalone picker when no video_id is present, and a row click opens long-form mode', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderEditor('/app/editor')
    expect(await screen.findByText(/Pick a video to edit/i)).toBeInTheDocument()
    // The old dead end (bounce to Review) is gone — Editor is a standalone tool.
    expect(screen.queryByRole('button', { name: /Go to Review/i })).toBeNull()
    await userEvent.click(await screen.findByRole('button', { name: 'Open in editor' }))
    expect(await screen.findByText('Suggested clips')).toBeInTheDocument()
  })

  it('renders the editor with clip meta and honesty disclaimer', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderEditor('/app/editor?video_id=v1&clip_id=c1')
    // Honesty constraint — present on every interface (CLAUDE.md)
    expect(await screen.findByText(/does not promise virality/i)).toBeInTheDocument()
    // Clip rank
    expect(await screen.findByText(/Clip #1/i)).toBeInTheDocument()
    // Fit badge (score 0.82 → "Strong channel fit")
    expect(await screen.findByText(/Strong channel fit/i)).toBeInTheDocument()
  })

  it('renders the timeline scrubber', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderEditor('/app/editor?video_id=v1&clip_id=c1')
    await screen.findByText(/Clip #1/i)
    expect(screen.getByRole('slider', { name: /timeline scrubber/i })).toBeInTheDocument()
  })

  it('renders transcript words from the API', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderEditor('/app/editor?video_id=v1&clip_id=c1')
    expect(await screen.findByText('Hello')).toBeInTheDocument()
    expect(await screen.findByText('world')).toBeInTheDocument()
  })

  it('renders the Back to Review navigation link', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderEditor('/app/editor?video_id=v1&clip_id=c1')
    await screen.findByText(/Clip #1/i)
    expect(screen.getByRole('button', { name: /Back to Review/i })).toBeInTheDocument()
  })

  it('shows the caption style collapsible tool in the right rail', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderEditor('/app/editor?video_id=v1&clip_id=c1')
    await screen.findByText(/Clip #1/i)
    // "Caption style" appears as both the collapsible button and the inner label,
    // so getAllByText is appropriate here.
    expect(screen.getAllByText('Caption style').length).toBeGreaterThan(0)
    expect(screen.getByText('Clean filler + silence')).toBeInTheDocument()
  })

  // ── Issue 307: mode toggle + long-form source mode ──
  it('shows the short|long mode toggle (Issue 307)', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderEditor('/app/editor?video_id=v1&clip_id=c1')
    await screen.findByText(/Clip #1/i)
    expect(screen.getByRole('tab', { name: /Short-form clip/i })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /Long-form source/i })).toBeInTheDocument()
  })

  it('switches to long-form source mode and lists suggested clips (Issue 307)', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderEditor('/app/editor?video_id=v1&clip_id=c1')
    await screen.findByText(/Clip #1/i)
    await userEvent.click(screen.getByRole('tab', { name: /Long-form source/i }))
    expect(screen.getByText('Suggested clips')).toBeInTheDocument()
    // Honest placeholder for the un-backed full-source surfaces (scaffold scope).
    expect(screen.getByText(/Full-source preview isn’t available/i)).toBeInTheDocument()
  })

  it('opens /editor?video_id (no clip) directly in long-form mode (Issue 307)', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderEditor('/app/editor?video_id=v1')
    expect(await screen.findByText('Suggested clips')).toBeInTheDocument()
  })

  // ── Issue 361 sweep: query failure must not read as the no-clips UI ──
  it('shows a retry card — not the no-clips UI — when the clips query fails', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/videos/v1/clips'))
        return { status: 500, ok: false, json: async () => ({ detail: 'boom' }) }
      return { status: 200, ok: true, json: async () => ({}) }
    })
    vi.stubGlobal('fetch', fetchMock)
    renderEditor('/app/editor?video_id=v1&clip_id=c1')
    expect(await screen.findByText(/Couldn’t load clips for this video/)).toBeInTheDocument()
    expect(screen.queryByText(/No clip selected/i)).toBeNull()

    // Retry refires the clips query.
    const gets = () =>
      fetchMock.mock.calls.filter(([u]) => String(u).includes('/videos/v1/clips')).length
    const before = gets()
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(gets()).toBeGreaterThan(before)
  })
})

// ── Issue 188 AC: Review no longer renders the moved panels ──────────────────
// Verifying that TranscriptEditor / CaptionStylePanel / CleanPassPanel are
// NOT rendered in the Review page is covered in Review.test.tsx.  The
// structural test here is that Editor.tsx owns those tools when requested.
describe('Editor — AC: honest framing', () => {
  it('never renders virality promise language', async () => {
    vi.stubGlobal('fetch', mockFetch())
    const { container } = renderEditor('/app/editor?video_id=v1&clip_id=c1')
    await screen.findByText(/does not promise virality/i)
    const text = container.textContent ?? ''
    expect(text).not.toMatch(/\bpromises virality\b|\bguarantees performance\b/)
  })
})
