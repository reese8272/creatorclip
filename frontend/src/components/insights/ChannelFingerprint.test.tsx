import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ChannelFingerprint } from './ChannelFingerprint'
import type { DnaStats, FingerprintShare } from '@/types'

const dna: DnaStats = {
  version: 3,
  status: 'confirmed',
  optimal_clip_len_s: 38,
  best_source_region: 'mid_roll',
  optimal_upload_gap_h: 52,
}

const shareResponse: FingerprintShare = {
  niches: ['Education', 'Gaming'],
  content_pillars: ['budgeting basics'],
  tone_tags: ['calm', 'plainspoken'],
  mission: 'Make personal finance approachable.',
  style_summary: 'Prefers punchy hooks.',
  honesty_line: 'AutoClip predicts fit with your style and audience — it does not promise virality.',
  generated_at: '2026-07-30T00:00:00Z',
}

function renderFingerprint() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <ChannelFingerprint dna={dna} />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('ChannelFingerprint', () => {
  it('renders the private analytics-derived stats without any fetch', () => {
    renderFingerprint()
    expect(screen.getByText('38s')).toBeInTheDocument()
    expect(screen.getByText('mid roll')).toBeInTheDocument()
    expect(screen.getByText('52.0h')).toBeInTheDocument()
  })

  it('does not fetch or show the shareable card until the explicit button is clicked', () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    renderFingerprint()
    expect(fetchMock).not.toHaveBeenCalled()
    expect(screen.queryByText(/Shareable card preview/i)).not.toBeInTheDocument()
  })

  it('builds the shareable card only after the explicit action, via POST', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toContain('/creators/me/fingerprint/share')
      expect(init?.method).toBe('POST')
      return { status: 200, ok: true, json: async () => shareResponse }
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    renderFingerprint()

    await user.click(screen.getByRole('button', { name: /Create shareable fingerprint/i }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(await screen.findByText(/Shareable card preview/i)).toBeInTheDocument()
    expect(screen.getByText('Education')).toBeInTheDocument()
    expect(screen.getByText(/does not promise virality/i)).toBeInTheDocument()
    // The private analytics stats must never appear inside the shareable preview text.
    expect(screen.queryByText(/mid_roll/i)).not.toBeInTheDocument()
  })
})
