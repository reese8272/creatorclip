// Insights.test.tsx — Issue 212: narrative rebuild coverage
//
// Tests cover:
//   1. Page-level framing ("what this is showing") appears above fold
//   2. Per-row static "why" is visible without clicking Analyze
//   3. Performer rows deep-link to the video timeline
//   4. Week-over-week "What changed" panel renders with analytics data
//   5. Honesty disclaimer always present; no virality promises
//   6. Existing tests: loading state, analyze-and-save, upload-intel error

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Insights } from './Insights'

const BASE_INSIGHTS = {
  totals: { videos_analyzed: 9, shorts: 4, longs: 5, ingested_done: 9, total_minutes_processed: 120 },
  dna: { version: 2, status: 'confirmed', optimal_clip_len_s: 30, best_source_region: 'mid', optimal_upload_gap_h: 6 },
  top_performers: [
    {
      video_id: 'v1',
      youtube_video_id: 'yt1',
      title: 'My Top Vid',
      kind: 'long',
      performance_score: 88,
      performance_score_components: { retention: 75, engagement: 80, views: 70 },
    },
  ],
  bottom_performers: [
    {
      video_id: 'v2',
      youtube_video_id: 'yt2',
      title: 'My Bottom Vid',
      kind: 'long',
      performance_score: 22,
      performance_score_components: { retention: 30, engagement: 25, views: 40 },
    },
  ],
}

const BASE_ANALYTICS_7D = {
  period: '7d',
  videos_in_period: 2,
  total_views: 1500,
  total_watch_time_h: 50,
  avg_view_duration_s: 180,
  avg_engagement_rate: 0.05,
  metrics_available: true,
}

const BASE_ANALYTICS_28D = {
  period: '28d',
  videos_in_period: 8,
  total_views: 4000,
  total_watch_time_h: 120,
  avg_view_duration_s: 160,
  avg_engagement_rate: 0.04,
  metrics_available: true,
}

function mockFetch(overrides?: Partial<{ noAnalytics: boolean; uploadError: boolean }>) {
  const json = (body: unknown) => ({ status: 200, ok: true, json: async () => body })
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/auth/me')) return json({ channel_title: 'X' })
    if (url.endsWith('/billing/balance')) return json({ minutes_balance: 100, low_balance: false })
    if (url.endsWith('/upload-intel')) {
      if (overrides?.uploadError) {
        return { status: 500, ok: false, json: async () => ({ detail: 'boom' }) }
      }
      return json({ data_available: false, best_windows: [], optimal_gap_hours: null })
    }
    if (url.endsWith('/insights/saved')) return json({ insights: [] })
    if (url.endsWith('/insights/analyze-performer') && init?.method === 'POST')
      return json({ id: 'ins1', content: 'Strong hook in the first 3 seconds.' })
    if (url.includes('/insights/analytics?period=7d')) {
      if (overrides?.noAnalytics) return json({ ...BASE_ANALYTICS_7D, metrics_available: false })
      return json(BASE_ANALYTICS_7D)
    }
    if (url.includes('/insights/analytics?period=28d')) {
      return json(BASE_ANALYTICS_28D)
    }
    if (url.endsWith('/creators/me/insights')) return json(BASE_INSIGHTS)
    return json({})
  })
}

function renderInsights() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter basename="/app" initialEntries={['/app/insights']}>
        <Routes>
          <Route path="insights" element={<Insights />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('Insights — narrative rebuild (Issue 212)', () => {
  it('renders the page-level framing that explains what each section shows', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderInsights()
    // The panel title uses an em-dash; find by partial heading text
    expect(
      await screen.findByRole('heading', { name: /what this is showing/i }),
    ).toBeInTheDocument()
    // "not against a generic virality benchmark" is split across spans — use container text
    const main = await screen.findByRole('main')
    expect(main.textContent).toMatch(/generic virality benchmark/i)
  })

  it('shows a static per-row "why" without clicking Analyze', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderInsights()
    await screen.findByText('My Top Vid')
    // "why" copy appears immediately (no button click needed)
    const whyEls = screen.getAllByTestId('performer-why')
    expect(whyEls.length).toBeGreaterThan(0)
    // Top performer: retention=75, engagement=80, views=70 — all above 65 threshold
    expect(whyEls[0]).toHaveTextContent(/strong watch-through|above-average/i)
  })

  it('renders the underperformer "why" narrative for the bottom performer', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderInsights()
    await screen.findByText('My Bottom Vid')
    // Bottom performer: retention=30, engagement=25, views=40 — retention+engagement below 35
    const whyEls = screen.getAllByTestId('performer-why')
    // At least the bottom performer's why should mention lower signals
    const texts = whyEls.map((el) => el.textContent || '')
    expect(texts.some((t) => /lower watch-through|below-average/i.test(t))).toBe(true)
  })

  it('each performer row links to the video timeline page', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderInsights()
    await screen.findByText('My Top Vid')
    const link = screen.getByRole('link', { name: /view timeline for My Top Vid/i })
    expect(link).toHaveAttribute('href', '/app/video/v1')
  })

  it('renders the "what changed this week" panel with metric rows', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderInsights()
    // findAllByText because the panel title "What changed this week" appears in
    // multiple contexts (InsightsFraming cross-reference + the panel itself)
    const headers = await screen.findAllByText(/what changed this week/i)
    expect(headers.length).toBeGreaterThan(0)
    // The diff table column header
    expect(await screen.findByText('Last 7 days')).toBeInTheDocument()
    // Metric row labels
    expect(await screen.findByText('Views')).toBeInTheDocument()
    expect(await screen.findByText('Watch time')).toBeInTheDocument()
  })

  it('shows an honest empty state for "what changed" when no analytics data', async () => {
    vi.stubGlobal('fetch', mockFetch({ noAnalytics: true }))
    renderInsights()
    // Panel heading appears at least once (in the WhatChanged panel)
    const headers = await screen.findAllByText(/what changed this week/i)
    expect(headers.length).toBeGreaterThan(0)
    expect(
      await screen.findByText(/not enough analytics data yet/i),
    ).toBeInTheDocument()
  })

  it('renders the channel snapshot, top performer, and honesty disclaimer', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderInsights()
    expect(await screen.findByText('My Top Vid')).toBeInTheDocument()
    // "Channel snapshot" appears in the framing text and the panel heading
    const snapshots = screen.getAllByText('Channel snapshot')
    expect(snapshots.length).toBeGreaterThan(0)
    expect(screen.getByText(/does not promise virality/i)).toBeInTheDocument()
  })

  it('honesty disclaimer copy is never virality-promising', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderInsights()
    await screen.findByText(/does not promise virality/i)
    // The framing also carries an honesty note
    expect(
      await screen.findByText(/do not predict future performance or promise any outcome/i),
    ).toBeInTheDocument()
  })

  it('analyzes a performer on demand and offers to save the result', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderInsights()
    await screen.findByText('My Top Vid')
    // Both top and bottom performers have Analyze buttons — click the first (top performer)
    const analyzeBtns = screen.getAllByRole('button', { name: 'Analyze' })
    expect(analyzeBtns.length).toBeGreaterThan(0)
    await userEvent.click(analyzeBtns[0])
    expect(await screen.findByText(/Strong hook in the first 3 seconds/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '★ Save' })).toBeInTheDocument()
  })

  // Issue 157: loading state instead of misleading empty/"build DNA" copy mid-fetch.
  it('shows a loading state while insights are in flight', () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise(() => {})),
    )
    renderInsights()
    expect(screen.getByText(/loading your channel insights/i)).toBeInTheDocument()
  })

  // Issue 157: failed sub-fetch surfaces distinctly from genuine empty state.
  it('surfaces an upload-intel fetch error distinctly from "no data yet"', async () => {
    vi.stubGlobal('fetch', mockFetch({ uploadError: true }))
    renderInsights()
    expect(await screen.findByText('Could not load timing data.')).toBeInTheDocument()
  })
})
