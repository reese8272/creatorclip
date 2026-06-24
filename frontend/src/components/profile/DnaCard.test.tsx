import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DnaCard } from './DnaCard'
import type { Identity, NicheOption } from '@/types'

const NICHES: NicheOption[] = [{ id: 'tech', label: 'Tech & tutorials' }]
const IDENTITY: Identity = {
  version: 2,
  created_at: '2026-06-01T00:00:00Z',
  niches: ['tech'],
  audience_summary: 'devs',
  content_pillars: ['Fast, tight cuts'],
  tone_tags: ['Casual, direct'],
  hard_nos: [],
}

function mockDna(profile: unknown) {
  const json = (body: unknown) => ({ status: 200, ok: true, json: async () => body })
  return vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith('/creators/me/dna')) return json({ profile })
    return json({})
  })
}

afterEach(() => vi.unstubAllGlobals())

describe('DnaCard', () => {
  it('active DNA → read-only snapshot: trait chips + Re-sync/View full, no Confirm', async () => {
    vi.stubGlobal(
      'fetch',
      mockDna({
        version: 3,
        status: 'confirmed',
        created_at: '2026-06-10T00:00:00Z',
        brief_text: '# Brief\nbody',
        optimal_clip_len_s: 40,
        best_source_region: null,
        optimal_upload_gap_h: null,
      }),
    )
    render(<DnaCard identity={IDENTITY} niches={NICHES} />)

    expect(await screen.findByText('Signature traits')).toBeInTheDocument()
    expect(screen.getByText('Fast, tight cuts')).toBeInTheDocument()
    expect(screen.getByText('Tech & tutorials')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Re-sync DNA/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /View full DNA/i })).toBeInTheDocument()
    // Onboarding-only activation control must NOT appear once active.
    expect(screen.queryByRole('button', { name: /Confirm & activate/i })).toBeNull()
  })

  it('View full DNA toggles the brief into view', async () => {
    vi.stubGlobal(
      'fetch',
      mockDna({
        version: 3,
        status: 'confirmed',
        created_at: '2026-06-10T00:00:00Z',
        brief_text: 'My creator brief body',
        optimal_clip_len_s: null,
        best_source_region: null,
        optimal_upload_gap_h: null,
      }),
    )
    render(<DnaCard identity={IDENTITY} niches={NICHES} />)
    const toggle = await screen.findByRole('button', { name: /View full DNA/i })
    expect(screen.queryByText(/My creator brief body/)).toBeNull()
    await userEvent.click(toggle)
    expect(screen.getByText(/My creator brief body/)).toBeInTheDocument()
  })

  it('pending DNA → keeps the Confirm & activate path (onboarding hand-off)', async () => {
    vi.stubGlobal(
      'fetch',
      mockDna({
        version: 1,
        status: 'draft',
        created_at: '2026-06-10T00:00:00Z',
        brief_text: 'draft brief',
        optimal_clip_len_s: null,
        best_source_region: null,
        optimal_upload_gap_h: null,
      }),
    )
    render(<DnaCard identity={IDENTITY} niches={NICHES} />)
    expect(await screen.findByRole('button', { name: /Confirm & activate/i })).toBeInTheDocument()
    expect(screen.queryByText('Signature traits')).toBeNull()
  })
})
