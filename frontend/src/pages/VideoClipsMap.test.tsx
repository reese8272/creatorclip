/**
 * Tests for Issue 213 — VideoClipsMap page.
 * Tests for Issue 217 — "why not clipped" skip_reason surface.
 *
 * Covers:
 *  - One marker per clip rendered
 *  - Peak flag present (aria-label "peak moment")
 *  - Marker click shows principle + FitBadge
 *  - No virality language in the rendered output
 *  - Per-origin empty-states (upload / link / catalog)
 *  - "Review in order" link href
 *  - Deep-link href per marker (review?video_id=…&clip_id=…)
 *  - skip_reason_label shown for zero-clip upload videos (Issue 217)
 *  - No virality language in skip_reason_label surface
 */
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi, afterEach } from 'vitest'
import { VideoClipsMap } from './VideoClipsMap'
import type { Video, ReviewClip } from '@/types'

// ── Fixture helpers ───────────────────────────────────────────────────────────

function makeVideo(over: Partial<Video> = {}): Video {
  return {
    id: 'v1',
    youtube_video_id: 'abc123',
    title: 'Test video',
    kind: 'long',
    ingest_status: 'done',
    duration_s: 300,
    created_at: '2026-06-01T00:00:00Z',
    origin: 'upload',
    clippable: true,
    ...over,
  }
}

function makeClip(over: Partial<ReviewClip> = {}): ReviewClip {
  return {
    id: 'c1',
    video_id: 'v1',
    setup_start_s: 10,
    start_s: 12,
    end_s: 75,
    peak_s: 45,
    score: 0.82,
    rank: 1,
    principle: 'HOOK_SETUP',
    reasoning: 'Strong narrative hook with clear audience setup.',
    render_status: 'pending',
    render_uri: null,
    cleaned_render_uri: null,
    ...over,
  }
}

function mockFetch(
  video: Video | null,
  clips: ReviewClip[],
  skipReasonLabel: string | null = null,
) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const json = (body: unknown) => ({ status: 200, ok: true, json: async () => body })
    if (url.endsWith('/auth/me'))
      return json({ channel_title: 'Test', analysis_mode: 'manual', setup: { step: 'complete' } })
    if (url.endsWith('/billing/balance'))
      return json({ minutes_balance: 100, low_balance: false })
    if (url.endsWith('/videos'))
      return json({ videos: video ? [video] : [], state: 'populated' })
    if (url.match(/\/videos\/[^/]+\/clips$/))
      return json({
        clips,
        state: clips.length ? 'populated' : 'empty_initial',
        skip_reason: skipReasonLabel ? 'no_signal_above_threshold' : null,
        skip_reason_label: skipReasonLabel,
      })
    return json({})
  })
}

function renderMap(videoId = 'v1') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter basename="/app" initialEntries={[`/app/video/${videoId}`]}>
        <Routes>
          <Route path="video/:videoId" element={<VideoClipsMap />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('VideoClipsMap', () => {
  it('renders one marker per clip', async () => {
    const clips = [
      makeClip({ id: 'c1', setup_start_s: 10 }),
      makeClip({ id: 'c2', setup_start_s: 120 }),
    ]
    vi.stubGlobal('fetch', mockFetch(makeVideo(), clips))
    renderMap()
    await waitFor(() => {
      const markers = screen.getAllByRole('button', { name: /Clip at/i })
      expect(markers).toHaveLength(2)
    })
  })

  it('each marker has a peak flag with aria-label "peak moment"', async () => {
    vi.stubGlobal('fetch', mockFetch(makeVideo(), [makeClip()]))
    renderMap()
    await waitFor(() => {
      expect(screen.getByLabelText('peak moment')).toBeInTheDocument()
    })
  })

  it('clicking a marker reveals the principle and FitBadge', async () => {
    vi.stubGlobal('fetch', mockFetch(makeVideo(), [makeClip({ principle: 'HOOK_SETUP' })]))
    renderMap()
    const marker = await screen.findByRole('button', { name: /Clip at/i })
    fireEvent.click(marker)
    // Scope to the opened detail panel — the "Fit:" legend also shows a
    // "Strong channel fit" badge, so an unscoped query matches multiple.
    const panel = await screen.findByRole('region', { name: /Clip detail/i })
    expect(within(panel).getByText(/HOOK_SETUP/i)).toBeInTheDocument()
    expect(within(panel).getByText(/Strong channel fit/i)).toBeInTheDocument()
  })

  it('contains no virality language', async () => {
    vi.stubGlobal('fetch', mockFetch(makeVideo(), [makeClip()]))
    const { baseElement } = renderMap()
    await screen.findByRole('button', { name: /Clip at/i })
    const text = baseElement.textContent ?? ''
    expect(text).not.toMatch(/viral/i)
    expect(text).not.toMatch(/virality/i)
    expect(text).not.toMatch(/guarantee/i)
    expect(text).not.toMatch(/promise/i)
  })

  it('shows upload empty-state when origin=upload and no clips and no skip_reason_label', async () => {
    vi.stubGlobal('fetch', mockFetch(makeVideo({ origin: 'upload' }), [], null))
    renderMap()
    expect(await screen.findByText(/Generate clips to see your timeline/i)).toBeInTheDocument()
  })

  it('shows link empty-state when origin=link and no clips', async () => {
    vi.stubGlobal('fetch', mockFetch(makeVideo({ origin: 'link' }), []))
    renderMap()
    expect(await screen.findByText(/Upload source file to clip/i)).toBeInTheDocument()
  })

  it('shows catalog empty-state when origin=catalog and no clips', async () => {
    vi.stubGlobal('fetch', mockFetch(makeVideo({ origin: 'catalog' }), []))
    renderMap()
    expect(
      await screen.findByText(/catalog reference — not clippable/i),
    ).toBeInTheDocument()
  })

  it('renders "Review all clips in order" link pointing to /review?video_id=…', async () => {
    vi.stubGlobal('fetch', mockFetch(makeVideo({ id: 'v1' }), [makeClip()]))
    renderMap('v1')
    const link = await screen.findByRole('link', { name: /Review all clips in order/i })
    expect(link).toHaveAttribute('href', '/app/review?video_id=v1')
  })

  it('clicking a marker exposes a deep-link to that clip in Review', async () => {
    vi.stubGlobal('fetch', mockFetch(makeVideo({ id: 'v1' }), [makeClip({ id: 'c1' })]))
    renderMap('v1')
    const marker = await screen.findByRole('button', { name: /Clip at/i })
    fireEvent.click(marker)
    // The link's accessible name is its aria-label ("Review this clip in order"),
    // which overrides the visible "Review →" text for role-name queries.
    const link = await screen.findByRole('link', { name: /Review this clip in order/i })
    expect(link).toHaveAttribute('href', '/app/review?video_id=v1&clip_id=c1')
  })

  // ── Issue 217: skip_reason_label surface ──────────────────────────────────

  it('shows skip_reason_label when origin=upload, no clips, and label is provided', async () => {
    const label =
      'No engagement signal reached the detection threshold ' +
      '(Principle #6: Retention curve is ground truth — ' +
      'this video lacks the data density needed to locate a setup).'
    vi.stubGlobal('fetch', mockFetch(makeVideo({ origin: 'upload' }), [], label))
    renderMap()
    expect(await screen.findByText(/No clips were generated/i)).toBeInTheDocument()
    expect(await screen.findByText(/No engagement signal reached the detection threshold/i)).toBeInTheDocument()
  })

  it('skip_reason surface shows the honesty disclaimer (not a guarantee)', async () => {
    const label = 'No engagement signal reached the detection threshold (Principle #6).'
    vi.stubGlobal('fetch', mockFetch(makeVideo({ origin: 'upload' }), [], label))
    const { baseElement } = renderMap()
    await screen.findByText(/No clips were generated/i)
    expect(baseElement.textContent).toMatch(/grounded in your own data/i)
  })

  it('skip_reason_label surface contains no virality language (Issue 217 honesty constraint)', async () => {
    // Honesty constraint: never promise virality. "not a guarantee" is correct
    // honest framing (not a virality promise); only prohibit positive virality claims.
    const label = 'No engagement signal reached the detection threshold (Principle #6).'
    vi.stubGlobal('fetch', mockFetch(makeVideo({ origin: 'upload' }), [], label))
    const { baseElement } = renderMap()
    await screen.findByText(/No clips were generated/i)
    const text = baseElement.textContent ?? ''
    // Forbidden: positive virality promises
    expect(text).not.toMatch(/\bviral\b/i)
    expect(text).not.toMatch(/\bvirality\b/i)
    expect(text).not.toMatch(/\bpromises virality\b/i)
    expect(text).not.toMatch(/\bguaranteed to go viral\b/i)
  })

  it('does not show "No clips were generated" heading when clips exist', async () => {
    vi.stubGlobal('fetch', mockFetch(makeVideo(), [makeClip()]))
    renderMap()
    await screen.findByRole('button', { name: /Clip at/i })
    expect(screen.queryByText(/No clips were generated/i)).toBeNull()
  })
})
