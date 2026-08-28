/**
 * ClipPiles — Keep-pile finish line (Issue 447).
 *
 * 80/20: the rendered → downloaded → published chip states (done vs todo, the
 * scheduled narration), the authed download link's presence/absence, the
 * dropped pile staying lean, and the Publish toggle revealing the reused
 * PublishPanel. Copy is workflow-state only — a structural check pins the
 * no-virality constraint on this new surface.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PileList, PileTabs } from './ClipPiles'
import type { Pile } from '@/lib/clipPiles'
import type { ReviewClip } from '@/types'

function makeClip(over: Partial<ReviewClip> = {}): ReviewClip {
  return {
    id: 'c1',
    video_id: 'v1',
    setup_start_s: 2,
    start_s: 0,
    end_s: 20,
    peak_s: 10,
    score: 0.9,
    rank: 1,
    principle: 'Curiosity gap',
    reasoning: 'Strong hook.',
    render_status: 'done',
    render_uri: 'http://x/c1.mp4',
    cleaned_render_uri: null,
    applied_title: 'My clip',
    applied_description: null,
    origin: 'engine' as const,
    aspect: '9:16',
    shortlisted: true,
    triage: 'kept',
    downloaded_at: null,
    latest_publication: null,
    ...over,
  }
}

function renderPile(clips: ReviewClip[], pile: 'kept' | 'dropped' = 'kept') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <PileList pile={pile} clips={clips} videoId="v1" onGoToReview={() => {}} />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('Keep-pile finish line (Issue 447)', () => {
  it('marks all three steps done for a rendered, downloaded, published clip', () => {
    renderPile([
      makeClip({
        downloaded_at: '2026-08-10T12:00:00Z',
        latest_publication: {
          status: 'done',
          scheduled_at: null,
          youtube_video_id: 'yt-abc',
        },
      }),
    ])
    expect(screen.getByText('Rendered')).toHaveAttribute('data-state', 'done')
    expect(screen.getByText('Downloaded')).toHaveAttribute('data-state', 'done')
    expect(screen.getByText('Published')).toHaveAttribute('data-state', 'done')
  })

  it('leaves undone steps as todo when nothing has happened yet', () => {
    renderPile([makeClip()])
    expect(screen.getByText('Rendered')).toHaveAttribute('data-state', 'done')
    expect(screen.getByText('Downloaded')).toHaveAttribute('data-state', 'todo')
    expect(screen.getByText('Published')).toHaveAttribute('data-state', 'todo')
  })

  it('narrates a scheduled publication without claiming it happened', () => {
    renderPile([
      makeClip({
        latest_publication: {
          status: 'scheduled',
          scheduled_at: '2027-01-09T18:00:00Z',
          youtube_video_id: null,
        },
      }),
    ])
    expect(screen.getByText('Scheduled')).toHaveAttribute('data-state', 'todo')
    expect(screen.queryByText('Published')).not.toBeInTheDocument()
  })

  it('renders the authed download link for a rendered kept clip', () => {
    renderPile([makeClip()])
    const link = screen.getByRole('link', { name: /Download/ })
    expect(link).toHaveAttribute('href', '/clips/c1/download')
    expect(link).toHaveAttribute('download')
  })

  it('omits the download link when the clip has no render', () => {
    renderPile([makeClip({ render_status: 'pending', render_uri: null })])
    expect(screen.queryByRole('link', { name: /Download/ })).not.toBeInTheDocument()
  })

  it('keeps the dropped pile lean: no chips, no download, no publish', () => {
    renderPile([makeClip({ triage: 'dropped' })], 'dropped')
    expect(screen.queryByTestId('finish-line')).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Download/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Publish' })).not.toBeInTheDocument()
  })

  it('Publish toggle reveals the reused PublishPanel with its schedule entry', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        status: 200,
        ok: true,
        json: async () => ({
          publications: [],
          suggested_windows: [],
          privacy_note: undefined,
          truncated: false,
        }),
      })),
    )
    renderPile([makeClip()])
    await userEvent.click(screen.getByRole('button', { name: 'Publish' }))
    expect(await screen.findByRole('button', { name: 'Schedule publish' })).toBeInTheDocument()
  })

  // The 422 that shipped: this call once sent {state: …} against a backend
  // whose TriageIn declares `triage` with extra="forbid", so every Un-keep and
  // Restore click failed loudly. These two tests CLICK the buttons and pin the
  // exact request body — presence-only assertions are how it slipped through.
  it('Un-keep PUTs {triage: "pending"} — the exact wire contract', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => ({
      status: 200,
      ok: true,
      json: async () => ({}),
    }))
    vi.stubGlobal('fetch', fetchMock)
    renderPile([makeClip()])

    await userEvent.click(screen.getByRole('button', { name: 'Un-keep' }))

    await waitFor(() => {
      const put = fetchMock.mock.calls.find(
        ([u, init]) =>
          String(u).endsWith('/clips/c1/triage') &&
          (init as RequestInit | undefined)?.method === 'PUT',
      )
      expect(put).toBeTruthy()
      expect(JSON.parse(String((put![1] as RequestInit).body))).toEqual({ triage: 'pending' })
    })
  })

  it('Restore PUTs {triage: "pending"} from the dropped pile', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => ({
      status: 200,
      ok: true,
      json: async () => ({}),
    }))
    vi.stubGlobal('fetch', fetchMock)
    renderPile([makeClip({ triage: 'dropped' })], 'dropped')

    await userEvent.click(screen.getByRole('button', { name: 'Restore' }))

    await waitFor(() => {
      const put = fetchMock.mock.calls.find(
        ([u, init]) =>
          String(u).endsWith('/clips/c1/triage') &&
          (init as RequestInit | undefined)?.method === 'PUT',
      )
      expect(put).toBeTruthy()
      expect(JSON.parse(String((put![1] as RequestInit).body))).toEqual({ triage: 'pending' })
    })
  })

  it('surfaces the server message when the move fails — honest failure (Issue 437)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        status: 422,
        ok: false,
        json: async () => ({ detail: { code: 'validation', message: 'Unknown triage state.' } }),
      })),
    )
    renderPile([makeClip()])

    await userEvent.click(screen.getByRole('button', { name: 'Un-keep' }))

    expect(await screen.findByRole('status')).toHaveTextContent(/Unknown triage state/)
  })

  it('structural: pile copy stays workflow-neutral — no virality language', () => {
    const { container } = renderPile([
      makeClip({
        downloaded_at: '2026-08-10T12:00:00Z',
        latest_publication: {
          status: 'done',
          scheduled_at: null,
          youtube_video_id: 'yt-abc',
        },
      }),
    ])
    const text = (container.textContent ?? '').toLowerCase()
    for (const term of ['viral', 'guarantee', 'promise', 'reach', 'views']) {
      expect(text).not.toContain(term)
    }
  })
})

// Issue 445 AC6 — the tablist is keyboard-operable: one roving tab stop,
// ←/→/Home/End move between piles, selection follows focus (WAI-ARIA tabs).
describe('PileTabs keyboard navigation (Issue 445 AC6)', () => {
  function renderTabs(active: Pile = 'pending') {
    const onSelect = vi.fn()
    render(
      <PileTabs
        counts={{ pending: 3, kept: 2, dropped: 1 }}
        active={active}
        onSelect={onSelect}
      />,
    )
    return onSelect
  }

  it('only the active tab is in the tab order', () => {
    renderTabs('kept')
    const tabs = screen.getAllByRole('tab')
    for (const tab of tabs) {
      const selected = tab.getAttribute('aria-selected') === 'true'
      expect(tab).toHaveAttribute('tabindex', selected ? '0' : '-1')
    }
  })

  it('ArrowRight selects the next pile; ArrowLeft wraps to the last', async () => {
    const onSelect = renderTabs('pending')
    screen.getByRole('tab', { selected: true }).focus()

    await userEvent.keyboard('{ArrowRight}')
    expect(onSelect).toHaveBeenLastCalledWith('kept')

    await userEvent.keyboard('{ArrowLeft}')
    expect(onSelect).toHaveBeenLastCalledWith('dropped')
  })

  it('Home and End jump to the first and last pile', async () => {
    const onSelect = renderTabs('kept')
    screen.getByRole('tab', { selected: true }).focus()

    await userEvent.keyboard('{End}')
    expect(onSelect).toHaveBeenLastCalledWith('dropped')

    await userEvent.keyboard('{Home}')
    expect(onSelect).toHaveBeenLastCalledWith('pending')
  })
})
