import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { GenerateMoreClipsButton } from './GenerateMoreClipsButton'

const CLIP = {
  id: 'c9', video_id: 'v1', setup_start_s: 400, start_s: 400, end_s: 460, peak_s: 430,
  score: 0.8, rank: 13, principle: 'Curiosity gap', reasoning: 'Hook.',
  render_status: 'pending', render_uri: null, cleaned_render_uri: null,
  applied_title: null, applied_description: null,
}

function mockFetch(respond: () => unknown) {
  return vi.fn(async (_input: RequestInfo | URL) => respond())
}

function renderButton(onDone = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidateSpy = vi.spyOn(qc, 'invalidateQueries')
  render(
    <QueryClientProvider client={qc}>
      <GenerateMoreClipsButton videoId="v1" onDone={onDone} />
    </QueryClientProvider>,
  )
  return { onDone, invalidateSpy }
}

afterEach(() => vi.unstubAllGlobals())

describe('GenerateMoreClipsButton', () => {
  it('posts generate-more, invalidates the review queries, and reports the new total', async () => {
    const fetchMock = mockFetch(() => ({
      status: 200, ok: true, json: async () => ({ clips: [CLIP], message: null }),
    }))
    vi.stubGlobal('fetch', fetchMock)
    const { onDone, invalidateSpy } = renderButton()

    await userEvent.click(screen.getByRole('button', { name: 'Generate more clips' }))
    expect(await screen.findByRole('button', { name: 'Generate more clips' })).toBeEnabled()
    expect(
      fetchMock.mock.calls.filter(([u]) => String(u).endsWith('/videos/v1/clips/generate-more')),
    ).toHaveLength(1)
    expect(onDone).toHaveBeenCalledWith(1, null)
    const keys = invalidateSpy.mock.calls.map(([f]) => JSON.stringify(f?.queryKey))
    expect(keys).toContain(JSON.stringify(['review-clips', 'v1']))
    expect(keys).toContain(JSON.stringify(['clip-counts']))
  })

  it('shows the honest no-new-moments message without treating it as an error', async () => {
    vi.stubGlobal('fetch', mockFetch(() => ({
      status: 200, ok: true,
      json: async () => ({ clips: [CLIP], message: 'No new distinct moments found — try after more feedback.' }),
    })))
    const { onDone } = renderButton()

    await userEvent.click(screen.getByRole('button', { name: 'Generate more clips' }))
    expect(await screen.findByText(/No new distinct moments/)).toBeInTheDocument()
    expect(onDone).toHaveBeenCalledWith(1, 'No new distinct moments found — try after more feedback.')
    expect(screen.queryByRole('button', { name: 'Retry generate more' })).not.toBeInTheDocument()
  })

  it('surfaces the server-authored cap/billing error verbatim', async () => {
    vi.stubGlobal('fetch', mockFetch(() => ({
      status: 409, ok: false,
      json: async () => ({ detail: 'This video already has 24 generated clips — the per-video limit is reached.' }),
    })))
    const { onDone } = renderButton()

    await userEvent.click(screen.getByRole('button', { name: 'Generate more clips' }))
    expect(
      await screen.findByText(/already has 24 generated clips/),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry generate more' })).toBeInTheDocument()
    expect(onDone).not.toHaveBeenCalled()
  })
})
