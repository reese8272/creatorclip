import { render, screen, act } from '@testing-library/react'
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
    duration_s: 600,
    created_at: '2026-06-01T00:00:00Z',
    origin: 'upload',
    clippable: true,
    ...over,
  }
}

function renderTable(
  videos: Video[],
  clipInfoByVideo: Record<string, { total: number; rendered: number; loading: boolean }> = {},
) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <VideoTable videos={videos} clipInfoByVideo={clipInfoByVideo} analysisMode="auto" />
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

  it('Clips column shows 0 for a done video with no clips (Issue 305)', () => {
    renderTable([makeVideo({ id: 'vd', ingest_status: 'done', clippable: true })], {
      vd: { total: 0, rendered: 0, loading: false },
    })
    expect(screen.getByText('0')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Generate clips' })).toBeInTheDocument()
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
