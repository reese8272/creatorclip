/**
 * CleanedPreviewConfirm — the shared review-then-confirm affordance for the
 * clean pass (Editor) and the trim re-render (Review).
 *
 * 80/20: the stale-cache-latch regression. Confirm nulls cleaned_render_uri
 * server-side; if the poll cache under ['clips-clean-poll', videoId] survives
 * (global staleTime 30s), a second trim within 30s remounts against the OLD
 * URI, shows "Use cleaned version" for the already-swapped-in render, and
 * never polls for the new one.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CleanedPreviewConfirm } from './CleanedPreviewConfirm'
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
  origin: 'engine' as const,
  aspect: '9:16',
}

// Mutable server truth: the GET poll serves whatever cleanedUri currently is.
function makeFetch(state: { cleanedUri: string | null }) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (init?.method === 'POST' && url.endsWith('/clips/c1/clean/confirm'))
      return { status: 200, ok: true, json: async () => ({}) }
    if (url.endsWith('/videos/v1/clips'))
      return {
        status: 200,
        ok: true,
        json: async () => ({ clips: [{ ...CLIP, cleaned_render_uri: state.cleanedUri }] }),
      }
    return { status: 200, ok: true, json: async () => ({}) }
  })
}

function renderConfirm(qc: QueryClient, onConfirmed = () => {}) {
  return render(
    <QueryClientProvider client={qc}>
      <CleanedPreviewConfirm
        clip={CLIP}
        enabled
        onConfirmed={onConfirmed}
        onDiscarded={() => {}}
        onError={() => {}}
      />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('CleanedPreviewConfirm', () => {
  it('polls until the cleaned URI lands, then offers confirm which POSTs /clean/confirm', async () => {
    const state: { cleanedUri: string | null } = { cleanedUri: 's3://bucket/c1-cleaned.mp4' }
    const fetchMock = makeFetch(state)
    vi.stubGlobal('fetch', fetchMock)
    const onConfirmed = vi.fn()
    // Mirror the app-wide 30s staleTime so the test exercises real conditions.
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 30_000 } },
    })

    renderConfirm(qc, onConfirmed)
    await userEvent.click(await screen.findByRole('button', { name: 'Use cleaned version' }))

    await waitFor(() => expect(onConfirmed).toHaveBeenCalled())
    expect(
      fetchMock.mock.calls.some(
        ([u, init]) => String(u).endsWith('/clips/c1/clean/confirm') && init?.method === 'POST',
      ),
    ).toBe(true)
  })

  it('does not serve the previous URI to a remounted poll after confirm (stale-latch regression)', async () => {
    const state: { cleanedUri: string | null } = { cleanedUri: 's3://bucket/c1-cleaned.mp4' }
    const fetchMock = makeFetch(state)
    vi.stubGlobal('fetch', fetchMock)
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 30_000 } },
    })

    // First trim: poll lands the URI, creator confirms the swap.
    const first = renderConfirm(qc)
    await userEvent.click(await screen.findByRole('button', { name: 'Use cleaned version' }))
    await waitFor(() =>
      expect(qc.getQueryData(['clips-clean-poll', 'v1'])).toBeUndefined(),
    )
    first.unmount()

    // Second trim within 30s: server has nulled cleaned_render_uri (new render
    // in flight). The remounted poll must refetch — not render "Use cleaned
    // version" instantly from the previous trim's cached URI.
    state.cleanedUri = null
    const getsBefore = fetchMock.mock.calls.filter(([u]) =>
      String(u).endsWith('/videos/v1/clips'),
    ).length
    renderConfirm(qc)

    await waitFor(() => {
      const getsAfter = fetchMock.mock.calls.filter(([u]) =>
        String(u).endsWith('/videos/v1/clips'),
      ).length
      expect(getsAfter).toBeGreaterThan(getsBefore)
    })
    expect(screen.queryByRole('button', { name: 'Use cleaned version' })).not.toBeInTheDocument()
  })
})
