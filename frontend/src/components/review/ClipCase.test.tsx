/**
 * ClipCase (L26 Issue 424 — the argued half of the old WhyThisClip; tests
 * migrated in Issue 425 when that component was deleted).
 *
 * Covers the case surface: principle badge + fit tier lead, reasoning, the
 * score behind a closed disclosure with the estimate-not-guarantee wording
 * verbatim, the Issue 325 explanation card, and creator-clip honesty. The
 * metadata half (apply/regenerate) is covered in ClipMetadataPanel.test.tsx.
 */
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ClipCase } from './ClipCase'
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
  applied_title: null,
  applied_description: null,
  origin: 'engine' as const,
  aspect: '9:16',
  shortlisted: true,
}

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

afterEach(() => vi.unstubAllGlobals())

describe('ClipCase', () => {
  it('renders principle, score, and reasoning', () => {
    render(<ClipCase clip={CLIP} />, { wrapper })
    expect(screen.getByText(/Hook in the first 3 seconds/)).toBeInTheDocument()
    expect(screen.getByText('0.82')).toBeInTheDocument()
    expect(screen.getByText(CLIP.reasoning)).toBeInTheDocument()
  })

  it('shows no virality language in static copy', () => {
    const { container } = render(<ClipCase clip={CLIP} />, { wrapper })
    const text = container.textContent?.toLowerCase() ?? ''
    expect(text).not.toContain('will go viral')
    expect(text).not.toContain('guaranteed views')
    // The score label must use hedged language, verbatim.
    expect(text).toContain('fit estimate, not a guarantee')
  })

  it('renders the principle as a named badge, not [bracketed] monospace', () => {
    const { container } = render(<ClipCase clip={CLIP} />, { wrapper })
    expect(container.textContent).not.toContain('[principle]')
    expect(screen.getByTestId('principle-badge')).toHaveTextContent('Hook in the first 3 seconds')
  })

  it('keeps the numeric score reachable but not leading', () => {
    render(<ClipCase clip={CLIP} />, { wrapper })
    const details = screen.getByText('0.82').closest('details')
    expect(details, 'the score belongs behind a disclosure').toBeTruthy()
    expect(details!.open, 'the disclosure starts closed').toBe(false)
  })

  it('a creator selection gets provenance framing, not a principle + fit tier (Issue 373)', () => {
    render(
      <ClipCase clip={{ ...CLIP, origin: 'creator', score: null, rank: null, principle: '' }} />,
      { wrapper },
    )
    expect(screen.getByText('Your selection')).toBeInTheDocument()
    expect(screen.getByText(/not engine-scored/)).toBeInTheDocument()
    expect(screen.queryByTestId('principle-badge')).toBeNull()
  })

  it('explain-clip: shows card when trigger clicked (Issue 325)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          explanation:
            'This moment fits your channel because the hook lands in the first 3 seconds.',
          cited_principle: 'Hook in the first 3 seconds',
          disclaimer: 'This explanation is an estimate grounded in your channel data.',
        }),
      }),
    )

    const user = userEvent.setup()
    render(<ClipCase clip={CLIP} />, { wrapper })

    await user.click(screen.getByText(/Why this clip\? \(detailed explanation\)/i))

    expect(await screen.findByText('Why this clip')).toBeInTheDocument()
    expect(await screen.findByText(/hook lands in the first 3 seconds/i)).toBeInTheDocument()
    const card = await screen.findByTestId('explain-clip-card')
    expect(within(card).getByText('Hook in the first 3 seconds')).toBeInTheDocument()
    expect(
      within(card).getByText(/estimate grounded in your channel data/),
    ).toBeInTheDocument()
  })
})
