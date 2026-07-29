/**
 * AppliedTitleField — inline publish-title editor on the Your-call card.
 *
 * 80/20: the collapsed→edit→save happy path (PATCH applied_title), the
 * clear-on-empty-save null semantics, and the set-state display row.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppliedTitleField } from './AppliedTitleField'
import type { ReviewClip } from '@/types'

const CLIP: ReviewClip = {
  id: 'c1',
  video_id: 'v1',
  setup_start_s: 2,
  start_s: 0,
  end_s: 20,
  peak_s: 10,
  score: 0.9,
  rank: 1,
  principle: 'Curiosity gap',
  reasoning: 'Strong hook.',
  render_status: 'done',
  render_uri: 'http://x/c1.mp4',
  cleaned_render_uri: null,
  applied_title: null,
  applied_description: null,
}

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

function patchFetch() {
  return vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) }))
}

afterEach(() => vi.unstubAllGlobals())

describe('AppliedTitleField', () => {
  it('expands from the subtle affordance and PATCHes the typed title on save', async () => {
    const fetchMock = patchFetch()
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<AppliedTitleField clip={CLIP} />, { wrapper })

    await user.click(screen.getByText(/Set the title this clip publishes with/))
    await user.type(screen.getByLabelText('Publish title'), 'My better title')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      const patch = fetchMock.mock.calls.find(
        ([, init]) => (init as RequestInit | undefined)?.method === 'PATCH',
      )
      expect(patch).toBeTruthy()
      expect(String(patch![0])).toBe('/clips/c1')
      expect(JSON.parse(String((patch![1] as RequestInit).body))).toEqual({
        applied_title: 'My better title',
      })
    })
  })

  it('shows the applied title with an Edit affordance when set', () => {
    vi.stubGlobal('fetch', patchFetch())
    render(<AppliedTitleField clip={{ ...CLIP, applied_title: 'Kept title' }} />, { wrapper })
    expect(screen.getByText('Kept title')).toBeInTheDocument()
    expect(screen.getByText('Edit')).toBeInTheDocument()
  })

  it('saving an empty value clears the title (PATCH null)', async () => {
    const fetchMock = patchFetch()
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<AppliedTitleField clip={{ ...CLIP, applied_title: 'Kept title' }} />, { wrapper })

    await user.click(screen.getByText('Edit'))
    await user.clear(screen.getByLabelText('Publish title'))
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      const patch = fetchMock.mock.calls.find(
        ([, init]) => (init as RequestInit | undefined)?.method === 'PATCH',
      )
      expect(patch).toBeTruthy()
      expect(JSON.parse(String((patch![1] as RequestInit).body))).toEqual({ applied_title: null })
    })
  })
})
