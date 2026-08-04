import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Editor } from './Editor'
import { stubRect } from '@/test/rect'
import { flushResizeObservers } from '@/test/setup'

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
  origin: 'engine',
  aspect: '9:16',
  shortlisted: true,
}

// Creator-made selection (Issue 373) — never engine-scored.
const CREATOR_CLIP = {
  ...BASE_CLIP,
  id: 'c9',
  rank: null,
  shortlisted: false,
  score: null,
  peak_s: null,
  setup_start_s: null,
  start_s: 30,
  end_s: 75,
  principle: '',
  reasoning: '',
  origin: 'creator',
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

// Full-source transcript (Issue 372) — segment-granular.
const VIDEO_TRANSCRIPT = {
  video_id: 'v1',
  duration_s: 300,
  source: 'deepgram',
  state: 'populated',
  segments: [
    { text: 'Intro hello world', start_s: 0, end_s: 4, index: 0 },
    { text: 'Deep dive begins here', start_s: 4, end_s: 9, index: 1 },
  ],
}

function mockFetch() {
  const json = (body: unknown) => ({ status: 200, ok: true, json: async () => body })
  return vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/videos')) return json({ videos: [BASE_VIDEO], state: 'populated' })
    if (url.endsWith('/videos/clips/counts'))
      return json({ counts: [{ video_id: 'v1', total: 1, rendered: 1 }] })
    if (url.endsWith('/videos/v1/transcript')) return json(VIDEO_TRANSCRIPT)
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
    // Issue 355 renamed the picker heading to "Editor" so it matches the nav —
    // which the loaded editor also uses, so assert the picker's own sub-line.
    expect(await screen.findByText(/Pick a processed video to work its clips/i)).toBeInTheDocument()
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
    const { container } = renderEditor('/app/editor?video_id=v1&clip_id=c1')
    await screen.findByText(/Clip #1/i)
    await userEvent.click(screen.getByRole('tab', { name: /Long-form source/i }))
    expect(screen.getByText('Suggested clips')).toBeInTheDocument()
    // Issue 372: the placeholder became a real source player streaming from the
    // authed source endpoint.
    const video = container.querySelector('video[src="/videos/v1/stream"]')
    expect(video).not.toBeNull()
  })

  // ── Issue 372: full-source player + searchable transcript ──
  it('long-form shows the source-expired card (no player) when the source is purged', async () => {
    const base = mockFetch()
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/videos'))
        return {
          status: 200, ok: true,
          json: async () => ({ videos: [{ ...BASE_VIDEO, clippable: false }], state: 'populated' }),
        }
      return base(input)
    })
    vi.stubGlobal('fetch', fetchMock)
    const { container } = renderEditor('/app/editor?video_id=v1')
    await screen.findByText('Suggested clips')
    expect(await screen.findByText(/Source media expired/)).toBeInTheDocument()
    expect(container.querySelector('video[src="/videos/v1/stream"]')).toBeNull()
  })

  it('long-form renders the searchable transcript; filter narrows segments; click seeks', async () => {
    vi.stubGlobal('fetch', mockFetch())
    const { container } = renderEditor('/app/editor?video_id=v1')
    expect(await screen.findByText('Intro hello world')).toBeInTheDocument()
    expect(screen.getByText('Deep dive begins here')).toBeInTheDocument()

    await userEvent.type(screen.getByRole('searchbox', { name: /Search the transcript/i }), 'deep')
    expect(screen.queryByText('Intro hello world')).toBeNull()
    expect(screen.getByText('Deep dive begins here')).toBeInTheDocument()

    const player = container.querySelector('video[src="/videos/v1/stream"]') as HTMLVideoElement
    await userEvent.click(screen.getByText('Deep dive begins here'))
    expect(player.currentTime).toBe(4)
  })

  it('long-form master timeline uses the real source duration, not furthest clip end', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderEditor('/app/editor?video_id=v1')
    await screen.findByText('Suggested clips')
    // The ruler is adaptive and derives its labels from the MEASURED width, so
    // the bar needs a box before it can emit any (Issue 390). 100px / 300s =
    // 0.33 px/s → a 300s tick interval → labels 0:00 and 5:00.
    const bar = screen.getByTestId('master-timeline-bar')
    stubRect(bar, 100, 96)
    // The layout effect already measured 0 (the stub lands after mount), so the
    // ruler needs the ResizeObserver to fire before it has a width to work with.
    await act(async () => {
      flushResizeObservers(100)
    })
    // BASE_VIDEO.duration_s = 300 → the right-hand tick reads 5:00 (clip ends at 20s).
    expect(await screen.findByText('5:00')).toBeInTheDocument()
  })

  // ── Issue 373: create-clip-from-selection + provenance + export ──
  it('dragging the master timeline proposes a clip and Create posts the range', async () => {
    const fetchMock = mockFetch()
    vi.stubGlobal('fetch', fetchMock)
    renderEditor('/app/editor?video_id=v1')
    await screen.findByText('Suggested clips')

    // jsdom rects are zero-sized — pin the bar geometry so x→time works.
    const bar = screen.getByTestId('master-timeline-bar')
    stubRect(bar, 100, 96)

    // Drag 10% → 40% of a 300s source = 30s → 120s. POINTER events, not mouse:
    // Issue 390 moved both timelines onto pointer input so touch and pen work,
    // and fireEvent.mouseDown does not trigger onPointerDown. Same coordinates,
    // same assertions — this asserts behaviour, not the input mechanism.
    fireEvent.pointerDown(bar, { clientX: 10, button: 0, pointerId: 1 })
    fireEvent.pointerMove(bar, { clientX: 40, pointerId: 1 })
    fireEvent.pointerUp(bar, { clientX: 40, pointerId: 1 })

    expect(await screen.findByText(/0:30 → 2:00/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Create clip' }))

    const createCall = fetchMock.mock.calls.find(
      ([u, init]) => String(u).endsWith('/videos/v1/clips') && init?.method === 'POST',
    )
    expect(createCall).toBeTruthy()
    const posted = JSON.parse(String(createCall![1]?.body))
    expect(posted.start_s).toBeCloseTo(30, 0)
    expect(posted.end_s).toBeCloseTo(120, 0)
  })

  it('transcript "Clip this" pre-fills the create card with the segment bounds', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderEditor('/app/editor?video_id=v1')
    await screen.findByText('Deep dive begins here')
    await userEvent.click(screen.getAllByRole('button', { name: 'Clip this' })[1])
    // Second segment: 4s → 9s.
    expect(await screen.findByText(/0:04 → 0:09/)).toBeInTheDocument()
  })

  it('creator clips render in "Your clips" with honest provenance, engine clips stay Suggested', async () => {
    const base = mockFetch()
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes('/videos/v1/clips'))
        return {
          status: 200, ok: true,
          json: async () => ({ clips: [BASE_CLIP, CREATOR_CLIP], personalization: null }),
        }
      return base(input)
    })
    vi.stubGlobal('fetch', fetchMock)
    renderEditor('/app/editor?video_id=v1')
    expect(await screen.findByText('Your clips')).toBeInTheDocument()
    expect(screen.getByText(/not engine-scored/)).toBeInTheDocument()
    expect(screen.getByText('Suggested clips')).toBeInTheDocument()
    // No fake fit tier on the creator clip row (appears in list + export rows).
    expect(screen.getAllByText('Your selection').length).toBeGreaterThan(0)
  })

  it('export panel lists rendered clips with real download links and the honest source-edit line', async () => {
    const base = mockFetch()
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes('/videos/v1/clips'))
        return {
          status: 200, ok: true,
          json: async () => ({ clips: [BASE_CLIP, CREATOR_CLIP], personalization: null }),
        }
      return base(input)
    })
    vi.stubGlobal('fetch', fetchMock)
    renderEditor('/app/editor?video_id=v1')
    await screen.findByText('Your clips')
    const links = screen.getAllByRole('link', { name: 'Download' })
    expect(links).toHaveLength(2)
    expect(links[0]).toHaveAttribute(
      'href',
      expect.stringContaining('/download?disposition=attachment'),
    )
    expect(screen.getByText(/Full source-edit export isn’t available/)).toBeInTheDocument()
    // No stub button anymore.
    expect(screen.queryByRole('button', { name: /Export source edit/ })).toBeNull()
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
// Verifying that the caption-style and clean-pass panels are
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
