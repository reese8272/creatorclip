import { render, screen, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CaptionStylePanel } from './CaptionStylePanel'
import type { BrandKit, ReviewClip } from '@/types'

// Issue 352 Batch K (carried from the 2026-06-24 e2e assessment): after queueing
// a styled render the panel must subscribe to the render task's SSE and, on
// done, invalidate the review-clips query so render_uri surfaces live.

// Capture the handlers passed to the (mocked) SSE transport so tests can drive
// the stream to done/error without a real EventSource.
const captured = vi.hoisted(() => ({
  urls: [] as string[],
  handlers: null as null | {
    onDone?: (data: Record<string, unknown>) => void
    onError?: (message: string) => void
  },
}))

vi.mock('@/lib/taskStream', () => ({
  subscribeToTaskStream: vi.fn((url: string, handlers: never) => {
    captured.urls.push(url)
    captured.handlers = handlers
    return { close: vi.fn() }
  }),
}))

const kit: BrandKit = {
  subtitle: 'bold_pop',
  background: null,
  captions_enabled: true,
  zoom_on_peak: false,
  denoise: false,
  aspect: null,
}

function makeClip(): ReviewClip {
  return {
    id: 'c1',
    video_id: 'v1',
    setup_start_s: 0,
    start_s: 0,
    end_s: 30,
    peak_s: 15,
    score: 0.8,
    rank: 1,
    principle: 'Setup-Payoff Integrity',
    reasoning: 'test',
    render_status: 'done',
    render_uri: '/media/old.mp4',
    cleaned_render_uri: null,
  }
}

function stubFetch(renderResponse: () => Promise<Partial<Response>>) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      if (typeof url === 'string' && url.startsWith('/creators/me/brand-kit')) {
        return { ok: true, status: 200, json: async () => kit } as Response
      }
      return (await renderResponse()) as Response
    }),
  )
}

function renderPanel(qc: QueryClient) {
  return render(
    <QueryClientProvider client={qc}>
      <CaptionStylePanel clip={makeClip()} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  captured.urls = []
  captured.handlers = null
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

describe('CaptionStylePanel', () => {
  it('subscribes to the returned render SSE and invalidates review-clips on done', async () => {
    stubFetch(async () => ({
      ok: true,
      status: 202,
      json: async () => ({ task_id: 't1', status: 'queued', stream_url: '/tasks/c1/events' }),
    }))
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries')
    renderPanel(qc)

    await userEvent.click(screen.getByRole('button', { name: 'Render with style' }))

    // Streaming: subscribed to the owner-stamped stream, button disabled.
    expect(await screen.findByText('Rendering…')).toBeInTheDocument()
    expect(captured.urls).toEqual(['/tasks/c1/events'])
    expect(screen.getByRole('button', { name: 'Render with style' })).toBeDisabled()

    // Worker finishes → done event → invalidation + ready copy + re-enabled.
    act(() => captured.handlers?.onDone?.({}))
    expect(await screen.findByText('Styled render ready ✓')).toBeInTheDocument()
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['review-clips', 'v1'] })
    expect(screen.getByRole('button', { name: 'Render with style' })).toBeEnabled()
  })

  it('falls back to the check-back copy when the queue response has no stream_url', async () => {
    stubFetch(async () => ({
      ok: true,
      status: 202,
      json: async () => ({ task_id: 't1', status: 'queued', stream_url: null }),
    }))
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    renderPanel(qc)

    await userEvent.click(screen.getByRole('button', { name: 'Render with style' }))
    expect(await screen.findByText(/come back in ~30s/i)).toBeInTheDocument()
    expect(captured.urls).toEqual([])
  })

  it('surfaces the API error and opens no stream when the POST fails', async () => {
    stubFetch(async () => ({
      ok: false,
      status: 429,
      json: async () => ({ detail: 'Daily render limit reached' }),
    }))
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    renderPanel(qc)

    await userEvent.click(screen.getByRole('button', { name: 'Render with style' }))
    expect(await screen.findByText('Daily render limit reached')).toBeInTheDocument()
    expect(captured.urls).toEqual([])
    expect(screen.getByRole('button', { name: 'Render with style' })).toBeEnabled()
  })
})
