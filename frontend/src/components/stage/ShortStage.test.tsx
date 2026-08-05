/**
 * ShortStage (L26 Issues 424/425) — the shared stage. Migrates ClipPlayer's
 * render-state coverage (the hook + placeholder ladder now live here for both
 * pages) and pins the stage-specific contracts: the mediaKey remount, the
 * muted-autoplay pairing, and the single-player cleaned-preview tab swap.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ShortStage, type ShortStageProps } from './ShortStage'
import type { ReviewClip } from '@/types'

const BASE: ReviewClip = {
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

function stageUi(props: ShortStageProps, qc: QueryClient) {
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ShortStage {...props} />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

function renderStage(props: ShortStageProps) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(stageUi(props, qc))
}

afterEach(() => vi.unstubAllGlobals())

describe('ShortStage render states', () => {
  it('plays the video when the clip is rendered', () => {
    renderStage({ clip: BASE })
    expect(document.querySelector('video')).toBeInTheDocument()
    expect(screen.queryByText(/Render this clip/)).not.toBeInTheDocument()
  })

  it('autoplays muted with preload so Chrome does not block on a black frame (Issue 359d)', () => {
    renderStage({ clip: BASE, autoPlay: true })
    const video = document.querySelector('video')!
    expect(video).toHaveAttribute('autoplay')
    expect(video.muted).toBe(true)
    expect(video).toHaveAttribute('preload', 'auto')
  })

  it('remounts the video element when render_uri changes (confirmed trim/clean swap)', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { rerender } = render(stageUi({ clip: BASE }, qc))
    document.querySelector('video')!.setAttribute('data-marker', 'old-element')

    rerender(stageUi({ clip: BASE }, qc))
    expect(document.querySelector('video')!.getAttribute('data-marker')).toBe('old-element')

    rerender(stageUi({ clip: { ...BASE, render_uri: 'http://x/c1-trimmed.mp4' } }, qc))
    expect(document.querySelector('video')!.getAttribute('data-marker')).toBeNull()
  })

  it('shows a "Rendering…" status while a render is in flight (no manual button)', () => {
    renderStage({ clip: { ...BASE, render_status: 'running', render_uri: null } })
    expect(screen.getByText(/Rendering your clip/)).toBeInTheDocument()
    expect(screen.queryByText(/Render this clip/)).not.toBeInTheDocument()
  })

  it('offers a manual render button when pending, and POSTs on click', async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) => ({
        status: 202,
        ok: true,
        json: async () => ({}),
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    renderStage({ clip: { ...BASE, render_status: 'pending', render_uri: null } })

    await userEvent.click(screen.getByText(/Render this clip/))

    await waitFor(() => {
      const called = fetchMock.mock.calls.some(
        (c) => String(c[0]).endsWith('/clips/c1/render') && c[1]?.method === 'POST',
      )
      expect(called).toBe(true)
    })
  })

  it('does not latch the spinner after a render request settles (the render-loop bug)', async () => {
    const fetchMock = vi.fn(async () => ({ status: 202, ok: true, json: async () => ({}) }))
    vi.stubGlobal('fetch', fetchMock)
    // Server truth for this clip is terminal-failed (source gone); render_uri never lands.
    renderStage({ clip: { ...BASE, render_status: 'failed', render_uri: null } })

    await userEvent.click(screen.getByText(/Retry render/))

    await waitFor(() => {
      expect(screen.queryByText(/Rendering your clip/)).not.toBeInTheDocument()
    })
    expect(screen.getByText(/Render failed/)).toBeInTheDocument()
  })

  it('shows the source-expired card (not a spinner) on the structured 409', async () => {
    const fetchMock = vi.fn(async () => ({
      status: 409,
      ok: false,
      json: async () => ({
        detail: {
          code: 'source_expired',
          message:
            'Source media expired (72-hour retention) — re-upload the video to render this clip.',
        },
      }),
    }))
    vi.stubGlobal('fetch', fetchMock)
    renderStage({ clip: { ...BASE, render_status: 'pending', render_uri: null } })

    await userEvent.click(screen.getByText(/Render this clip/))

    expect(await screen.findByText('Source media expired')).toBeInTheDocument()
    const cta = screen.getByRole('link', { name: /Upload again/ })
    expect(cta).toHaveAttribute('href', '/dashboard')
    expect(screen.queryByText(/Rendering your clip/)).not.toBeInTheDocument()
    expect(document.body.textContent).not.toContain('[object Object]')
  })

  it('still treats a plain-string 409 as render-already-running (regression)', async () => {
    const fetchMock = vi.fn(async () => ({
      status: 409,
      ok: false,
      json: async () => ({ detail: 'Render already in progress' }),
    }))
    vi.stubGlobal('fetch', fetchMock)
    renderStage({ clip: { ...BASE, render_status: 'pending', render_uri: null } })

    await userEvent.click(screen.getByText(/Render this clip/))

    await waitFor(() => {
      expect(screen.queryByText(/Rendering your clip/)).not.toBeInTheDocument()
    })
    expect(screen.queryByText(/Render already in progress/)).not.toBeInTheDocument()
    expect(screen.queryByText('Source media expired')).not.toBeInTheDocument()
  })
})

describe('ShortStage meta + honesty', () => {
  it('shows the clip number, setup-origin duration and fit tier', () => {
    renderStage({ clip: BASE })
    // 20 - 2 = 18s — the setup origin, never end_s - start_s.
    expect(screen.getByText(/Clip #1 · 18\.0s/)).toBeInTheDocument()
    expect(screen.getByText(/Strong channel fit/)).toBeInTheDocument()
  })

  it('a creator selection gets honest provenance, never a fit tier (Issue 373)', () => {
    renderStage({
      clip: { ...BASE, origin: 'creator', rank: null, score: null, shortlisted: false },
    })
    expect(screen.getByText(/Your selection/)).toBeInTheDocument()
    expect(screen.getByText('Not engine-scored')).toBeInTheDocument()
    expect(screen.queryByText(/channel fit/i)).toBeNull()
  })
})

describe('ShortStage cleaned-preview tab swap (Issue 425)', () => {
  it('swaps ONE player to the edited preview when a cleaned render lands, and back', async () => {
    const onConfirm = vi.fn()
    const onDiscard = vi.fn()
    renderStage({
      clip: BASE,
      cleanedUri: 's3://bucket/cleaned.mp4',
      onConfirmCleaned: onConfirm,
      onDiscardCleaned: onDiscard,
    })

    // One player, not a second stacked one.
    expect(document.querySelectorAll('video')).toHaveLength(1)
    // The cleaned render just landed — the stage shows it by default…
    expect(document.querySelector('video')!.getAttribute('src')).toContain('variant=cleaned')
    expect(screen.getByRole('tab', { name: 'Edited preview' })).toHaveAttribute(
      'aria-selected',
      'true',
    )

    // …with the decision buttons wired through.
    await userEvent.click(screen.getByRole('button', { name: 'Use edited version' }))
    expect(onConfirm).toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Keep original' }))
    expect(onDiscard).toHaveBeenCalled()

    // Tabbing back plays the current render again.
    await userEvent.click(screen.getByRole('tab', { name: 'Current' }))
    expect(document.querySelector('video')!.getAttribute('src')).not.toContain('variant=cleaned')
    expect(document.querySelectorAll('video')).toHaveLength(1)
  })

  it('renders no tabs when there is no pending cleaned render', () => {
    renderStage({ clip: BASE })
    expect(screen.queryByRole('tab')).toBeNull()
  })
})
