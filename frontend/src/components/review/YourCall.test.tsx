/**
 * YourCall — trim → re-render flow (Wave-1 trim-rerender contract).
 *
 * 80/20: the POST body (clip-relative seconds), the review-then-confirm
 * affordance riding cleaned_render_uri, and the two load-bearing error edges
 * (409 pending_clean_or_edit surfaces the pending preview; 422 messages show
 * verbatim). Save-trim feedback logging is unchanged and untested here.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { YourCall } from './YourCall'
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

function renderYourCall(clip: ReviewClip = CLIP) {
  return render(
    <YourCall clip={clip} trimStart={2} trimEnd={18} onAdvance={() => {}} />,
    { wrapper },
  )
}

// Fetch mock: trim-render 202, clip-list poll returns the cleaned URI, confirm 200.
function trimRenderFetch(
  trimResponse: { status: number; body: unknown } = {
    status: 202,
    body: { task_id: 't1', status: 'queued', stream_url: '/tasks/c1/events' },
  },
) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const json = (body: unknown, status = 200) => ({
      status,
      ok: status < 400,
      json: async () => body,
    })
    if (init?.method === 'POST' && url.endsWith('/clips/c1/trim-render'))
      return json(trimResponse.body, trimResponse.status)
    if (url.endsWith('/videos/v1/clips'))
      return json({ clips: [{ ...CLIP, cleaned_render_uri: '/media/c1-trimmed.mp4' }] })
    if (init?.method === 'POST' && url.endsWith('/clips/c1/clean/confirm')) return json({})
    return json({})
  })
}

afterEach(() => vi.unstubAllGlobals())

describe('YourCall — apply trim & re-render', () => {
  it('POSTs the clip-relative trim window to /trim-render', async () => {
    const fetchMock = trimRenderFetch()
    vi.stubGlobal('fetch', fetchMock)
    renderYourCall()

    await userEvent.click(screen.getByRole('button', { name: /Apply trim & re-render/ }))

    await waitFor(() => {
      const post = fetchMock.mock.calls.find(
        ([u, init]) => String(u).endsWith('/clips/c1/trim-render') && init?.method === 'POST',
      )
      expect(post).toBeTruthy()
      expect(JSON.parse(String(post![1]!.body))).toEqual({ trim_start_s: 2, trim_end_s: 18 })
    })
  })

  it('shows the confirm affordance once the poll returns cleaned_render_uri, and confirms', async () => {
    const fetchMock = trimRenderFetch()
    vi.stubGlobal('fetch', fetchMock)
    renderYourCall()

    await userEvent.click(screen.getByRole('button', { name: /Apply trim & re-render/ }))

    // Poll lands the cleaned URI → review-then-confirm appears.
    const confirmBtn = await screen.findByRole('button', { name: 'Use cleaned version' })
    expect(screen.getByRole('button', { name: 'Keep original' })).toBeInTheDocument()

    await userEvent.click(confirmBtn)
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          ([u, init]) =>
            String(u).endsWith('/clips/c1/clean/confirm') && init?.method === 'POST',
        ),
      ).toBe(true)
    })
    expect(await screen.findByText(/Trimmed version is now the main render/)).toBeInTheDocument()
  })

  it('surfaces the 409 pending_clean_or_edit message and the pending preview', async () => {
    const fetchMock = trimRenderFetch({
      status: 409,
      body: {
        detail: {
          code: 'pending_clean_or_edit',
          message: 'Confirm or discard the pending cleaned/edited version first.',
        },
      },
    })
    vi.stubGlobal('fetch', fetchMock)
    renderYourCall()

    await userEvent.click(screen.getByRole('button', { name: /Apply trim & re-render/ }))

    expect(
      await screen.findByText('Confirm or discard the pending cleaned/edited version first.'),
    ).toBeInTheDocument()
    // The blocking artifact's preview is surfaced so the creator can decide.
    expect(await screen.findByRole('button', { name: 'Use cleaned version' })).toBeInTheDocument()
  })

  it('shows 422 validation messages verbatim (limits embedded server-side)', async () => {
    vi.stubGlobal(
      'fetch',
      trimRenderFetch({
        status: 422,
        body: {
          detail: { code: 'trim_noop', message: 'Trim covers the full clip — nothing to remove.' },
        },
      }),
    )
    renderYourCall()

    await userEvent.click(screen.getByRole('button', { name: /Apply trim & re-render/ }))
    expect(
      await screen.findByText('Trim covers the full clip — nothing to remove.'),
    ).toBeInTheDocument()
  })

  it('disables the re-render button until the clip has a render', () => {
    vi.stubGlobal('fetch', trimRenderFetch())
    renderYourCall({ ...CLIP, render_uri: null })
    expect(screen.getByRole('button', { name: /Apply trim & re-render/ })).toBeDisabled()
  })
})
