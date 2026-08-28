import { render, screen, waitFor, within } from '@testing-library/react'
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
  return vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
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

  // Issue 445 (owner decision 2026-08-10): the verdict commits on the FIRST
  // click; tags become optional post-hoc enrichment on the LastCallStrip.
  it('Keep commits on first click and offers post-hoc tags + Undo on the strip', async () => {
    const fetchMock = mockFetch()
    vi.stubGlobal('fetch', fetchMock)
    renderReview('/app/review?video_id=v1')
    await screen.findByText(/Clip #1/)
    await userEvent.click(screen.getByRole('button', { name: 'Keep' }))

    // The POST went out immediately — no Submit ritual.
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([u, init]) =>
            String(u).endsWith('/clips/c1/feedback') &&
            (init as RequestInit | undefined)?.method === 'POST',
        ),
      ).toBe(true),
    )
    // The strip carries the post-hoc enrichment and the Undo.
    const strip = await screen.findByTestId('last-call-strip')
    expect(within(strip).getByText('Kept')).toBeInTheDocument()
    expect(within(strip).getByText(/Add why\?/)).toBeInTheDocument()
    expect(within(strip).getByRole('button', { name: /Undo/ })).toBeInTheDocument()
  })

  it('Undo retracts via PUT triage → pending and returns to the clip (Issue 472 contract)', async () => {
    const fetchMock = mockFetch()
    vi.stubGlobal('fetch', fetchMock)
    renderReview('/app/review?video_id=v1')
    await screen.findByText(/Clip #1/)
    await userEvent.click(screen.getByRole('button', { name: 'Drop' }))

    const strip = await screen.findByTestId('last-call-strip')
    await userEvent.click(within(strip).getByRole('button', { name: /Undo/ }))

    // Retraction is EXCLUSIVELY the triage PUT — never a `skip` feedback POST.
    await waitFor(() => {
      const put = fetchMock.mock.calls.find(
        ([u, init]) =>
          String(u).endsWith('/clips/c1/triage') &&
          (init as RequestInit | undefined)?.method === 'PUT',
      )
      expect(put).toBeTruthy()
      expect(JSON.parse(String((put![1] as RequestInit).body))).toEqual({ triage: 'pending' })
    })
    // The queue rewound to the retracted clip.
    expect(await screen.findByText(/Clip #1/)).toBeInTheDocument()
    expect(screen.queryByTestId('last-call-strip')).toBeNull()
  })

  it('shows per-video progress as "N of M clips reviewed" (Issue 445 AC5)', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderReview('/app/review?video_id=v1')
    await screen.findByText(/Clip #1/)
    expect(screen.getByTestId('review-progress')).toHaveTextContent('0 of 1 clips reviewed')
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

  it('scales the trim filmstrip to the setup-origin duration, not end_s - start_s', async () => {
    // Setup clips have setup_start_s < start_s. The backend validates and cuts
    // trims against end_s - (setup_start_s ?? start_s), so the filmstrip's
    // timebase must match or every drag submits compressed seconds. (Migrated
    // from ClipPlayer.test when the filmstrip moved to the stage's below slot.)
    vi.stubGlobal('fetch', mockFetch())
    renderReview('/app/review?video_id=v1')
    await screen.findByText(/Clip #1/)
    // BASE_CLIP: end 20, setup_start 2 → 18s, never 20s.
    expect(screen.getByRole('slider', { name: 'Trim start' })).toHaveAttribute(
      'aria-valuemax',
      '18',
    )
    expect(screen.getByRole('slider', { name: 'Trim end' })).toHaveAttribute('aria-valuemax', '18')
    // Both the meta row and the filmstrip readout agree on the timebase.
    expect(screen.getAllByText(/18\.0s/).length).toBeGreaterThanOrEqual(2)
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

  it('shows "Personalized" copy past the threshold (active=true)', async () => {
    const personalization: PersonalizationStatus = {
      active: true, labels: 25, threshold: 20, weight: 0.25,
    }
    vi.stubGlobal('fetch', mockFetch(personalization))
    renderReview('/app/review?video_id=v1')
    expect(await screen.findByText(/Clip #1/)).toBeInTheDocument()
    const band = screen.getByText(/Personalized to your feedback/i)
    expect(band).toBeInTheDocument()
    // "clips rated" — the post-dedup trained count, not raw rating events (474).
    expect(screen.getByText(/25 clips rated/i)).toBeInTheDocument()
    // The band copy itself must not promise virality.
    const bandText = band.textContent?.toLowerCase() ?? ''
    expect(bandText).not.toMatch(/\bviral\b|\bguarantee\b/)
  })

  it('drops the N/threshold fraction when inactive PAST the threshold (#520)', async () => {
    // Issue 520 — `active` no longer follows from `labels >= threshold`: a creator
    // can be past it while their model still scores every clip the same, so we
    // serve DNA order and say so. Without the guard this rendered "45/20 clips
    // rated", a progress bar counting past its own target.
    const personalization: PersonalizationStatus = {
      active: false, labels: 45, threshold: 20, weight: 0,
    }
    vi.stubGlobal('fetch', mockFetch(personalization))
    renderReview('/app/review?video_id=v1')
    expect(await screen.findByText(/Clip #1/)).toBeInTheDocument()
    const band = screen.getByText(/Still learning/i)
    expect(band).toBeInTheDocument()
    expect(band.textContent).toContain('45 clips rated')
    expect(band.textContent).not.toContain('45/20')
    // The honest count is still shown — the creator did the work either way.
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
  return vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/videos')) return json({ videos: [BASE_VIDEO], state: 'populated' })
    if (url.endsWith('/videos/clips/counts'))
      return json({ counts: [{ video_id: 'v1', total: clips.length, rendered: clips.length }] })
    if (url.endsWith('/videos/v1/feedback')) return json({ items: [] })
    if (url.endsWith('/videos/v1/clips')) return json({ clips, personalization: null })
    return json({})
  })
}

describe('Review — piles + shortlist ordering (Issue 445, reversing 377)', () => {
  // Issue 377 used `shortlisted` as a FILTER: the queue was the top 3 and the
  // rest sat behind "show all candidates". The two tests that stood here pinned
  // that, including the assertion that finishing 3 of 4 shows "All clips
  // reviewed".
  //
  // The creator hit exactly that on 2026-08-12 — kept clip 3 of 12, was told
  // they were done and offered more clips, while the dashboard showed 27 to
  // review. The shortlist now ORDERS the pending queue instead of truncating
  // it. Ruling in docs/DECISIONS.md 2026-08-12.
  function renderReview(entry = '/app/review?video_id=v1') {
    return render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter basename="/app" initialEntries={[entry]}>
          <Routes>
            <Route path="review" element={<Review />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )
  }

  it('the pending queue holds EVERY untriaged clip, top picks first', async () => {
    vi.stubGlobal('fetch', mockFetchWithClips(FOUR_CLIPS))
    renderReview()
    expect(await screen.findByText(/Clip #1/)).toBeInTheDocument()

    // The unshortlisted 4th candidate is reachable by simply continuing —
    // it is no longer hidden behind a toggle, and reaching it does not
    // require having discovered a link.
    await userEvent.click(screen.getByRole('button', { name: /Next clip/ }))
    expect(await screen.findByText(/Clip #2/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Next clip/ }))
    expect(await screen.findByText(/Clip #3/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Next clip/ }))
    // THE REVERSAL: this used to assert "All clips reviewed". Four clips are
    // pending, so three verdicts cannot possibly mean done.
    expect(await screen.findByText(/Clip #4/)).toBeInTheDocument()
    expect(screen.queryByText(/All clips reviewed/)).not.toBeInTheDocument()
  })

  it('"all reviewed" means the pending pile is empty, not the shortlist', async () => {
    const allTriaged = FOUR_CLIPS.map((c, i) => ({
      ...c,
      triage: i % 2 === 0 ? 'kept' : 'dropped',
    }))
    vi.stubGlobal('fetch', mockFetchWithClips(allTriaged))
    renderReview()
    expect(await screen.findByText(/All clips reviewed/)).toBeInTheDocument()
  })

  it('pile tabs count each pile and switch to the kept list', async () => {
    const mixed = [
      { ...makeClip(1, true), triage: 'pending' },
      { ...makeClip(2, true), triage: 'kept', suggested_title: 'A kept clip title' },
      { ...makeClip(3, true), triage: 'dropped' },
      { ...makeClip(4, false), triage: 'pending' },
    ]
    vi.stubGlobal('fetch', mockFetchWithClips(mixed))
    renderReview()

    const tabs = await screen.findByTestId('pile-tabs')
    expect(within(tabs).getByRole('tab', { name: /Needs review 2/ })).toBeInTheDocument()
    expect(within(tabs).getByRole('tab', { name: /Keep 1/ })).toBeInTheDocument()
    expect(within(tabs).getByRole('tab', { name: /Drop 1/ })).toBeInTheDocument()

    await userEvent.click(within(tabs).getByRole('tab', { name: /Keep 1/ }))
    // The kept pile is a LIST showing the title in full — that is the question
    // being answered there ("which of these am I publishing?").
    expect(await screen.findByText('A kept clip title')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Un-keep' })).toBeInTheDocument()
  })

  it('opening the Keep pile does not claim the queue is finished', async () => {
    // The kept/dropped views render BEFORE the `reviewed` guard on purpose.
    const mixed = [
      { ...makeClip(1, true), triage: 'kept' },
      { ...makeClip(2, true), triage: 'kept' },
    ]
    vi.stubGlobal('fetch', mockFetchWithClips(mixed))
    renderReview('/app/review?video_id=v1&pile=kept')
    expect(await screen.findByTestId('pile-tabs')).toBeInTheDocument()
    expect(screen.queryByText(/All clips reviewed/)).not.toBeInTheDocument()
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
