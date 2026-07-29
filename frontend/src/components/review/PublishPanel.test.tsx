/**
 * PublishPanel + SchedulePublishDialog (Issue 196 frontend).
 *
 * 80/20: the schedule happy path (chip → tz-aware future POST body), the
 * two-step confirm, cancel gating by status, the Cancelled-vs-Failed badge
 * inference, the youtu.be link, the past-datetime rejection, and the
 * nextOccurrence week-rollover edge the backend 422s on.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PublishPanel } from './PublishPanel'
import { nextOccurrence } from '@/lib/schedule'
import type { PublicationListOut, PublicationOut, ReviewClip } from '@/types'

const CLIP: ReviewClip = {
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
  applied_title: null,
  applied_description: null,
}

const PRIVACY_NOTE =
  'Pre-audit: clips are uploaded as private. Open YouTube Studio to publish publicly when ready.'

function makePub(over: Partial<PublicationOut> = {}): PublicationOut {
  return {
    id: 'p1',
    clip_id: 'c1',
    creator_id: 'cr1',
    task_id: null,
    youtube_video_id: null,
    status: 'scheduled',
    error: null,
    scheduled_at: '2027-01-09T18:00:00+00:00',
    platform: 'youtube',
    confirmed_at: null,
    created_at: '2026-07-01T08:00:00Z',
    updated_at: '2026-07-01T08:00:00Z',
    privacy_note: PRIVACY_NOTE,
    ...over,
  }
}

function makeList(pubs: PublicationOut[]): PublicationListOut {
  return {
    publications: pubs,
    suggested_windows: [
      { day_of_week: 6, day_name: 'Saturday', hour: 10, activity_index: 0.91, label: 'Saturday 10:00 AM' },
      { day_of_week: 3, day_name: 'Wednesday', hour: 18, activity_index: 0.78, label: 'Wednesday 6:00 PM' },
      { day_of_week: 0, day_name: 'Sunday', hour: 16, activity_index: 0.66, label: 'Sunday 4:00 PM' },
    ],
    privacy_note: PRIVACY_NOTE,
    truncated: false,
  }
}

function pubsFetch(list: PublicationListOut) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const json = (body: unknown, status = 200) => ({
      status,
      ok: status < 400,
      json: async () => body,
    })
    if (init?.method === 'POST' && url.endsWith('/clips/c1/publications'))
      return json(makePub({ id: 'p-new' }), 201)
    if (init?.method === 'POST' && /\/publications\/[^/]+\/(confirm|cancel)$/.test(url))
      return json(makePub({ status: 'confirmed' }))
    if (url.endsWith('/clips/c1/publications')) return json(list)
    return json({})
  })
}

function renderPanel(clip: ReviewClip = CLIP) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <PublishPanel clip={clip} />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('nextOccurrence', () => {
  it('rolls a full week forward when the window is today but already past', () => {
    // Wednesday 2026-07-29 12:00 local; Wednesday(3) 10:00 already passed.
    const from = new Date(2026, 6, 29, 12, 0, 0)
    const next = nextOccurrence(3, 10, from)
    expect(next.getDay()).toBe(3)
    expect(next.getHours()).toBe(10)
    expect(next.getTime()).toBeGreaterThan(from.getTime())
    expect(next.getDate()).toBe(5) // Aug 5 — one week out, not today.
  })
})

describe('PublishPanel', () => {
  it('gates on render_uri: no schedule affordance for unrendered clips', () => {
    vi.stubGlobal('fetch', pubsFetch(makeList([])))
    renderPanel({ ...CLIP, render_uri: null })
    expect(screen.getByRole('button', { name: 'Schedule publish' })).toBeDisabled()
    expect(screen.getByText(/Render this clip to schedule a publish/)).toBeInTheDocument()
  })

  it('schedules from a suggested-window chip with a tz-aware future scheduled_at', async () => {
    const fetchMock = pubsFetch(makeList([]))
    vi.stubGlobal('fetch', fetchMock)
    renderPanel()

    await userEvent.click(screen.getByRole('button', { name: 'Schedule publish' }))
    // Quick-pick chips from the audience-activity windows + the verbatim note.
    expect(await screen.findByText('Saturday 10:00 AM')).toBeInTheDocument()
    expect(screen.getAllByText(PRIVACY_NOTE).length).toBeGreaterThan(0)

    await userEvent.click(screen.getByText('Saturday 10:00 AM'))
    await userEvent.click(screen.getByRole('button', { name: 'Schedule' }))

    await waitFor(() => {
      const post = fetchMock.mock.calls.find(
        ([u, init]) => String(u).endsWith('/clips/c1/publications') && init?.method === 'POST',
      )
      expect(post).toBeTruthy()
      const body = JSON.parse(String(post![1]!.body)) as { scheduled_at: string }
      // toISOString() → tz-aware UTC; the backend 422s naive datetimes.
      expect(body.scheduled_at.endsWith('Z')).toBe(true)
      const when = new Date(body.scheduled_at)
      expect(when.getTime()).toBeGreaterThan(Date.now())
      expect(when.getDay()).toBe(6) // Saturday in local time
      expect(when.getHours()).toBe(10)
    })
  })

  it('rejects a past typed datetime with a specific message and no POST', async () => {
    const fetchMock = pubsFetch(makeList([]))
    vi.stubGlobal('fetch', fetchMock)
    renderPanel()

    await userEvent.click(screen.getByRole('button', { name: 'Schedule publish' }))
    fireEvent.change(await screen.findByLabelText('Publish date and time'), {
      target: { value: '2020-01-01T10:00' },
    })
    await userEvent.click(screen.getByRole('button', { name: 'Schedule' }))

    expect(await screen.findByText(/That time is in the past/)).toBeInTheDocument()
    expect(
      fetchMock.mock.calls.some(
        ([u, init]) => String(u).endsWith('/clips/c1/publications') && init?.method === 'POST',
      ),
    ).toBe(false)
  })

  it('two-step: a scheduled row offers Confirm-to-lock-in which POSTs …/confirm', async () => {
    const fetchMock = pubsFetch(makeList([makePub()]))
    vi.stubGlobal('fetch', fetchMock)
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: 'Confirm to lock in' }))
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          ([u, init]) =>
            String(u).endsWith('/clips/c1/publications/p1/confirm') && init?.method === 'POST',
        ),
      ).toBe(true)
    })
  })

  it('offers Cancel only on mutable statuses; done links youtu.be; cancelled badge inferred', async () => {
    const list = makeList([
      makePub({ id: 'p1', status: 'confirmed', confirmed_at: '2026-07-02T08:00:00Z' }),
      makePub({ id: 'p2', status: 'done', youtube_video_id: 'yt12345abcd' }),
      makePub({ id: 'p3', status: 'failed', error: 'Cancelled by creator' }),
      makePub({ id: 'p4', status: 'failed', error: 'Upload quota exceeded' }),
    ])
    vi.stubGlobal('fetch', pubsFetch(list))
    renderPanel()

    // Only the confirmed row is cancellable (scheduled would be too).
    expect(await screen.findAllByRole('button', { name: 'Cancel' })).toHaveLength(1)
    // Done row links straight to the (private) upload.
    expect(screen.getByRole('link', { name: /Watch on YouTube/ })).toHaveAttribute(
      'href',
      'https://youtu.be/yt12345abcd',
    )
    // Cancelled is inferred from the backend's repurposed failed status.
    expect(screen.getByText('Cancelled')).toBeInTheDocument()
    expect(screen.getByText('Failed')).toBeInTheDocument()
    expect(screen.getByText('Upload quota exceeded')).toBeInTheDocument()
  })
})
