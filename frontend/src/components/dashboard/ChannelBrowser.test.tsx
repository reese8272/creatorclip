import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ChannelBrowser } from './ChannelBrowser'
import type { CatalogListResponse } from '@/types'

function catalogPayload(): CatalogListResponse {
  return {
    videos: [
      {
        id: 'cat-1',
        youtube_video_id: 'abc12345678',
        title: 'Synced channel video',
        kind: 'long',
        ingest_status: 'pending',
        duration_s: 600,
        created_at: '2026-06-01T00:00:00Z',
        origin: 'catalog',
        clippable: false,
      },
    ],
    total: 1,
    limit: 50,
    offset: 0,
  }
}

// A fetch stub that returns the catalog payload for GET /videos/catalog and a
// 200 for the POST /videos/link adopt call. Returns the spy so tests can assert
// the link call carried FormData with youtube_video_id.
function stubFetch() {
  const fetchSpy = vi.fn(async (url: string, _init?: RequestInit) => {
    if (typeof url === 'string' && url.startsWith('/videos/catalog')) {
      return {
        ok: true,
        status: 200,
        json: async () => catalogPayload(),
      } as Response
    }
    // POST /videos/link
    return { ok: true, status: 200, json: async () => ({ video_id: 'v1' }) } as Response
  })
  vi.stubGlobal('fetch', fetchSpy)
  return fetchSpy
}

function renderBrowser(qc: QueryClient) {
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ChannelBrowser open onClose={() => {}} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => stubFetch())
afterEach(() => vi.unstubAllGlobals())

describe('ChannelBrowser', () => {
  it('renders one row per catalog video with its title and youtube_video_id', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    renderBrowser(qc)
    expect(await screen.findByText('Synced channel video')).toBeInTheDocument()
    expect(screen.getByText(/abc12345678/)).toBeInTheDocument()
  })

  it('"Clip this" posts FormData to /videos/link with youtube_video_id', async () => {
    const fetchSpy = stubFetch()
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    renderBrowser(qc)
    const btn = await screen.findByRole('button', { name: 'Clip this' })
    await userEvent.click(btn)

    await waitFor(() => {
      const linkCall = fetchSpy.mock.calls.find((c) => c[0] === '/videos/link')
      expect(linkCall).toBeTruthy()
      const init = linkCall![1] as RequestInit
      expect(init.method).toBe('POST')
      expect(init.body).toBeInstanceOf(FormData)
      expect((init.body as FormData).get('youtube_video_id')).toBe('abc12345678')
    })
  })

  it('invalidates the videos and catalog query keys after a successful Clip this', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries')
    renderBrowser(qc)
    const btn = await screen.findByRole('button', { name: 'Clip this' })
    await userEvent.click(btn)

    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['videos'] })
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['catalog'] })
    })
  })

  it('surface contains no virality language (structural honesty)', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { container } = renderBrowser(qc)
    await screen.findByText('Synced channel video')
    expect(container.textContent ?? '').not.toMatch(/viral|virality|guarantee|guaranteed/i)
  })
})
