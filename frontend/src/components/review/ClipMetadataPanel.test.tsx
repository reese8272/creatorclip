/**
 * ClipMetadataPanel (L26 Issue 424) — the metadata half of the old WhyThisClip.
 *
 * 80/20: the `applied_* ?? suggested_*` precedence, the Apply chip going through
 * the EXISTING useApplyClipMetadata PATCH, honest fallback when suggested_* are
 * absent (Track A lands them server-side later), and the Regenerate path with
 * its disclaimer intact.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ClipMetadataPanel } from './ClipMetadataPanel'
import type { ReviewClip } from '@/types'

const CLIP: ReviewClip = {
  id: 'clip-1',
  video_id: 'vid-1',
  setup_start_s: 5,
  start_s: 7,
  end_s: 67,
  peak_s: 35,
  score: 0.82,
  rank: 1,
  principle: 'Open Loop',
  reasoning: 'Strong hook.',
  render_status: 'done',
  render_uri: null,
  cleaned_render_uri: null,
  applied_title: null,
  applied_description: null,
  origin: 'engine',
  aspect: '9:16',
  shortlisted: true,
  suggested_title: 'The Suggested Title',
  suggested_description: 'The suggested description. #Shorts',
  suggested_hook: 'The suggested hook',
}

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

afterEach(() => vi.unstubAllGlobals())

function patchFetch() {
  return vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => ({
    ok: true,
    status: 200,
    json: async () => ({}),
  }))
}

describe('ClipMetadataPanel', () => {
  it('shows suggested values with the Suggested chip when nothing is applied', () => {
    render(<ClipMetadataPanel clip={CLIP} />, { wrapper })
    expect(screen.getByText('The Suggested Title')).toBeInTheDocument()
    expect(screen.getByText('The suggested description. #Shorts')).toBeInTheDocument()
    expect(screen.getAllByText(/Suggested ·/)).toHaveLength(2)
    expect(screen.queryByText('Applied')).toBeNull()
  })

  it('applied values win over suggestions and show the Applied chip', () => {
    render(
      <ClipMetadataPanel
        clip={{ ...CLIP, applied_title: 'My Own Title', applied_description: 'My own hook' }}
      />,
      { wrapper },
    )
    expect(screen.getByText('My Own Title')).toBeInTheDocument()
    expect(screen.getByText('My own hook')).toBeInTheDocument()
    expect(screen.queryByText('The Suggested Title')).toBeNull()
    expect(screen.getAllByText('Applied')).toHaveLength(2)
  })

  // Issue 452 (DECISIONS 2026-08-12) reverses this test's original assertion. It used to
  // pin "title and hook are one TRUNCATING row each when collapsed" — one `.truncate` per
  // row plus a native `title=` tooltip as the escape hatch. The creator reported that the
  // hover "sometimes cuts off too": native tooltips clip long strings themselves, so the
  // escape hatch failed exactly like the thing it was escaping. Owner decision: expand to
  // fit. Same fix Issue 445 made to the pile rows.
  it('title and hook WRAP rather than truncate, with no tooltip escape hatch', () => {
    const longTitle =
      'A clip title long enough to overflow the metadata panel several times over, ' +
      'which is precisely when the creator most needs to read the whole thing'
    render(<ClipMetadataPanel clip={{ ...CLIP, suggested_title: longTitle }} />, { wrapper })

    const titleRow = screen.getByTestId('metadata-row-title')
    const hookRow = screen.getByTestId('metadata-row-hook')

    // The full string is present and not clipped by CSS.
    expect(screen.getByText(longTitle)).toBeInTheDocument()
    expect(titleRow.querySelectorAll('.truncate')).toHaveLength(0)
    expect(hookRow.querySelectorAll('.truncate')).toHaveLength(0)
    expect(titleRow.querySelectorAll('.break-words').length).toBeGreaterThan(0)

    // No native tooltip is relied on to reveal the value — it clipped too.
    expect(titleRow.querySelector('[title]')).toBeNull()
    expect(hookRow.querySelector('[title]')).toBeNull()

    // The actions rail must not float against the middle of a wrapped block.
    expect(titleRow.className).toContain('items-start')
  })

  it('Apply on the title row PATCHes the suggested title through the existing hook', async () => {
    const fetchMock = patchFetch()
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<ClipMetadataPanel clip={CLIP} />, { wrapper })

    const titleRow = screen.getByTestId('metadata-row-title')
    await user.click(titleRow.querySelector('button')!)

    await waitFor(() => {
      const patch = fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH')
      expect(patch).toBeTruthy()
      expect(String(patch![0])).toBe(`/clips/${CLIP.id}`)
      expect(JSON.parse(String(patch![1]!.body))).toEqual({
        applied_title: 'The Suggested Title',
      })
    })
  })

  it('renders honestly when suggested_* are absent — no value, no Soon badge, editors work', () => {
    render(
      <ClipMetadataPanel
        clip={{
          ...CLIP,
          suggested_title: undefined,
          suggested_description: undefined,
          suggested_hook: undefined,
        }}
      />,
      { wrapper },
    )
    // Empty rows, edit affordances, and no vaporware placeholders.
    expect(screen.queryByText(/Soon/)).toBeNull()
    expect(screen.queryByText(/Suggested ·/)).toBeNull()
    expect(screen.getByRole('button', { name: 'Edit title' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Edit hook' })).toBeInTheDocument()
  })

  it('Edit opens the applied-title editor directly (one click)', async () => {
    const user = userEvent.setup()
    render(<ClipMetadataPanel clip={CLIP} />, { wrapper })
    await user.click(screen.getByRole('button', { name: 'Edit title' }))
    expect(screen.getByRole('textbox', { name: 'Publish title' })).toBeInTheDocument()
    // Cancel collapses back to the compact row.
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('textbox', { name: 'Publish title' })).toBeNull()
    expect(screen.getByText('The Suggested Title')).toBeInTheDocument()
  })

  it('the description editor counts BYTES against the YouTube limit', async () => {
    const user = userEvent.setup()
    render(<ClipMetadataPanel clip={CLIP} />, { wrapper })
    await user.click(screen.getByRole('button', { name: 'Edit hook' }))
    const box = screen.getByRole('textbox', { name: 'Publish description' })
    await user.type(box, 'héllo') // 5 chars, 6 UTF-8 bytes
    expect(screen.getByText('6/5000 bytes')).toBeInTheDocument()
  })

  it('hosts the regenerate paths behind More suggestions with the disclaimer intact', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => ({
          titles: [{ title: 'Regenerated', rationale: 'r', ctr_signal: 'up' }],
          hook_rewrites: [],
          disclaimer: 'Estimates grounded in your channel data.',
        }),
      })),
    )
    const user = userEvent.setup()
    render(<ClipMetadataPanel clip={CLIP} />, { wrapper })

    expect(screen.getByText('More suggestions')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Regenerate titles' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Regenerate caption hooks' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Regenerate titles' }))
    expect(await screen.findByText('Regenerated')).toBeInTheDocument()
    expect(screen.getByText('Estimates grounded in your channel data.')).toBeInTheDocument()
  })

  it('never promises virality in its static copy', () => {
    const { container } = render(<ClipMetadataPanel clip={CLIP} />, { wrapper })
    const text = container.textContent?.toLowerCase() ?? ''
    expect(text).not.toMatch(/\bviral\b|\bguarantee/)
  })
})
