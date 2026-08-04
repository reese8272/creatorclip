import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Timeline } from './Timeline'
import { stubRect, withMeasuredRender } from '@/test/rect'
import type { EditorCut } from '@/types'

// The editing keys that TimelineRail owns (zoom + scrub). The keys ShortFormEditor
// owns (I/O, Delete, S) are exercised through the editor's own suite, because
// they act on cut state this component does not hold.

const WIDTH = 400
const DURATION = 40

// Dispatched natively (the bus listens on `document`, not through React), so the
// state updates it triggers need an explicit act() to flush.
function press(key: string, init: KeyboardEventInit = {}) {
  const e = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init })
  act(() => {
    document.body.dispatchEvent(e)
  })
  return e
}

function setup(props: Partial<React.ComponentProps<typeof Timeline>> = {}) {
  const onSeek = vi.fn()
  withMeasuredRender(WIDTH, 80, () =>
    render(
      <Timeline
        duration={DURATION}
        currentTime={10}
        cuts={[]}
        onSeek={onSeek}
        onSelection={vi.fn()}
        {...props}
      />,
    ),
  )
  stubRect(screen.getByRole('group', { name: 'Clip timeline' }), WIDTH, 80)
  return { onSeek }
}

afterEach(() => {
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

describe('timeline keyboard', () => {
  it('zooms with =, - and 0, matching the on-screen controls', () => {
    setup()
    const fitButton = () => screen.getByRole('button', { name: 'Zoom to fit' })
    expect(fitButton()).toBeDisabled()

    press('=')
    expect(fitButton()).toBeEnabled()

    press('0')
    expect(fitButton()).toBeDisabled()
  })

  it('claims the keys it handles so the player never also acts on them', () => {
    setup()
    expect(press('=').defaultPrevented).toBe(true)
    expect(press('0').defaultPrevented).toBe(true)
    // A key the timeline does not own must stay available to the player.
    expect(press('k').defaultPrevented).toBe(false)
  })

  it('A BARE ARROW STEPS ONE PIXEL AT THE CURRENT ZOOM', () => {
    // This is what makes "fine step" actually fine without asking the user to
    // pick a unit: coarse when zoomed out, frame-accurate when zoomed in.
    const { onSeek } = setup()

    // At fit: 400px / 40s = 10 px/s → one pixel is 0.1s.
    press('ArrowRight')
    expect(onSeek).toHaveBeenLastCalledWith(expect.closeTo(10.1, 5))

    onSeek.mockClear()
    press('=') // 2x zoom → 20 px/s → one pixel is 0.05s
    press('ArrowRight')
    expect(onSeek).toHaveBeenLastCalledWith(expect.closeTo(10.05, 5))
  })

  it('Shift+arrow keeps the player’s familiar 5s jump', () => {
    const { onSeek } = setup()
    press('ArrowRight', { shiftKey: true })
    expect(onSeek).toHaveBeenLastCalledWith(15)
    press('ArrowLeft', { shiftKey: true })
    expect(onSeek).toHaveBeenLastCalledWith(5)
  })

  it('clamps scrubbing to the clip', () => {
    const { onSeek } = setup({ currentTime: 0 })
    press('ArrowLeft', { shiftKey: true })
    expect(onSeek).toHaveBeenLastCalledWith(0)

    onSeek.mockClear()
    document.body.innerHTML = ''
    const late = setup({ currentTime: DURATION })
    press('ArrowRight', { shiftKey: true })
    expect(late.onSeek).toHaveBeenLastCalledWith(DURATION)
  })

  it('ignores keys while the user is typing', () => {
    // The key-theft guard, at the timeline layer. Without it, pressing "0" in a
    // title field would zoom the timeline to fit.
    setup()
    const input = document.createElement('input')
    document.body.appendChild(input)
    const e = new KeyboardEvent('keydown', { key: '0', bubbles: true, cancelable: true })
    input.dispatchEvent(e)
    expect(e.defaultPrevented).toBe(false)
  })

  it('selects a cut on click so Delete has a target', () => {
    const onSelectCut = vi.fn()
    const cuts: EditorCut[] = [{ id: 'a', start_s: 10, end_s: 20 }]
    setup({ cuts, onSelectCut })
    const rail = screen.getByRole('group', { name: 'Clip timeline' })
    // 10 px/s → the body of cut 'a' spans x=100..200.
    fireEvent.pointerDown(rail, { clientX: 150, button: 0, pointerId: 1 })
    fireEvent.pointerUp(rail, { clientX: 150, pointerId: 1 })
    expect(onSelectCut).toHaveBeenCalledWith('a')
  })
})
