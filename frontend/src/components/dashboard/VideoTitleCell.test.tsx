import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { VideoTitleCell } from './VideoTitleCell'
import type { Video } from '@/types'

const VIDEO = {
  id: 'v1', youtube_video_id: null, title: null, kind: 'video',
  ingest_status: 'done', failure_reason: null, duration_s: 300,
  created_at: '2026-08-05T00:00:00Z', origin: 'upload', clippable: true,
} as unknown as Video

function renderCell(video: Video = VIDEO) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidateSpy = vi.spyOn(qc, 'invalidateQueries')
  render(
    <QueryClientProvider client={qc}>
      <VideoTitleCell video={video} />
    </QueryClientProvider>,
  )
  return { invalidateSpy }
}

afterEach(() => vi.unstubAllGlobals())

describe('VideoTitleCell', () => {
  it('shows the Untitled fallback and renames via PATCH', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => ({
      status: 200, ok: true,
      json: async () => ({ ...VIDEO, title: JSON.parse(String(init?.body)).title }),
    }))
    vi.stubGlobal('fetch', fetchMock)
    const { invalidateSpy } = renderCell()

    expect(screen.getByText(/Untitled/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Rename/ }))
    await userEvent.type(screen.getByRole('textbox', { name: 'Video title' }), 'Podcast Ep 12')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() =>
      expect(screen.queryByRole('textbox', { name: 'Video title' })).not.toBeInTheDocument(),
    )
    const call = fetchMock.mock.calls.find(([u]) => String(u).endsWith('/videos/v1'))!
    expect(call[1]?.method).toBe('PATCH')
    expect(JSON.parse(String(call[1]?.body))).toEqual({ title: 'Podcast Ep 12' })
    const keys = invalidateSpy.mock.calls.map(([f]) => JSON.stringify(f?.queryKey))
    expect(keys).toContain(JSON.stringify(['videos']))
  })

  it('Escape cancels without a request; empty save clears the title', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => ({
      status: 200, ok: true, json: async () => VIDEO,
    }))
    vi.stubGlobal('fetch', fetchMock)
    renderCell({ ...VIDEO, title: 'Old name' } as Video)

    await userEvent.click(screen.getByRole('button', { name: /Rename/ }))
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('button', { name: /Rename/ }))
    await userEvent.clear(screen.getByRole('textbox', { name: 'Video title' }))
    await userEvent.keyboard('{Enter}')
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(JSON.parse(String(fetchMock.mock.calls[0]![1]?.body))).toEqual({ title: null })
  })

  it('surfaces a save failure and stays in edit mode', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      status: 500, ok: false, json: async () => ({ detail: 'boom' }),
    })))
    renderCell()

    await userEvent.click(screen.getByRole('button', { name: /Rename/ }))
    await userEvent.type(screen.getByRole('textbox', { name: 'Video title' }), 'x')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText(/Could not rename/)).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Video title' })).toBeInTheDocument()
  })
})
