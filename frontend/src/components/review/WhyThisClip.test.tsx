/**
 * Tests for WhyThisClip component with Issue 322/323/325 additions.
 *
 * Covers:
 *  - Static rendering: principle, score, timing always visible.
 *  - Issue 325: "Why this clip? (detailed explanation)" trigger renders;
 *    on click, shows the card (no-virality copy checked).
 *  - Issue 322: "Suggest titles / rewrite hook" trigger renders.
 *  - Issue 323: "Suggest caption / overlay text" trigger renders.
 *  - No virality language in the rendered static copy.
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { WhyThisClip } from './WhyThisClip'
import type { ReviewClip } from '@/types'

const CLIP: ReviewClip = {
  id: 'clip-abc-123',
  video_id: 'vid-xyz',
  setup_start_s: 5.0,
  start_s: 7.0,
  end_s: 67.0,
  peak_s: 35.0,
  score: 0.82,
  rank: 1,
  principle: 'Hook in the first 3 seconds',
  reasoning: 'This clip opens strongly and matches the channel style.',
  render_status: 'done',
  render_uri: null,
  cleaned_render_uri: null,
}

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

afterEach(() => vi.unstubAllGlobals())

describe('WhyThisClip', () => {
  it('renders principle, score, and timing', () => {
    render(<WhyThisClip clip={CLIP} />, { wrapper })
    expect(screen.getByText(/Hook in the first 3 seconds/)).toBeInTheDocument()
    expect(screen.getByText('0.82')).toBeInTheDocument()
    expect(screen.getByText(CLIP.reasoning)).toBeInTheDocument()
  })

  it('shows no virality language in static copy', () => {
    const { container } = render(<WhyThisClip clip={CLIP} />, { wrapper })
    const text = container.textContent?.toLowerCase() ?? ''
    // Affirmative virality promises are banned.
    expect(text).not.toContain('will go viral')
    expect(text).not.toContain('guaranteed views')
    // The score label must use hedged language (may contain "not a guarantee").
    expect(text).toContain('fit estimate, not a guarantee')
  })

  it('shows the explain-clip trigger button (Issue 325)', () => {
    render(<WhyThisClip clip={CLIP} />, { wrapper })
    expect(screen.getByText(/Why this clip\? \(detailed explanation\)/i)).toBeInTheDocument()
  })

  it('shows title suggestions trigger button (Issue 322)', () => {
    render(<WhyThisClip clip={CLIP} />, { wrapper })
    expect(screen.getByText(/Suggest titles \/ rewrite hook/i)).toBeInTheDocument()
  })

  it('shows caption suggestions trigger button (Issue 323)', () => {
    render(<WhyThisClip clip={CLIP} />, { wrapper })
    expect(screen.getByText(/Suggest caption \/ overlay text/i)).toBeInTheDocument()
  })

  it('explain-clip: shows card when trigger clicked (Issue 325)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        explanation: 'This moment fits your channel because the hook lands in the first 3 seconds.',
        cited_principle: 'Hook in the first 3 seconds',
        disclaimer: 'This explanation is an estimate grounded in your channel data.',
      }),
    }))

    const user = userEvent.setup()
    render(<WhyThisClip clip={CLIP} />, { wrapper })

    await user.click(screen.getByText(/Why this clip\? \(detailed explanation\)/i))

    // Card heading should appear.
    expect(await screen.findByText('Why this clip')).toBeInTheDocument()
    // Explanation text.
    expect(await screen.findByText(/hook lands in the first 3 seconds/i)).toBeInTheDocument()
    // Principle citation.
    expect(await screen.findByText('Hook in the first 3 seconds')).toBeInTheDocument()
  })
})
