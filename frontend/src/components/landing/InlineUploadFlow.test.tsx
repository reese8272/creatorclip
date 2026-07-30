import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { InlineUploadFlow } from './InlineUploadFlow'

// Never open a real EventSource; the stepper just needs the hook to mount.
vi.mock('@/lib/taskStream', () => ({
  subscribeToTaskStream: vi.fn(() => ({ close: vi.fn() })),
}))

// Minimal XHR fake for UploadVideoForm's progress-aware upload path.
class FakeXHR {
  static instances: FakeXHR[] = []
  upload = { onprogress: null as ((e: ProgressEvent) => void) | null }
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  status = 0
  responseText = ''
  withCredentials = false
  open = vi.fn()
  send = vi.fn(() => {
    FakeXHR.instances.push(this)
  })
  respond(status: number, body: unknown) {
    this.status = status
    this.responseText = JSON.stringify(body)
    this.onload?.()
  }
}

function mockFetch(uploadedRow: Record<string, unknown>) {
  const json = (body: unknown) => ({ status: 200, ok: true, json: async () => body })
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/videos')) return json({ videos: [uploadedRow], state: 'populated' })
    if (url.endsWith('/clips/generate'))
      return json({
        clips: [{ id: 'c1', video_id: 'v9', render_status: 'pending' }],
      })
    return json({})
  })
}

function row(ingest_status: string, over: Record<string, unknown> = {}) {
  return {
    id: 'v9', youtube_video_id: null, title: 'Fresh upload', kind: 'video',
    ingest_status, failure_reason: null, duration_s: 120,
    created_at: '2026-07-30T00:00:00Z', origin: 'upload', clippable: true,
    ...over,
  }
}

function renderFlow(onReady = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <InlineUploadFlow onReady={onReady} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return onReady
}

async function uploadFile() {
  const input = screen.getByLabelText('Video file to upload')
  await userEvent.upload(input, new File(['x'], 'clip.mp4', { type: 'video/mp4' }))
  await userEvent.click(screen.getByRole('button', { name: 'Upload' }))
  const xhr = FakeXHR.instances.at(-1)!
  act(() => xhr.respond(200, { video_id: 'v9', status: 'pending' }))
}

afterEach(() => {
  vi.unstubAllGlobals()
  FakeXHR.instances = []
})

describe('InlineUploadFlow', () => {
  it('moves from upload to the in-place processing card on success', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXHR)
    vi.stubGlobal('fetch', mockFetch(row('running')))
    renderFlow()
    await uploadFile()
    expect(await screen.findByText(/Fresh upload/)).toBeInTheDocument()
    expect(
      screen.getByText(/we’ll offer clip generation when analysis finishes/),
    ).toBeInTheDocument()
  })

  it('offers an explicit generate step when ingest is done — and never auto-fires it', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXHR)
    const fetchMock = mockFetch(row('done'))
    vi.stubGlobal('fetch', fetchMock)
    const onReady = renderFlow()
    await uploadFile()
    // Billing consent: generation costs minutes, so it must be a click.
    expect(await screen.findByRole('button', { name: 'Generate clips' })).toBeInTheDocument()
    expect(screen.getByText(/uses\s+your minutes/)).toBeInTheDocument()
    expect(
      fetchMock.mock.calls.filter(([u]) => String(u).endsWith('/clips/generate')),
    ).toHaveLength(0)

    await userEvent.click(screen.getByRole('button', { name: 'Generate clips' }))
    await screen.findByRole('button', { name: 'Generate clips' }) // settle
    expect(
      fetchMock.mock.calls.filter(([u]) => String(u).endsWith('/videos/v9/clips/generate')),
    ).toHaveLength(1)
    expect(onReady).toHaveBeenCalledWith('v9')
  })

  it('shows the failure reason, refund reassurance, and a retry on ingest failure', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXHR)
    vi.stubGlobal('fetch', mockFetch(row('failed', { failure_reason: 'bad codec' })))
    renderFlow()
    await uploadFile()
    expect(await screen.findByText('bad codec')).toBeInTheDocument()
    expect(screen.getByText(/minutes are automatically refunded/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry analysis' })).toBeInTheDocument()
  })
})
