import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Editor } from './Editor'
import { Review } from './Review'
import { ToolChrome } from '@/components/ToolChrome'
import { HONESTY_STATEMENT } from '@/components/layout/ToolStatusBar'

// Issue 389 acceptance test.
//
// The shell must hold the same three invariants on EVERY branch of both tool
// routes — including the loading, error and picker branches that each used to
// render their own copy of the disclaimer band and their own <main>. Before 389
// there were ten inline DisclaimerBand call sites across these two files; the
// only guarantee that none was missed was that somebody remembered. Now it is a
// structural property of ToolShell, and this table is what pins it.
;(globalThis as unknown as Record<string, unknown>).AudioContext = undefined
;(globalThis as unknown as Record<string, unknown>).webkitAudioContext = undefined

const CLIP = {
  id: 'c1',
  video_id: 'v1',
  setup_start_s: 2,
  start_s: 0,
  end_s: 20,
  peak_s: 10,
  score: 0.82,
  rank: 1,
  principle: 'Curiosity gap',
  reasoning: 'Strong hook.',
  render_status: 'done',
  render_uri: 'http://cdn/c1.mp4',
  cleaned_render_uri: null,
  applied_title: null,
  applied_description: null,
  origin: 'engine',
  aspect: '9:16',
  shortlisted: true,
}

const VIDEO = {
  id: 'v1',
  youtube_video_id: 'yt1',
  title: 'My stream VOD',
  kind: 'video',
  ingest_status: 'done',
  failure_reason: null,
  duration_s: 300,
  created_at: '2026-07-01T00:00:00Z',
  origin: 'upload',
  clippable: true,
}

const TRANSCRIPT = {
  clip_id: 'c1',
  clip_duration_s: 20,
  words: [{ word: 'Hello', start_s: 0, end_s: 1, index: 0 }],
}

/** `mode: 'ok'` serves every fixture; `mode: 'clipsError'` fails the clips list. */
function stubFetch(mode: 'ok' | 'clipsError' | 'noClips') {
  const json = (body: unknown) => ({ status: 200, ok: true, json: async () => body })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/auth/me')) return json({ channel_title: 'C' })
      if (url.endsWith('/videos')) return json({ videos: [VIDEO], state: 'populated' })
      if (url.endsWith('/videos/clips/counts'))
        return json({ counts: [{ video_id: 'v1', total: 1, rendered: 1 }] })
      if (url.includes('/videos/v1/clips')) {
        if (mode === 'clipsError') return { status: 500, ok: false, json: async () => ({}) }
        return json({ clips: mode === 'noClips' ? [] : [CLIP], personalization: null })
      }
      if (url.includes('/clips/c1/transcript')) return json(TRANSCRIPT)
      if (url.includes('/clips/c1/download')) return new Response(new ArrayBuffer(0), { status: 200 })
      return json({})
    }),
  )
}

// Both tool routes are registered, so the entry URL alone selects the branch.
function renderThroughShell(entry: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter basename="/app" initialEntries={[entry]}>
        <Routes>
          <Route element={<ToolChrome />}>
            <Route path="editor" element={<Editor />} />
            <Route path="review" element={<Review />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

interface Branch {
  name: string
  entry: string
  mode: 'ok' | 'clipsError' | 'noClips'
  /** Text proving we actually landed on the intended branch, not a sibling. */
  settles: RegExp
}

const BRANCHES: Branch[] = [
  {
    name: 'editor · no video (picker landing)',
    entry: '/app/editor',
    mode: 'ok',
    settles: /Pick a processed video to work its clips/i,
  },
  {
    name: 'editor · short-form workspace',
    entry: '/app/editor?video_id=v1&clip_id=c1',
    mode: 'ok',
    settles: /Clip #1/i,
  },
  {
    name: 'editor · long-form source mode',
    entry: '/app/editor?video_id=v1',
    mode: 'ok',
    settles: /Suggested clips/i,
  },
  {
    name: 'editor · clips-load failure',
    entry: '/app/editor?video_id=v1&clip_id=c1',
    mode: 'clipsError',
    settles: /Couldn’t load clips for this video/i,
  },
  {
    name: 'review · no video (picker landing)',
    entry: '/app/review',
    mode: 'ok',
    settles: /Pick a processed video to review its clips/i,
  },
  {
    name: 'review · clip loaded',
    entry: '/app/review?video_id=v1',
    mode: 'ok',
    settles: /Clip #1/,
  },
  {
    name: 'review · style mode',
    entry: '/app/review?video_id=v1&mode=style',
    mode: 'ok',
    settles: /Your take on this style/i,
  },
  {
    name: 'review · clips-load failure',
    entry: '/app/review?video_id=v1',
    mode: 'clipsError',
    settles: /Couldn’t load clips for this video/i,
  },
  {
    name: 'review · zero clips',
    entry: '/app/review?video_id=v1',
    mode: 'noClips',
    settles: /No clips yet for this video/i,
  },
]

afterEach(() => vi.unstubAllGlobals())

describe('tool routes render inside the app shell', () => {
  it.each(BRANCHES)('$name', async ({ entry, mode, settles }) => {
    stubFetch(mode)
    renderThroughShell(entry)
    expect(await screen.findByText(settles)).toBeInTheDocument()

    // 1. The honesty statement, verbatim, exactly once. CLAUDE.md hard constraint —
    //    a branch that forgets it is a compliance failure, and a branch that shows
    //    it twice means a page kept its old inline band.
    expect(screen.getAllByText(HONESTY_STATEMENT)).toHaveLength(1)

    // 2. Exactly one <main> — axe landmark-one-main / landmark-unique gate both
    //    of these routes at serious impact.
    expect(screen.getAllByRole('main')).toHaveLength(1)

    // 3. The legal links stay reachable, exactly once. Twice would mean the
    //    marketing footer came back; zero would mean the tool routes dropped a
    //    Google-OAuth-verification requirement along with it.
    expect(screen.getAllByRole('link', { name: 'Terms' })).toHaveLength(1)
    expect(screen.getAllByRole('link', { name: 'Accessibility' })).toHaveLength(1)
  })

  it('never renders the old inline disclaimer band alongside the status bar', async () => {
    stubFetch('ok')
    renderThroughShell('/app/editor?video_id=v1&clip_id=c1')
    await screen.findByText(/Clip #1/i)
    await waitFor(() => {
      const hits = screen.getAllByText(/does not promise virality/i)
      expect(hits).toHaveLength(1)
    })
  })
})
