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

// Issue 395: the upload transport lives in the app-wide uploader (covered by
// lib/uploader.test.ts + UploadVideoForm.test.tsx). Here we only need the
// form's success callback, so the hook is faked and `uploadFile()` fires it —
// this suite pins InlineUploadFlow's phase transitions, not the transport.
const uploaderHook = {
  onUploaded: undefined as ((videoId: string) => void) | undefined,
}

vi.mock('@/hooks/useUploader', () => ({
  useUploader: (cb?: (videoId: string) => void) => {
    uploaderHook.onUploaded = cb
    return {
      ready: true,
      items: [],
      sessionExpired: false,
      addFiles: vi.fn(() => null),
      start: vi.fn(),
      retryFile: vi.fn(),
      removeFile: vi.fn(),
      clearFinished: vi.fn(),
    }
  },
}))

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

function uploadFile() {
  act(() => uploaderHook.onUploaded?.('v9'))
}

afterEach(() => {
  vi.unstubAllGlobals()
  uploaderHook.onUploaded = undefined
})

describe('InlineUploadFlow', () => {
  it('moves from upload to the in-place processing card on success', async () => {
    vi.stubGlobal('fetch', mockFetch(row('running')))
    renderFlow()
    uploadFile()
    expect(await screen.findByText(/Fresh upload/)).toBeInTheDocument()
    expect(
      screen.getByText(/we’ll offer clip generation when analysis finishes/),
    ).toBeInTheDocument()
  })

  it('offers an explicit generate step when ingest is done — and never auto-fires it', async () => {
    const fetchMock = mockFetch(row('done'))
    vi.stubGlobal('fetch', fetchMock)
    const onReady = renderFlow()
    uploadFile()
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
    vi.stubGlobal('fetch', mockFetch(row('failed', { failure_reason: 'bad codec' })))
    renderFlow()
    uploadFile()
    expect(await screen.findByText('bad codec')).toBeInTheDocument()
    expect(screen.getByText(/minutes are automatically refunded/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry analysis' })).toBeInTheDocument()
  })
})
