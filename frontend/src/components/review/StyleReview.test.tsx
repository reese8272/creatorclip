import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { StyleReview } from './StyleReview'

const PAST_NOTE = {
  id: 'f1',
  video_id: 'v9',
  sentiment: 'like',
  feedback_tags: ['tone_on_brand'],
  feedback_note: 'The dry humor lands.',
  created_at: '2026-07-29T12:00:00Z',
}

function mockFetch() {
  const json = (body: unknown, status = 200) => ({ status, ok: status < 300, json: async () => body })
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/videos/v9/feedback') && init?.method === 'POST')
      return json({ ...PAST_NOTE, id: 'f2' }, 201)
    if (url.endsWith('/videos/v9/feedback')) return json({ items: [PAST_NOTE] })
    return json({})
  })
}

function renderStyleReview() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <StyleReview videoId="v9" />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('StyleReview', () => {
  it('submits sentiment + tags + note and lists past notes', async () => {
    const fetchMock = mockFetch()
    vi.stubGlobal('fetch', fetchMock)
    renderStyleReview()

    expect(await screen.findByText('The dry humor lands.')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /Works for me/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Pacing feels right' }))
    await userEvent.type(
      screen.getByRole('textbox', { name: 'Style note' }),
      'Cuts are tight, keep this rhythm.',
    )
    await userEvent.click(screen.getByRole('button', { name: 'Save style note' }))

    await waitFor(() => {
      const post = fetchMock.mock.calls.find(
        ([u, init]) =>
          String(u).endsWith('/videos/v9/feedback') && (init as RequestInit)?.method === 'POST',
      )
      expect(post).toBeTruthy()
      const body = JSON.parse(String((post![1] as RequestInit).body))
      expect(body.sentiment).toBe('like')
      expect(body.feedback_tags).toEqual(['pacing_feels_right'])
      expect(body.feedback_note).toBe('Cuts are tight, keep this rhythm.')
    })
  })

  it('shows the dislike taxonomy for "Not my style"', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderStyleReview()
    await userEvent.click(await screen.findByRole('button', { name: /Not my style/ }))
    expect(screen.getByRole('button', { name: 'Pacing is off' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: "Editing style isn't mine" })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Pacing feels right' })).toBeNull()
  })

  it('requires substance — save stays disabled until a tag or note exists', async () => {
    vi.stubGlobal('fetch', mockFetch())
    renderStyleReview()
    await userEvent.click(await screen.findByRole('button', { name: /Works for me/ }))
    const save = screen.getByRole('button', { name: 'Save style note' })
    expect(save).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Strong intro' }))
    expect(save).toBeEnabled()
  })

  it('keeps the form usable when the source stream errors (retention honesty)', async () => {
    vi.stubGlobal('fetch', mockFetch())
    const { container } = renderStyleReview()
    const video = container.querySelector('video')!
    // Simulate a purged-source stream failure.
    video.dispatchEvent(new Event('error'))
    expect(await screen.findByText(/Source media isn’t available/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Works for me/ })).toBeInTheDocument()
  })
})
