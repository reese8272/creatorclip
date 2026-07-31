import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ProofOfLift } from './ProofOfLift'
import type { ProofOfLift as ProofOfLiftData } from '@/types'

const base: ProofOfLiftData = {
  published_count: 0,
  judged_count: 0,
  beat_median_count: 0,
  below_median_count: 0,
  deferred_count: 0,
  rate: null,
  rate_ci_low: null,
  rate_ci_high: null,
  min_judged_for_rate: 10,
  contrast: null,
  metric: 'Views at the checkpoint versus your own median.',
  checkpoint: '48h and 7d after publish',
}

describe('ProofOfLift', () => {
  it('shows a useful empty state — the default at beta launch', () => {
    render(<ProofOfLift lift={base} />)
    expect(screen.getByText(/Nothing published through AutoClip yet/i)).toBeInTheDocument()
  })

  it('distinguishes a failed fetch from a genuine empty state', () => {
    render(<ProofOfLift lift={undefined} isError />)
    expect(screen.getByText(/Could not load/i)).toBeInTheDocument()
  })

  it('reports deferred clips as unjudged, never as failures', () => {
    render(
      <ProofOfLift
        lift={{ ...base, published_count: 4, judged_count: 1, beat_median_count: 1, deferred_count: 3 }}
      />,
    )
    expect(screen.getByText(/still\s+unjudged/i)).toBeInTheDocument()
    expect(screen.getByText(/Not a\s+result either way/i)).toBeInTheDocument()
  })

  it('withholds a rate below the minimum but still shows the counts', () => {
    render(
      <ProofOfLift
        lift={{ ...base, published_count: 3, judged_count: 3, beat_median_count: 2, below_median_count: 1 }}
      />,
    )
    expect(screen.getByText(/Too few judged clips to state a rate/i)).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
  })

  it('renders a rate WITH its confidence interval once available', () => {
    render(
      <ProofOfLift
        lift={{
          ...base,
          published_count: 10,
          judged_count: 10,
          beat_median_count: 7,
          below_median_count: 3,
          rate: 0.7,
          rate_ci_low: 0.397,
          rate_ci_high: 0.892,
        }}
      />,
    )
    expect(screen.getByText('70%')).toBeInTheDocument()
    // The interval must appear — a bare percentage would overstate certainty.
    expect(screen.getByText(/95% confidence interval 40%–89%/i)).toBeInTheDocument()
  })

  it('labels the contrast as drawn from the creator’s own clips', () => {
    render(
      <ProofOfLift
        lift={{
          ...base,
          published_count: 10,
          judged_count: 10,
          beat_median_count: 5,
          below_median_count: 5,
          contrast: {
            winners_n: 5,
            underperformers_n: 5,
            winners: {
              avg_score: 0.9,
              avg_dna_match: 0.8,
              median_duration_s: 40,
              median_setup_lead_s: 20,
              top_principle: 'clip-the-setup',
            },
            underperformers: {
              avg_score: 0.2,
              avg_dna_match: 0.3,
              median_duration_s: 15,
              median_setup_lead_s: 2,
              top_principle: 'clip-the-setup',
            },
          },
        }}
      />,
    )
    expect(screen.getByText(/not general advice/i)).toBeInTheDocument()
    expect(screen.getByText('40s')).toBeInTheDocument()
  })

  it('never promises virality', () => {
    const { container } = render(
      <ProofOfLift lift={{ ...base, published_count: 5, judged_count: 2, beat_median_count: 2 }} />,
    )
    expect(container.textContent?.toLowerCase()).not.toMatch(/viral|guarantee|will perform/)
  })
})
