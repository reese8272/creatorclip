/**
 * YourCall — two contracts.
 *
 * 1. Trim → re-render (Wave-1 trim-rerender contract). 80/20: the POST body
 *    (clip-relative seconds), the review-then-confirm affordance riding
 *    cleaned_render_uri, and the two load-bearing error edges (409
 *    pending_clean_or_edit surfaces the pending preview; 422 messages verbatim).
 * 2. The keep/drop write's FAILURE path (Issue 437) — see that describe block.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { YourCall } from './YourCall'
import type { ReviewClip } from '@/types'

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
  origin: 'engine' as const,
  aspect: '9:16',
  shortlisted: true,
}

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

function renderYourCall(
  clip: ReviewClip = CLIP,
  onAdvance: () => void = () => {},
  onVerdict: (action: 'upvote' | 'downvote') => void = () => {},
) {
  return render(
    <YourCall
      clip={clip}
      trimStart={2}
      trimEnd={18}
      onAdvance={onAdvance}
      onVerdict={onVerdict}
    />,
    { wrapper },
  )
}

// Fetch mock: trim-render 202, clip-list poll returns the cleaned URI, confirm 200.
function trimRenderFetch(
  trimResponse: { status: number; body: unknown } = {
    status: 202,
    body: { task_id: 't1', status: 'queued', stream_url: '/tasks/c1/events' },
  },
) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const json = (body: unknown, status = 200) => ({
      status,
      ok: status < 400,
      json: async () => body,
    })
    if (init?.method === 'POST' && url.endsWith('/clips/c1/trim-render'))
      return json(trimResponse.body, trimResponse.status)
    if (url.endsWith('/videos/v1/clips'))
      return json({ clips: [{ ...CLIP, cleaned_render_uri: '/media/c1-trimmed.mp4' }] })
    if (init?.method === 'POST' && url.endsWith('/clips/c1/clean/confirm')) return json({})
    if (init?.method === 'POST' && url.endsWith('/clips/c1/clean/discard'))
      return json({ clip_id: 'c1', status: 'discarded' })
    return json({})
  })
}

afterEach(() => vi.unstubAllGlobals())

describe('YourCall — apply trim & re-render', () => {
  it('POSTs the clip-relative trim window to /trim-render', async () => {
    const fetchMock = trimRenderFetch()
    vi.stubGlobal('fetch', fetchMock)
    renderYourCall()

    await userEvent.click(screen.getByRole('button', { name: /Apply trim & re-render/ }))

    await waitFor(() => {
      const post = fetchMock.mock.calls.find(
        ([u, init]) => String(u).endsWith('/clips/c1/trim-render') && init?.method === 'POST',
      )
      expect(post).toBeTruthy()
      expect(JSON.parse(String(post![1]!.body))).toEqual({ trim_start_s: 2, trim_end_s: 18 })
    })
  })

  it('shows the confirm affordance once the poll returns cleaned_render_uri, and confirms', async () => {
    const fetchMock = trimRenderFetch()
    vi.stubGlobal('fetch', fetchMock)
    renderYourCall()

    await userEvent.click(screen.getByRole('button', { name: /Apply trim & re-render/ }))

    // Poll lands the cleaned URI → review-then-confirm appears.
    const confirmBtn = await screen.findByRole('button', { name: 'Use cleaned version' })
    expect(screen.getByRole('button', { name: 'Keep original' })).toBeInTheDocument()

    await userEvent.click(confirmBtn)
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          ([u, init]) =>
            String(u).endsWith('/clips/c1/clean/confirm') && init?.method === 'POST',
        ),
      ).toBe(true)
    })
    expect(await screen.findByText(/Trimmed version is now the main render/)).toBeInTheDocument()
  })

  it('"Keep original" POSTs /clean/discard so a follow-up trim/clean does not 409 (Issue 364)', async () => {
    const fetchMock = trimRenderFetch()
    vi.stubGlobal('fetch', fetchMock)
    renderYourCall()

    await userEvent.click(screen.getByRole('button', { name: /Apply trim & re-render/ }))

    const keepBtn = await screen.findByRole('button', { name: 'Keep original' })
    await userEvent.click(keepBtn)

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          ([u, init]) =>
            String(u).endsWith('/clips/c1/clean/discard') && init?.method === 'POST',
        ),
      ).toBe(true)
    })
    expect(await screen.findByText(/Keeping original render/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Use cleaned version' })).not.toBeInTheDocument()
  })

  it('surfaces the 409 pending_clean_or_edit message and the pending preview', async () => {
    const fetchMock = trimRenderFetch({
      status: 409,
      body: {
        detail: {
          code: 'pending_clean_or_edit',
          message: 'Confirm or discard the pending cleaned/edited version first.',
        },
      },
    })
    vi.stubGlobal('fetch', fetchMock)
    renderYourCall()

    await userEvent.click(screen.getByRole('button', { name: /Apply trim & re-render/ }))

    expect(
      await screen.findByText('Confirm or discard the pending cleaned/edited version first.'),
    ).toBeInTheDocument()
    // The blocking artifact's preview is surfaced so the creator can decide.
    expect(await screen.findByRole('button', { name: 'Use cleaned version' })).toBeInTheDocument()
  })

  it('shows 422 validation messages verbatim (limits embedded server-side)', async () => {
    vi.stubGlobal(
      'fetch',
      trimRenderFetch({
        status: 422,
        body: {
          detail: { code: 'trim_noop', message: 'Trim covers the full clip — nothing to remove.' },
        },
      }),
    )
    renderYourCall()

    await userEvent.click(screen.getByRole('button', { name: /Apply trim & re-render/ }))
    expect(
      await screen.findByText('Trim covers the full clip — nothing to remove.'),
    ).toBeInTheDocument()
  })

  it('disables the re-render button until the clip has a render', () => {
    vi.stubGlobal('fetch', trimRenderFetch())
    renderYourCall({ ...CLIP, render_uri: null })
    expect(screen.getByRole('button', { name: /Apply trim & re-render/ })).toBeDisabled()
  })
})

// ── Issue 437 — the keep/drop write must not fail silently ────────────────────
//
// Found live: during a 502 the owner's Keep/Drop appeared to do nothing. The
// write is a plain POST with no local buffer, so an unreachable origin losing
// the rating is expected — presenting that loss as a success is not. These are
// the only guard on this class of bug: `design-tokens.contract.test.ts` flags
// UNDECLARED token names, so a text-success/text-danger swap resolves cleanly
// and passes it.

function postsTo(fetchMock: ReturnType<typeof vi.fn>, path: string) {
  return fetchMock.mock.calls.filter(
    ([u, init]) => String(u).endsWith(path) && (init as RequestInit | undefined)?.method === 'POST',
  )
}

// A 502 from the Cloudflare edge returns an HTML error page, so `resp.json()`
// REJECTS — that rejection path is what the incident actually exercised.
function feedbackFetch(status = 201) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (init?.method === 'POST' && url.endsWith('/clips/c1/feedback')) {
      return {
        status,
        ok: status < 400,
        json: async () => {
          if (status >= 400) throw new SyntaxError('Unexpected token < in JSON at position 0')
          return { id: 'f1', action: 'upvote' }
        },
      }
    }
    return { status: 200, ok: true, json: async () => ({}) }
  })
}

describe('YourCall — keep/drop write failures (Issue 437)', () => {
  it('a failed Keep reads as an error and does not advance', async () => {
    const fetchMock = feedbackFetch(502)
    vi.stubGlobal('fetch', fetchMock)
    const onAdvance = vi.fn()
    const onVerdict = vi.fn()
    renderYourCall(CLIP, onAdvance, onVerdict)

    await userEvent.click(screen.getByRole('button', { name: 'Keep' }))

    // Says plainly that nothing was persisted, in the danger tone — not green.
    const status = await screen.findByRole('status')
    await waitFor(() => expect(status).toHaveTextContent(/nothing was saved/i))
    expect(status.className).toContain('text-danger')
    expect(status.className).not.toContain('text-success')

    // The clip stays put — one more click retries the same verdict.
    expect(screen.getByRole('button', { name: 'Keep' })).toBeEnabled()
    expect(onVerdict).not.toHaveBeenCalled()
    expect(onAdvance).not.toHaveBeenCalled()
  })

  it('a Keep commits on the FIRST click — no tag panel in the way (Issue 445)', async () => {
    const fetchMock = feedbackFetch(201)
    vi.stubGlobal('fetch', fetchMock)
    const onAdvance = vi.fn()
    const onVerdict = vi.fn()
    renderYourCall(CLIP, onAdvance, onVerdict)

    await userEvent.click(screen.getByRole('button', { name: 'Keep' }))

    // One POST, straight away, no Submit ritual — and the verdict callback
    // (which the page uses to advance + offer post-hoc tags) fires once.
    await waitFor(() => expect(onVerdict).toHaveBeenCalledWith('upvote'))
    expect(onVerdict).toHaveBeenCalledTimes(1)
    const posts = postsTo(fetchMock, '/clips/c1/feedback')
    expect(posts).toHaveLength(1)
    expect(JSON.parse(String(posts[0][1]!.body))).toEqual({ action: 'upvote' })
    expect(screen.queryByRole('button', { name: 'Submit' })).not.toBeInTheDocument()
  })

  it('locks Keep/Drop while the write is in flight so a double-click writes one row', async () => {
    let release: () => void = () => {}
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (init?.method === 'POST' && url.endsWith('/clips/c1/feedback')) {
        await new Promise<void>((resolve) => {
          release = resolve
        })
        return { status: 201, ok: true, json: async () => ({ id: 'f1', action: 'upvote' }) }
      }
      return { status: 200, ok: true, json: async () => ({}) }
    })
    vi.stubGlobal('fetch', fetchMock)
    renderYourCall()

    const keep = screen.getByRole('button', { name: 'Keep' })
    await userEvent.click(keep)
    expect(keep).toBeDisabled()
    await userEvent.click(keep)

    expect(postsTo(fetchMock, '/clips/c1/feedback')).toHaveLength(1)
    release()
  })
})

// Issue 445 — K/X are keyboard equivalents of Keep/Drop, routed through the
// shared shortcut bus (which already refuses keystrokes aimed at inputs and
// open modals).
describe('YourCall — K/X keyboard shortcuts (Issue 445)', () => {
  it('K commits a keep from the keyboard', async () => {
    const fetchMock = feedbackFetch(201)
    vi.stubGlobal('fetch', fetchMock)
    const onVerdict = vi.fn()
    renderYourCall(CLIP, () => {}, onVerdict)

    await userEvent.keyboard('k')

    await waitFor(() => expect(onVerdict).toHaveBeenCalledWith('upvote'))
    expect(postsTo(fetchMock, '/clips/c1/feedback')).toHaveLength(1)
  })

  it('X commits a drop from the keyboard', async () => {
    const fetchMock = feedbackFetch(201)
    vi.stubGlobal('fetch', fetchMock)
    const onVerdict = vi.fn()
    renderYourCall(CLIP, () => {}, onVerdict)

    await userEvent.keyboard('x')

    await waitFor(() => expect(onVerdict).toHaveBeenCalledWith('downvote'))
    const posts = postsTo(fetchMock, '/clips/c1/feedback')
    expect(posts).toHaveLength(1)
    expect(JSON.parse(String(posts[0][1]!.body))).toEqual({ action: 'downvote' })
  })
})

// Issue 472 — Skip is queue navigation, not feedback. The old handler POSTed
// {action:'skip'}, which the server treated as a RETRACTION in the training
// partition: Trim → Skip silently erased the trim label while the pile stayed
// kept. Skip must advance the queue and touch the network not at all.
describe('YourCall — skip is pure navigation (Issue 472)', () => {
  it('advances the queue without POSTing feedback', async () => {
    const fetchMock = vi.fn(async () => ({ status: 201, ok: true, json: async () => ({}) }))
    vi.stubGlobal('fetch', fetchMock)
    const onAdvance = vi.fn()
    renderYourCall(CLIP, onAdvance)

    await userEvent.click(screen.getByRole('button', { name: 'Skip' }))

    expect(onAdvance).toHaveBeenCalledTimes(1)
    expect(postsTo(fetchMock, '/clips/c1/feedback')).toHaveLength(0)
    expect(fetchMock).not.toHaveBeenCalled()
  })
})

// Issue 451 — the re-render affordance for an ALREADY-rendered clip.
//
// The trigger used to live only inside StagePlaceholder, which the stage swaps out the
// moment `render_uri` lands. So a `done` clip had no path back to the render pipeline,
// and every render-pipeline fix could only reach clips rendered after the deploy.
describe('YourCall — re-render a rendered clip (Issue 451)', () => {
  it('exposes the trigger on a clip that has already rendered, and POSTs the render', async () => {
    const fetchMock = vi.fn(async () => ({ status: 202, ok: true, json: async () => ({}) }))
    vi.stubGlobal('fetch', fetchMock)
    renderYourCall() // CLIP has render_uri set

    await userEvent.click(screen.getByRole('button', { name: /Re-render this clip/ }))

    await waitFor(() => expect(postsTo(fetchMock, '/clips/c1/render')).toHaveLength(1))
  })

  it('offers nothing to click on a clip that has never rendered', () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ status: 200, ok: true, json: async () => ({}) })))
    // StagePlaceholder already owns the never-rendered case; a second trigger here would
    // be a duplicate affordance, not a fix.
    renderYourCall({ ...CLIP, render_status: 'pending', render_uri: null })

    expect(screen.queryByRole('button', { name: /Re-render this clip/ })).toBeNull()
  })

  it('explains a purged source instead of offering a button that 409s', async () => {
    const fetchMock = vi.fn(async () => ({
      status: 409,
      ok: false,
      json: async () => ({ detail: { code: 'source_expired', message: 'Source media expired.' } }),
    }))
    vi.stubGlobal('fetch', fetchMock)
    renderYourCall()

    await userEvent.click(screen.getByRole('button', { name: /Re-render this clip/ }))

    // The dedicated explanation replaces the control — a retry can never succeed.
    expect(await screen.findByText(/purged under our retention window/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Re-render this clip/ })).toBeNull()
  })

  it('does not leave the player looking permanently broken while the re-render runs', () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ status: 200, ok: true, json: async () => ({}) })))
    // Server truth: the worker has picked it up. The control says so rather than going quiet.
    renderYourCall({ ...CLIP, render_status: 'running' })

    const button = screen.getByRole('button', { name: /Re-rendering…/ })
    expect(button).toBeDisabled()
    expect(screen.getByText(/The player comes back when it lands/)).toBeInTheDocument()
  })
})
