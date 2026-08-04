import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Timeline } from './Timeline'
import { stubRect, withMeasuredRender } from '@/test/rect'
import type { EditorCut, TranscriptWord } from '@/types'

const WIDTH = 400
const DURATION = 60

const WORDS: TranscriptWord[] = [
  { word: 'Hello', start_s: 0, end_s: 1, index: 0 },
  { word: 'world', start_s: 10, end_s: 12, index: 1 },
]

function cut(over: Partial<EditorCut> = {}): EditorCut {
  return { id: 'c1', start_s: 10, end_s: 20, ...over }
}

function renderTimeline(props: Partial<React.ComponentProps<typeof Timeline>> = {}) {
  const onSeek = vi.fn()
  const onSelection = vi.fn()
  const onCutsChange = vi.fn()
  const utils = withMeasuredRender(WIDTH, 80, () =>
    render(
      <Timeline
        duration={DURATION}
        currentTime={0}
        cuts={[]}
        onSeek={onSeek}
        onSelection={onSelection}
        onCutsChange={onCutsChange}
        {...props}
      />,
    ),
  )
  // Narrow to the rail alone, so it can have a different box from anything else
  // on the page — which a prototype-wide stub cannot express.
  const rail = screen.getByRole('group', { name: 'Clip timeline' })
  stubRect(rail, WIDTH, 80)
  return { ...utils, rail, onSeek, onSelection, onCutsChange }
}

/** A pointer gesture. `fireEvent.mouseDown` does NOT trigger onPointerDown. */
function drag(rail: HTMLElement, fromX: number, toX: number) {
  fireEvent.pointerDown(rail, { clientX: fromX, button: 0, pointerId: 1 })
  fireEvent.pointerMove(rail, { clientX: toX, pointerId: 1 })
  fireEvent.pointerUp(rail, { clientX: toX, pointerId: 1 })
}

afterEach(() => vi.restoreAllMocks())

describe('Timeline', () => {
  it('exposes the playhead as the slider — not the container', () => {
    // The container used to claim role="slider", which forces every descendant
    // to `presentation` (MDN) and was silently swallowing the waveform's
    // role="img" and the per-cut labels. The name moves to the thing that is
    // genuinely a slider, so this query keeps working and now means something.
    renderTimeline()
    const playhead = screen.getByRole('slider', { name: /timeline scrubber/i })
    expect(playhead).toHaveAttribute('aria-valuemax', String(DURATION))
    expect(screen.getByRole('group', { name: 'Clip timeline' })).toBeInTheDocument()
  })

  it('renders an adaptive ruler whose labels are human-round', () => {
    // 400px / 60s = 6.67 px/s → a 15s interval.
    renderTimeline()
    for (const label of ['0:00', '0:30', '1:00']) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
  })

  it('seeks on a click that never becomes a drag', () => {
    const { rail, onSeek, onSelection } = renderTimeline({ duration: 40 })
    fireEvent.pointerDown(rail, { clientX: 200, button: 0, pointerId: 1 })
    fireEvent.pointerUp(rail, { clientX: 200, pointerId: 1 })
    expect(onSeek).toHaveBeenCalledTimes(1)
    expect(onSeek.mock.calls[0][0]).toBeCloseTo(20, 1)
    expect(onSelection).not.toHaveBeenCalled()
  })

  it('selects a range on a drag across empty space, and does not also seek', () => {
    const { rail, onSeek, onSelection } = renderTimeline({ duration: 40 })
    drag(rail, 0, 100)
    expect(onSelection).toHaveBeenCalledTimes(1)
    const { start_s, end_s } = onSelection.mock.calls[0][0]
    expect(start_s).toBeCloseTo(0, 1)
    expect(end_s).toBeCloseTo(10, 1)
    expect(onSeek).not.toHaveBeenCalled()
  })

  it('labels every cut, and gives each edge its own focusable thumb', () => {
    renderTimeline({
      cuts: [cut({ id: 'a', start_s: 10, end_s: 20 }), cut({ id: 'b', start_s: 40, end_s: 50 })],
    })
    expect(screen.getByLabelText(/Cut 1:/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/Cut 2:/i)).toBeInTheDocument()

    // Multi-thumb (W3C APG): one slider per focusable thumb, plus the playhead.
    expect(screen.getAllByRole('slider')).toHaveLength(5)
    expect(screen.getByRole('slider', { name: 'Cut 1 start' })).toHaveAttribute('aria-valuenow', '10')
    expect(screen.getByRole('slider', { name: 'Cut 1 end' })).toHaveAttribute('aria-valuenow', '20')
  })

  it('drags a cut edge, in seconds proportional to the pixel delta', () => {
    // 6.67 px/s; the cut ends at 20s → x = 133. Drag +67px ≈ +10s.
    const { rail, onCutsChange } = renderTimeline({ cuts: [cut()] })
    drag(rail, 133, 200)
    expect(onCutsChange).toHaveBeenCalledTimes(1)
    const next = onCutsChange.mock.calls[0][0]
    expect(next).toHaveLength(1)
    expect(next[0].id).toBe('c1')
    expect(next[0].start_s).toBe(10)
    expect(next[0].end_s).toBeCloseTo(30, 0)
  })

  it('THE ZOOM PROPERTY: the same pixel delta trims fewer seconds when zoomed in', () => {
    const { rail, onCutsChange } = renderTimeline({ cuts: [cut()] })

    drag(rail, 133, 200)
    const atFit = onCutsChange.mock.calls[0][0][0].end_s - 20

    onCutsChange.mockClear()
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }))
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }))
    const edgeX = Number.parseFloat(
      (screen.getByRole('slider', { name: 'Cut 1 end' }) as HTMLElement).style.left,
    )
    drag(rail, edgeX + 1, edgeX + 68)
    const zoomed = onCutsChange.mock.calls[0][0][0].end_s - 20

    // 4x the zoom → a quarter of the time for the same distance.
    expect(atFit / zoomed).toBeCloseTo(4, 0)
  })

  it('does not merge mid-drag — the dragged cut keeps its id', () => {
    // Merging during the gesture would retire the id under the user's finger.
    const { rail, onCutsChange } = renderTimeline({
      cuts: [cut({ id: 'a', start_s: 10, end_s: 20 }), cut({ id: 'b', start_s: 22, end_s: 30 })],
    })
    drag(rail, 133, 148)
    const next = onCutsChange.mock.calls[0][0]
    expect(next.map((c: EditorCut) => c.id)).toEqual(['a', 'b'])
  })

  it('snaps a selection to a word boundary', () => {
    const { rail, onSelection } = renderTimeline({ words: WORDS, snapEnabled: true })
    // 6.67 px/s; 'world' ends at 12s → x = 80. Release 2px short of it.
    drag(rail, 0, 78)
    expect(onSelection.mock.calls[0][0].end_s).toBeCloseTo(12, 6)
  })

  it('does not snap when snapping is switched off', () => {
    const { rail, onSelection } = renderTimeline({ words: WORDS, snapEnabled: false })
    drag(rail, 0, 78)
    expect(onSelection.mock.calls[0][0].end_s).not.toBeCloseTo(12, 6)
  })

  it('positions the playhead in PIXELS, so it survives zoom and scroll', () => {
    // Percent positioning cannot express a zoomed, scrolled view — it only ever
    // describes the whole clip. Explicit constraint change from the pre-390
    // test, which asserted `left: 50%`.
    renderTimeline({ duration: 100, currentTime: 50 })
    const playhead = screen.getByRole('slider', { name: /timeline scrubber/i })
    // 400px / 100s = 4 px/s → 50s = 200px.
    expect(Number.parseFloat(playhead.style.left)).toBeCloseTo(200, 0)
    expect(playhead).toHaveAttribute('aria-valuetext', '0:50 of 1:40')
  })

  it('announces the waveform, and says so when there is none', () => {
    renderTimeline({ peaks: null })
    expect(screen.getByRole('img', { name: /waveform unavailable/i })).toBeInTheDocument()
  })

  it('renders the real waveform when peaks are supplied', () => {
    renderTimeline({
      peaks: {
        version: 1,
        sample_rate: 16000,
        samples_per_pixel: 512,
        bits: 8,
        length: 3,
        data: [-100, 120, -10, 12, -60, 64],
      },
      sourceStartS: 0,
    })
    expect(screen.getByRole('img', { name: /clip audio waveform/i })).toBeInTheDocument()
    expect(screen.queryByRole('img', { name: /waveform unavailable/i })).toBeNull()
  })

  it('offers zoom controls, with fit disabled until there is something to reset', () => {
    renderTimeline()
    expect(screen.getByRole('button', { name: 'Zoom to fit' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }))
    expect(screen.getByRole('button', { name: 'Zoom to fit' })).toBeEnabled()
  })
})
