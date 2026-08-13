import { render, screen, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { VideoTable } from './VideoTable'
import type { Video } from '@/types'

// Stub a minimal FakeEventSource that can emit named SSE events so the
// useStageStream hook can be exercised without a real backend.
class FakeEventSource {
  static instances: FakeEventSource[] = []
  url: string
  closed = false
  private listeners: Record<string, ((e: MessageEvent) => void)[]> = {}

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, cb: (e: MessageEvent) => void) {
    ;(this.listeners[type] ??= []).push(cb)
  }

  emit(type: string, data: unknown) {
    const evt = { data: JSON.stringify(data) } as MessageEvent
    ;(this.listeners[type] ?? []).forEach((cb) => cb(evt))
  }

  close() {
    this.closed = true
  }
}

function makeVideo(over: Partial<Video> = {}): Video {
  return {
    id: 'v1',
    youtube_video_id: 'yt123',
    title: 'Test video',
    kind: 'long',
    ingest_status: 'pending',
    failure_reason: null,
    duration_s: 600,
    created_at: '2026-06-01T00:00:00Z',
    origin: 'upload',
    has_poster: false,
    has_peaks: false,
    clippable: true,
    ...over,
  }
}

function renderTable(
  videos: Video[],
  clipInfoByVideo: Record<string, { total: number; rendered: number; loading: boolean }> = {},
  archivedView = false,
) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <VideoTable
          videos={videos}
          clipInfoByVideo={clipInfoByVideo}
          analysisMode="auto"
          archivedView={archivedView}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  FakeEventSource.instances = []
  vi.stubGlobal('EventSource', FakeEventSource)
})
afterEach(() => vi.unstubAllGlobals())

describe('VideoTable — StageStepper integration', () => {
  // Issue 388: the secondary line was `{kind} · {youtube_video_id}` — a raw ID
  // like dQw4w9WgXcQ, which is how you identify a row in a database, not how a
  // creator identifies their own footage.
  it('shows human metadata instead of the raw YouTube id', () => {
    renderTable([makeVideo({ youtube_video_id: 'dQw4w9WgXcQ', duration_s: 754, kind: 'long' })])
    expect(screen.queryByText(/dQw4w9WgXcQ/)).toBeNull()
    expect(screen.getByText(/Long-form/)).toBeInTheDocument()
    expect(screen.getByText(/12:34/)).toBeInTheDocument()
  })

  it('shows the Badge (not the stepper) for a done video — no SSE connection opened', () => {
    renderTable([makeVideo({ ingest_status: 'done', clippable: true })])
    expect(FakeEventSource.instances).toHaveLength(0)
    expect(screen.getByText('done')).toBeInTheDocument()
  })

  it('shows the Badge for a failed video — no SSE connection opened', () => {
    renderTable([makeVideo({ ingest_status: 'failed', clippable: true })])
    expect(FakeEventSource.instances).toHaveLength(0)
    expect(screen.getByText('failed')).toBeInTheDocument()
  })

  it('surfaces the persisted failure_reason on a failed video', () => {
    renderTable([
      makeVideo({
        ingest_status: 'failed',
        clippable: true,
        failure_reason: 'We couldn’t read your uploaded file from storage. Please re-upload.',
      }),
    ])
    expect(screen.getByText(/couldn’t read your uploaded file/i)).toBeInTheDocument()
  })

  it('opens one SSE connection for a pending clippable video', () => {
    renderTable([makeVideo({ ingest_status: 'pending', clippable: true })])
    expect(FakeEventSource.instances).toHaveLength(1)
    expect(FakeEventSource.instances[0].url).toContain('/tasks/v1/events')
  })

  it('does NOT open an SSE connection for a non-clippable linked video (no slot wasted)', () => {
    renderTable([makeVideo({ ingest_status: 'pending', clippable: false, origin: 'link' })])
    expect(FakeEventSource.instances).toHaveLength(0)
  })

  it('shows the StageStepper stage label when a step event arrives', () => {
    renderTable([makeVideo({ ingest_status: 'pending', clippable: true })])
    const es = FakeEventSource.instances[0]
    act(() => es.emit('step', { label: 'transcribe_start', stage: 'transcribe' }))
    expect(screen.getByText('Transcribing')).toBeInTheDocument()
  })

  it('falls back to the Badge after the SSE stream reports done', () => {
    renderTable([makeVideo({ ingest_status: 'done', clippable: true })])
    // No SSE opened for done rows — Badge is rendered directly.
    expect(FakeEventSource.instances).toHaveLength(0)
    expect(screen.getByText('done')).toBeInTheDocument()
  })

  it('Clips column shows the rendered count, and Actions becomes a Review link (Issue 305)', () => {
    renderTable([makeVideo({ id: 'vd', ingest_status: 'done', clippable: true })], {
      vd: { total: 3, rendered: 2, loading: false },
    })
    // Clips column surfaces the rendered count.
    expect(screen.getByText('2')).toBeInTheDocument()
    expect(screen.getByText('rendered')).toBeInTheDocument()
    // Action is "Review" (the count no longer lives on the button).
    expect(screen.getByRole('link', { name: 'Review' })).toBeInTheDocument()
  })

  it('shows a Create recap link for a clippable done video with clips (Issue 192)', () => {
    renderTable([makeVideo({ id: 'vd', ingest_status: 'done', clippable: true })], {
      vd: { total: 3, rendered: 2, loading: false },
    })
    const link = screen.getByRole('link', { name: 'Create recap' })
    expect(link).toHaveAttribute('href', '/video/vd/recap')
  })

  it('hides Create recap when the video is not clippable (Issue 192 gating)', () => {
    renderTable([makeVideo({ id: 'vd', ingest_status: 'done', clippable: false })], {
      vd: { total: 3, rendered: 2, loading: false },
    })
    expect(screen.queryByRole('link', { name: 'Create recap' })).toBeNull()
  })

  it('Clips column shows 0 for a done video with no clips (Issue 305)', () => {
    renderTable([makeVideo({ id: 'vd', ingest_status: 'done', clippable: true })], {
      vd: { total: 0, rendered: 0, loading: false },
    })
    expect(screen.getByText('0')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Generate clips' })).toBeInTheDocument()
  })

  it('shows the done label after a successful queue POST (button stays disabled)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) }) as Response),
    )
    renderTable([makeVideo({ ingest_status: 'pending', clippable: true })])
    await userEvent.click(screen.getByRole('button', { name: 'Queue for analysis' }))
    const btn = await screen.findByRole('button', { name: 'Queued' })
    expect(btn).toBeDisabled()
  })

  it('resets to an enabled Retry button when the queue POST rejects at the network level (Issue 352 Batch K)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
    )
    renderTable([makeVideo({ ingest_status: 'pending', clippable: true })])
    await userEvent.click(screen.getByRole('button', { name: 'Queue for analysis' }))
    const btn = await screen.findByRole('button', { name: 'Retry' })
    expect(btn).toBeEnabled()
  })

  it('resets to Retry on an HTTP error response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          ({ ok: false, status: 500, json: async () => ({ detail: 'boom' }) }) as Response,
      ),
    )
    renderTable([makeVideo({ ingest_status: 'pending', clippable: true })])
    await userEvent.click(screen.getByRole('button', { name: 'Queue for analysis' }))
    expect(await screen.findByRole('button', { name: 'Retry' })).toBeEnabled()
  })

  it('a 10-row table with 9 done + 1 in-flight opens exactly 1 SSE connection', () => {
    const videos: Video[] = [
      ...Array.from({ length: 9 }, (_, i) =>
        makeVideo({ id: `done-${i}`, ingest_status: 'done', clippable: true }),
      ),
      makeVideo({ id: 'running', ingest_status: 'running', clippable: true }),
    ]
    renderTable(videos)
    expect(FakeEventSource.instances).toHaveLength(1)
    expect(FakeEventSource.instances[0].url).toContain('/tasks/running/events')
  })
})

describe('VideoTable — archive / restore / erase (Issue 446)', () => {
  it('every row offers an Archive action, and clicking it DELETEs the video', async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        ({ ok: true, status: 200, json: async () => ({}) }) as Response,
    )
    vi.stubGlobal('fetch', fetchMock)
    renderTable([makeVideo({ id: 'va', ingest_status: 'done', clippable: true })], {
      va: { total: 0, rendered: 0, loading: false },
    })
    await userEvent.click(screen.getByRole('button', { name: 'Archive' }))
    const call = fetchMock.mock.calls.find(([u]) => String(u).endsWith('/videos/va'))
    expect(call).toBeTruthy()
    expect((call![1] as RequestInit).method).toBe('DELETE')
  })

  it('archived view swaps actions to Restore + Delete permanently', () => {
    renderTable(
      [makeVideo({ id: 'va', ingest_status: 'done', archived_at: '2026-08-10T00:00:00Z' })],
      {},
      true,
    )
    expect(screen.getByRole('button', { name: 'Restore' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Delete permanently' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Archive' })).toBeNull()
    // No SSE slot is held for archived rows.
    expect(FakeEventSource.instances).toHaveLength(0)
  })

  it('Restore POSTs to /videos/{id}/restore', async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        ({ ok: true, status: 200, json: async () => ({}) }) as Response,
    )
    vi.stubGlobal('fetch', fetchMock)
    renderTable([makeVideo({ id: 'va', archived_at: '2026-08-10T00:00:00Z' })], {}, true)
    await userEvent.click(screen.getByRole('button', { name: 'Restore' }))
    const call = fetchMock.mock.calls.find(([u]) => String(u).endsWith('/videos/va/restore'))
    expect(call).toBeTruthy()
    expect((call![1] as RequestInit).method).toBe('POST')
  })

  it('Delete permanently requires an explicit confirm naming the feedback loss', async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        ({ ok: true, status: 200, json: async () => ({}) }) as Response,
    )
    vi.stubGlobal('fetch', fetchMock)
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    try {
      renderTable([makeVideo({ id: 'va', archived_at: '2026-08-10T00:00:00Z' })], {}, true)
      await userEvent.click(screen.getByRole('button', { name: 'Delete permanently' }))
      // Declined → no request fired; the confirm copy states the training-data loss.
      expect(fetchMock).not.toHaveBeenCalled()
      expect(confirmSpy.mock.calls[0][0]).toMatch(/feedback/i)
      expect(confirmSpy.mock.calls[0][0]).toMatch(/cannot be undone/i)

      confirmSpy.mockReturnValue(true)
      await userEvent.click(screen.getByRole('button', { name: 'Delete permanently' }))
      const call = fetchMock.mock.calls.find(([u]) => String(u).endsWith('/videos/va/erase'))
      expect(call).toBeTruthy()
      expect((call![1] as RequestInit).method).toBe('POST')
    } finally {
      confirmSpy.mockRestore()
    }
  })
})

describe('VideoTable — source expiry surface (Issue 446)', () => {
  it('shows the retention deadline while the source is stored', () => {
    renderTable([
      makeVideo({
        ingest_status: 'done',
        clippable: true,
        source_expires_at: '2026-08-16T12:00:00Z',
        source_expired: false,
      }),
    ])
    expect(screen.getByText(/Source kept until/)).toBeInTheDocument()
  })

  it('states plainly that an expired source cannot be re-rendered', () => {
    renderTable([
      makeVideo({
        ingest_status: 'done',
        clippable: false,
        source_expires_at: null,
        source_expired: true,
      }),
    ])
    expect(screen.getByText(/can’t be re-rendered/)).toBeInTheDocument()
  })
})
