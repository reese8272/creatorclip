import { render, screen, fireEvent } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Timeline } from './Timeline'
import type { Cut } from './Timeline'

// getBoundingClientRect is not implemented in jsdom; stub a 400px-wide container.
function stubRect(width: number = 400) {
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({
    left: 0, top: 0, right: width, bottom: 80,
    width, height: 80, x: 0, y: 0, toJSON: () => ({}),
  })
}

function renderTimeline(props?: Partial<React.ComponentProps<typeof Timeline>>) {
  const defaults = {
    duration: 60,
    currentTime: 0,
    cuts: [] as Cut[],
    onSeek: vi.fn(),
    onSelection: vi.fn(),
  }
  return render(<Timeline {...defaults} {...props} />)
}

describe('Timeline', () => {
  it('renders the scrubber container with the correct aria role', () => {
    renderTimeline()
    expect(screen.getByRole('slider', { name: /timeline scrubber/i })).toBeInTheDocument()
  })

  it('shows the time ruler labels', () => {
    renderTimeline({ duration: 60, currentTime: 0 })
    expect(screen.getByText('0:00')).toBeInTheDocument()
    // Mid-point label for 60s clip is 0:30
    expect(screen.getByText('0:30')).toBeInTheDocument()
    // End label
    expect(screen.getByText('1:00')).toBeInTheDocument()
  })

  it('calls onSeek when a single click is made (no drag)', () => {
    stubRect(400)
    const onSeek = vi.fn()
    renderTimeline({ onSeek, duration: 40 })
    const slider = screen.getByRole('slider')
    // Click at x=200 → 200/400 * 40s = 20s
    fireEvent.mouseDown(slider, { button: 0, clientX: 200 })
    fireEvent.mouseUp(slider, { clientX: 200 })
    expect(onSeek).toHaveBeenCalledOnce()
    expect(onSeek.mock.calls[0][0]).toBeCloseTo(20, 1)
  })

  it('calls onSelection (not onSeek) when a drag range exceeds MIN_CUT_S', () => {
    stubRect(400)
    const onSeek = vi.fn()
    const onSelection = vi.fn()
    renderTimeline({ onSeek, onSelection, duration: 40 })
    const slider = screen.getByRole('slider')
    // Drag from x=0 to x=100 → 0s to 10s
    fireEvent.mouseDown(slider, { button: 0, clientX: 0 })
    fireEvent.mouseMove(slider, { clientX: 100 })
    fireEvent.mouseUp(slider, { clientX: 100 })
    expect(onSelection).toHaveBeenCalledOnce()
    const cut: Cut = onSelection.mock.calls[0][0]
    expect(cut.start_s).toBeCloseTo(0, 1)
    expect(cut.end_s).toBeCloseTo(10, 1)
    // onSeek must NOT fire on a drag
    expect(onSeek).not.toHaveBeenCalled()
  })

  it('renders cut overlay divs for each queued cut', () => {
    const cuts: Cut[] = [
      { start_s: 10, end_s: 20 },
      { start_s: 40, end_s: 50 },
    ]
    renderTimeline({ cuts, duration: 60 })
    // aria-label is set on each cut overlay
    expect(screen.getByLabelText(/Cut 1:/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/Cut 2:/i)).toBeInTheDocument()
  })

  it('shows the placeholder wave when no waveformData or image is supplied', () => {
    renderTimeline({ waveformData: null, waveformImageUrl: null })
    // The placeholder is aria-hidden but the bars render
    const { container } = render(
      <Timeline
        duration={30}
        currentTime={0}
        cuts={[]}
        onSeek={vi.fn()}
        onSelection={vi.fn()}
      />,
    )
    // The placeholder div contains aria-hidden="true" bars — check the container has them
    expect(container.querySelectorAll('[aria-hidden="true"]').length).toBeGreaterThan(0)
  })

  it('renders a waveform image when waveformImageUrl is provided', () => {
    renderTimeline({ waveformImageUrl: 'http://cdn/wave.png' })
    const img = screen.getByRole('img', { name: /clip waveform/i })
    expect(img).toBeInTheDocument()
    expect(img).toHaveAttribute('src', 'http://cdn/wave.png')
  })

  it('playhead position reflects currentTime / duration ratio', () => {
    const { container } = render(
      <Timeline
        duration={100}
        currentTime={50}
        cuts={[]}
        onSeek={vi.fn()}
        onSelection={vi.fn()}
      />,
    )
    // The playhead is the narrow 1px bg-accent div with left set as inline style.
    // There are multiple aria-hidden elements (placeholder bars + playhead); use
    // querySelectorAll and find the one with an inline left style.
    const candidates = container.querySelectorAll('[aria-hidden="true"]') as NodeListOf<HTMLElement>
    const playhead = Array.from(candidates).find((el) => el.style.left !== '')
    expect(playhead?.style.left).toBe('50%')
  })
})
