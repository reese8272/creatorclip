import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { YouTubeConnectionCard } from './YouTubeConnectionCard'

// Regression guard for the weekly-reconnect dead end.
//
// worker/tasks.py emits a `reauth_required` in-app notification titled
// "Reconnect your YouTube account" and notify/copy.py sends the matching email —
// both linking to /app/profile. That page had NO reconnect control (read-only
// since Issue 308), and Settings only offers PublishingSection, which is the
// separate youtube.upload grant. Because Google expires Testing-mode refresh
// tokens after 7 days, this dead end was the most frequently hit recovery path
// in the beta.

const json = (body: unknown) => ({ status: 200, ok: true, json: async () => body })

function mockMe(me: Record<string, unknown>) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/auth/me')) return json(me)
    return json({})
  })
}

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter basename="/app" initialEntries={['/app/profile']}>
        <YouTubeConnectionCard />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('YouTubeConnectionCard', () => {
  it('always offers a working reconnect affordance pointing at /auth/login', async () => {
    vi.stubGlobal('fetch', mockMe({ channel_title: 'C', youtube_connected: true }))
    renderCard()

    const link = await screen.findByRole('link', { name: /reconnect youtube/i })
    // A real server route, not a react-router path: /auth/login 302s to Google.
    expect(link).toHaveAttribute('href', '/auth/login')
  })

  it('shows a disconnected state when the token row is gone', async () => {
    // The refresh tick DELETEs the YoutubeToken row once Google rejects the
    // grant, so a missing row is exactly the revoked/expired state.
    vi.stubGlobal('fetch', mockMe({ channel_title: 'C', youtube_connected: false }))
    renderCard()

    expect(await screen.findByText('Disconnected')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /connect youtube/i })).toBeInTheDocument()
    expect(screen.getByText(/paused until you reconnect/i)).toBeInTheDocument()
  })

  it('warns before the 7-day Testing-mode token actually expires', async () => {
    const inOneDay = new Date(Date.now() + 86_400_000).toISOString()
    vi.stubGlobal(
      'fetch',
      mockMe({ channel_title: 'C', youtube_connected: true, youtube_expires_at: inOneDay }),
    )
    renderCard()

    expect(await screen.findByText('Expiring')).toBeInTheDocument()
    expect(screen.getByText(/expires in 1 day/i)).toBeInTheDocument()
  })

  it('explains the weekly cadence while the connection is healthy', async () => {
    const inSixDays = new Date(Date.now() + 6 * 86_400_000).toISOString()
    vi.stubGlobal(
      'fetch',
      mockMe({ channel_title: 'C', youtube_connected: true, youtube_expires_at: inSixDays }),
    )
    renderCard()

    // Exact match: /connected/i also matches the "Disconnected" badge.
    expect(await screen.findByText('Connected')).toBeInTheDocument()
    // Setting expectations up front is the point: an unexplained weekly prompt
    // reads as breakage rather than as a known review-status limitation.
    expect(screen.getByText(/every 7 days/i)).toBeInTheDocument()
  })
})
