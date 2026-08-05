import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Review } from './Review'
import type { PersonalizationStatus } from '@/types'

const BASE_CLIP = {
  id: 'c1', video_id: 'v1', setup_start_s: 2, start_s: 0, end_s: 20, peak_s: 10,
  score: 0.91, rank: 1, principle: 'Curiosity gap', reasoning: 'Strong hook in 3s.',
  render_status: 'done', render_uri: 'http://x/c1.mp4', cleaned_render_uri: null,
  applied_title: null, applied_description: null,
  origin: 'engine', aspect: '9:16', shortlisted: true,
}

// Row for the standalone picker landing (no-param /review).
const BASE_VIDEO = {
  id: 'v1', youtube_video_id: 'yt1', title: 'My stream VOD', kind: 'video',
  ingest_status: 'done', failure_reason: null, duration_s: 300,
  created_at: '2026-07-01T00:00:00Z', origin: 'upload', clippable: true,
}

function mockFetch(personalization?: PersonalizationStatus | null) {
  const json = (body: unknown) => ({ status: 200, ok: true, json: async () => body })
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/videos')) return json({ videos: [BASE_VIDEO], state: 'populated' })
    if (url.endsWith('/videos/clips/counts'))
      return json({ counts: [{ video_id: 'v1', total: 1, rendered: 1 }] })
    if (url.endsWith('/videos/v1/feedback')) return json({ items: [] })
    if (url.endsWith('/videos/v1/clips'))
      return json({
        clips: [BASE_CLIP],
        personalization: personalization ?? null,
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
  it('shows the standalone picker when no video_id is present, and a row click opens the clip view', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderReview('/app/review')
    expect(await screen.findByRole("heading", { name: "Review clips" })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Upload a video/i })).toBeInTheDocument()
    // The old dead end is gone — Review is a standalone tool now.
    expect(screen.queryByText(/No video selected/)).toBeNull()
    // Round trip: picking the row sets ?video_id= and the live page takes over.
    await userEvent.click(await screen.findByRole('button', { name: 'Review clips' }))
    expect(await screen.findByText(/Clip #1/)).toBeInTheDocument()
  })

  // ── Issue 370: video-level style review ──
  it('?mode=style opens the style review surface', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderReview('/app/review?video_id=v1&mode=style')
    expect(await screen.findByText('Your take on this style')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Works for me/ })).toBeInTheDocument()
    // Honesty band still present in style mode.
    expect(screen.getByText(/does not promise virality/i)).toBeInTheDocument()
  })

  it('a 0-clip video offers "Review the style instead" and the click lands on the style surface', async () => {
    const base = mockFetch()
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/videos/v1/clips'))
        return { status: 200, ok: true, json: async () => ({ clips: [], personalization: null }) }
      return base(input)
    })
    vi.stubGlobal('fetch', fetchMock)
    renderReview('/app/review?video_id=v1')
    expect(await screen.findByText(/No clips yet/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Review the style instead/ }))
    expect(await screen.findByText('Your take on this style')).toBeInTheDocument()
  })

  it('the clip view links to the overall style review', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderReview('/app/review?video_id=v1')
    await screen.findByText(/Clip #1/)
    expect(
      screen.getByRole('button', { name: /Review this video’s overall style/ }),
    ).toBeInTheDocument()
  })

  it('loads the clip: player meta, why-this-clip reasoning, honesty disclaimer, and Refine button', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderReview('/app/review?video_id=v1')
    expect(await screen.findByText(/Clip #1/)).toBeInTheDocument()
    expect(screen.getByText('Strong hook in 3s.')).toBeInTheDocument() // Why-this-clip is default-open
    expect(screen.getByText(/does not promise virality/i)).toBeInTheDocument()
    // Issue 188: Refine button opens Editor; transcript/caption/clean panels are NOT on Review
    expect(screen.getByRole('button', { name: /Refine/i })).toBeInTheDocument()
  })

  it('does NOT render transcript editor, caption style, or clean pass panels (Issue 188 — moved to Editor)', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderReview('/app/review?video_id=v1')
    await screen.findByText(/Clip #1/)
    // These panels were relocated to Editor.tsx. Assert the absence of the
    // interactive panel controls (not descriptive copy — Issue 306's "Open in
    // the editor" card legitimately mentions transcript/caption/filler in prose).
    expect(screen.queryByRole('button', { name: /caption style/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /clean filler/i })).toBeNull()
    expect(screen.queryByRole('textbox', { name: /transcript/i })).toBeNull()
  })

  it('shows a retry affordance — not "No clips yet" — when the clips query fails', async () => {
    // A transient 500 must not masquerade as first-run emptiness and tell the
    // creator to regenerate clips that already exist.
    const base = mockFetch()
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/videos/v1/clips'))
        return { status: 500, ok: false, json: async () => ({ detail: 'boom' }) }
      return base(input)
    })
    vi.stubGlobal('fetch', fetchMock)
    renderReview('/app/review?video_id=v1')
    expect(await screen.findByText(/Couldn.t load clips for this video/)).toBeInTheDocument()
    expect(screen.queryByText(/No clips yet/)).toBeNull()

    // Retry refires the clips query.
    const before = fetchMock.mock.calls.filter(([u]) =>
      String(u).endsWith('/videos/v1/clips'),
    ).length
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([u]) => String(u).endsWith('/videos/v1/clips')).length,
      ).toBeGreaterThan(before),
    )
  })

  it('opens the tag-feedback panel when Keep is clicked', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderReview('/app/review?video_id=v1')
    await screen.findByText(/Clip #1/)
    await userEvent.click(screen.getByRole('button', { name: 'Keep' }))
    expect(screen.getByText('Why are you keeping this?')).toBeInTheDocument()
  })

  // ── L26 Issue 424: the stage flip ──
  it('renders exactly ONE primary panel — the stage card (docs/UI.md hierarchy rule)', async () => {
    vi.stubGlobal('fetch', mockFetch())
    const { container } = renderReview('/app/review?video_id=v1')
    await screen.findByText(/Clip #1/)
    const primaries = container.querySelectorAll('[data-elevation="primary"]')
    expect(primaries).toHaveLength(1)
    // And it is the stage: it hosts the clip player.
    expect(primaries[0]!.querySelector('video')).not.toBeNull()
  })

  it('shows the metadata panel on the actions rail and keeps Next clip working from YourCall', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderReview('/app/review?video_id=v1')
    await screen.findByText(/Clip #1/)
    expect(screen.getByTestId('clip-metadata-panel')).toBeInTheDocument()
    // The quiet footer advance still carries the load-bearing accessible name.
    expect(screen.getByRole('button', { name: /Next clip/ })).toBeInTheDocument()
  })

  it('keeps the personalization card inside the case column (#412 empty-canvas guard)', async () => {
    const personalization: PersonalizationStatus = {
      active: false, labels: 5, threshold: 20, weight: 0,
    }
    vi.stubGlobal('fetch', mockFetch(personalization))
    renderReview('/app/review?video_id=v1')
    await screen.findByText(/Clip #1/)
    const caseColumn = screen.getByRole('region', { name: 'Why this clip' })
    expect(caseColumn.textContent).toContain('Learning your taste')
  })
})

// ── Issue 216: PersonalizationBand honest copy ────────────────────────────────

describe('PersonalizationBand', () => {
  it('shows "Still learning" copy below threshold (active=false)', async () => {
    const personalization: PersonalizationStatus = {
      active: false, labels: 5, threshold: 20, weight: 0,
    }
    vi.stubGlobal('fetch', mockFetch(personalization))
    renderReview('/app/review?video_id=v1')
    // Wait for clip data to load.
    expect(await screen.findByText(/Clip #1/)).toBeInTheDocument()
    const band = screen.getByText(/Still learning/i)
    expect(band).toBeInTheDocument()
    expect(screen.getByText(/5\/20/)).toBeInTheDocument()
    // The band copy itself must not promise virality. The existing DisclaimerBand
    // contains "does not promise virality" which is correct honesty language — we
    // scope the check to the band element, not the whole page.
    const bandText = band.textContent?.toLowerCase() ?? ''
    expect(bandText).not.toMatch(/\bviral\b|\bguarantee\b/)
  })

  it('shows "Personalized" copy at/above threshold (active=true)', async () => {
    const personalization: PersonalizationStatus = {
      active: true, labels: 25, threshold: 20, weight: 0.25,
    }
    vi.stubGlobal('fetch', mockFetch(personalization))
    renderReview('/app/review?video_id=v1')
    expect(await screen.findByText(/Clip #1/)).toBeInTheDocument()
    const band = screen.getByText(/Personalized to your feedback/i)
    expect(band).toBeInTheDocument()
    expect(screen.getByText(/25 ratings collected/i)).toBeInTheDocument()
    // The band copy itself must not promise virality.
    const bandText = band.textContent?.toLowerCase() ?? ''
    expect(bandText).not.toMatch(/\bviral\b|\bguarantee\b/)
  })

  it('renders no personalization band when the field is absent (null)', async () => {
    // When the API returns no personalization field, neither band should appear.
    vi.stubGlobal('fetch', mockFetch(null))
    renderReview('/app/review?video_id=v1')
    expect(await screen.findByText(/Clip #1/)).toBeInTheDocument()
    expect(screen.queryByText(/Still learning/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Personalized to your feedback/i)).not.toBeInTheDocument()
  })
})

// ── Issue 377: shortlist mode ──────────────────────────────────────────────

function makeClip(rank: number, shortlisted: boolean) {
  return {
    ...BASE_CLIP,
    id: `c${rank}`,
    rank,
    shortlisted,
    principle: `Principle ${rank}`,
    reasoning: `Reasoning for clip ${rank}.`,
  }
}

// 4 candidates, top 3 shortlisted (matches SHORTLIST_SIZE=3) — the shape the
// backend actually sends: every candidate scored, only the top N flagged.
const FOUR_CLIPS = [
  makeClip(1, true),
  makeClip(2, true),
  makeClip(3, true),
  makeClip(4, false),
]

function mockFetchWithClips(clips: unknown[]) {
  const json = (body: unknown) => ({ status: 200, ok: true, json: async () => body })
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/videos')) return json({ videos: [BASE_VIDEO], state: 'populated' })
    if (url.endsWith('/videos/clips/counts'))
      return json({ counts: [{ video_id: 'v1', total: clips.length, rendered: clips.length }] })
    if (url.endsWith('/videos/v1/feedback')) return json({ items: [] })
    if (url.endsWith('/videos/v1/clips')) return json({ clips, personalization: null })
    return json({})
  })
}

describe('Review — shortlist mode (Issue 377)', () => {
  it('defaults to the shortlisted clips and offers "show all candidates"', async () => {
    vi.stubGlobal('fetch', mockFetchWithClips(FOUR_CLIPS))
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter basename="/app" initialEntries={['/app/review?video_id=v1']}>
          <Routes>
            <Route path="review" element={<Review />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )
    expect(await screen.findByText(/Clip #1/)).toBeInTheDocument()
    expect(screen.getByText('Top 3 picks — the case for each, ranked')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Show all 4 candidates/ })).toBeInTheDocument()

    // Advance through the shortlist — the queue must be exactly the 3
    // shortlisted clips, never surfacing #4 by default.
    await userEvent.click(screen.getByRole('button', { name: /Next clip/ }))
    expect(await screen.findByText(/Clip #2/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Next clip/ }))
    expect(await screen.findByText(/Clip #3/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Next clip/ }))
    // Reviewing all 3 shortlisted clips ends the queue (redirect message),
    // never silently including the 4th, unshortlisted candidate.
    expect(await screen.findByText(/All clips reviewed/)).toBeInTheDocument()
  })

  it('"show all candidates" reveals the full set including non-shortlisted clips', async () => {
    vi.stubGlobal('fetch', mockFetchWithClips(FOUR_CLIPS))
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter basename="/app" initialEntries={['/app/review?video_id=v1']}>
          <Routes>
            <Route path="review" element={<Review />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )
    expect(await screen.findByText(/Clip #1/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Show all 4 candidates/ }))
    expect(await screen.findByText('Showing all 4 candidates')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /Show top picks only \(3\)/ }),
    ).toBeInTheDocument()
    // Toggling resets to the first clip of the now-active (full) queue.
    expect(screen.getByText(/Clip #1/)).toBeInTheDocument()
  })

  it('shows no shortlist banner when nothing is shortlisted (e.g. only a creator selection)', async () => {
    const creatorOnly = [
      { ...BASE_CLIP, id: 'c9', rank: null, score: null, origin: 'creator', shortlisted: false },
    ]
    vi.stubGlobal('fetch', mockFetchWithClips(creatorOnly))
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter basename="/app" initialEntries={['/app/review?video_id=v1']}>
          <Routes>
            <Route path="review" element={<Review />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )
    await waitFor(() => expect(screen.getAllByText(/Your selection/).length).toBeGreaterThan(0))
    expect(screen.queryByTestId('shortlist-banner')).not.toBeInTheDocument()
  })
})
