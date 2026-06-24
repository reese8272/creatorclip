import { render, screen } from '@testing-library/react'
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
}

function mockFetch(personalization?: PersonalizationStatus | null) {
  const json = (body: unknown) => ({ status: 200, ok: true, json: async () => body })
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
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
  it('prompts to pick a video when no video_id is present', () => {
    vi.stubGlobal('fetch', mockFetch())
    renderReview('/app/review')
    expect(screen.getByText(/No video selected/)).toBeInTheDocument()
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

  it('opens the tag-feedback panel when Keep is clicked', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderReview('/app/review?video_id=v1')
    await screen.findByText(/Clip #1/)
    await userEvent.click(screen.getByRole('button', { name: '👍 Keep' }))
    expect(screen.getByText('Why are you keeping this?')).toBeInTheDocument()
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
