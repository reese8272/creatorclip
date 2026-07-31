import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { OriginalityGuard } from './OriginalityGuard'
import type { OriginalityAdvisory } from '@/types'

const base: OriginalityAdvisory = {
  checked: true,
  flagged: false,
  window: 8,
  cluster_size: 0,
  similar_clip_ids: [],
  common_structural_features: [],
  message: null,
  policy_url: 'https://support.google.com/youtube/answer/1311392',
  policy_checked: '2026-07-30',
}

describe('OriginalityGuard', () => {
  it('distinguishes a failed fetch from every other state', () => {
    render(<OriginalityGuard advisory={undefined} isError />)
    expect(screen.getByText(/Could not load/i)).toBeInTheDocument()
  })

  it('shows a loading state while the advisory is pending', () => {
    render(<OriginalityGuard advisory={undefined} />)
    expect(screen.getByText(/Checking your recent clips/i)).toBeInTheDocument()
  })

  it('reports "could not check yet" distinctly from "checked, nothing found"', () => {
    render(<OriginalityGuard advisory={{ ...base, checked: false }} />)
    expect(screen.getByText(/Not enough recent clips to check yet/i)).toBeInTheDocument()
  })

  it('shows a clean all-clear when checked and nothing is flagged', () => {
    render(<OriginalityGuard advisory={base} />)
    expect(screen.getByText(/Nothing flagged/i)).toBeInTheDocument()
    expect(screen.getByText('8')).toBeInTheDocument()
  })

  it('renders the flagged advisory with counts, the dated link, and the disclaimer', () => {
    render(
      <OriginalityGuard
        advisory={{
          ...base,
          flagged: true,
          cluster_size: 4,
          similar_clip_ids: ['a', 'b', 'c', 'd'],
          common_structural_features: ['length', 'pacing (setup lead)'],
        }}
      />,
    )
    expect(screen.getByText('4')).toBeInTheDocument()
    expect(screen.getByText(/near-identical in spoken content/i)).toBeInTheDocument()
    expect(screen.getByText(/length, pacing \(setup lead\)/i)).toBeInTheDocument()
    expect(screen.getByText(/may affect monetization eligibility/i)).toBeInTheDocument()
    expect(screen.getByText(/checked 2026-07-30/i)).toBeInTheDocument()
    expect(screen.getByText(/not a certification/i)).toBeInTheDocument()
    const link = screen.getByRole('link', { name: /see YouTube's policy/i })
    expect(link).toHaveAttribute('href', base.policy_url)
  })

  it('never states a strike count, a suspension timeline, or "new policy" framing', () => {
    const { container } = render(
      <OriginalityGuard
        advisory={{
          ...base,
          flagged: true,
          cluster_size: 4,
          similar_clip_ids: ['a', 'b', 'c', 'd'],
        }}
      />,
    )
    const text = container.textContent?.toLowerCase() ?? ''
    expect(text).not.toMatch(/strike|ladder|90.day|permanent(ly)? remov|new policy/)
  })

  it('never promises virality or guarantees an outcome', () => {
    const { container } = render(
      <OriginalityGuard
        advisory={{ ...base, flagged: true, cluster_size: 4, similar_clip_ids: ['a', 'b', 'c', 'd'] }}
      />,
    )
    expect(container.textContent?.toLowerCase()).not.toMatch(/viral|guarantee/)
  })
})
