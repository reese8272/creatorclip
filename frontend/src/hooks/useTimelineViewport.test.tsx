import { act, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useTimelineViewport, type TimelineViewport } from './useTimelineViewport'
import { fitPxPerSecond } from '@/lib/timelineZoom'
import { flushResizeObservers } from '@/test/setup'

// jsdom has no layout: getBoundingClientRect returns all zeros. Stub it PER
// ELEMENT rather than on Element.prototype — a prototype-wide spy returns the
// same rect for every element, which is what made the old Timeline.test unable
// to distinguish a scroll viewport from its content (logged in
// docs/OFF_COURSE_BUGS.md, assigned to this issue).
function stubRect(el: HTMLElement, width: number, left = 0) {
  vi.spyOn(el, 'getBoundingClientRect').mockReturnValue({
    width,
    height: 100,
    top: 0,
    left,
    right: left + width,
    bottom: 100,
    x: left,
    y: 0,
    toJSON: () => ({}),
  } as DOMRect)
}

const DURATION = 40
const WIDTH = 1100

let latest: TimelineViewport

function Probe({ duration = DURATION, followTime }: { duration?: number; followTime?: number }) {
  const vp = useTimelineViewport({ duration, followTime })
  latest = vp
  return <div data-testid="rail" ref={vp.ref} style={{ width: WIDTH }} />
}

function mount(props: { duration?: number; followTime?: number } = {}) {
  // The element must already measure non-zero when the LAYOUT effect runs, which
  // is during render — before any handle to it exists. So the prototype is
  // stubbed for that single pass and immediately swapped for a per-element stub,
  // which is what lets later assertions give the rail a different width from
  // anything else on the page.
  const protoSpy = vi
    .spyOn(Element.prototype, 'getBoundingClientRect')
    .mockReturnValue({ width: WIDTH, height: 100, top: 0, left: 0, right: WIDTH, bottom: 100, x: 0, y: 0, toJSON: () => ({}) } as DOMRect)
  const utils = render(<Probe {...props} />)
  const rail = utils.getByTestId('rail')
  protoSpy.mockRestore()
  stubRect(rail, WIDTH)
  return { ...utils, rail }
}

afterEach(() => vi.restoreAllMocks())

describe('useTimelineViewport', () => {
  it('measures on first paint and starts fitted — no frame at width 0', () => {
    // The reason measurement uses getBoundingClientRect in a layout effect with
    // ResizeObserver only as the change-notifier: an RO has not fired yet on
    // first paint, and at width 0 every derived value is 0 or NaN.
    mount()
    expect(latest.viewportWidth).toBe(WIDTH)
    expect(latest.pxPerSecond).toBeCloseTo(fitPxPerSecond(WIDTH, DURATION), 6)
    expect(latest.scrollLeft).toBe(0)
    expect(latest.isZoomed).toBe(false)
  })

  it('zooms in and back out, anchored on the viewport centre by default', () => {
    mount()
    const fit = latest.pxPerSecond
    const centreTimeBefore = latest.duration / 2

    act(() => latest.zoomIn())
    expect(latest.pxPerSecond).toBeCloseTo(fit * 2, 6)
    expect(latest.isZoomed).toBe(true)
    // The instant at the centre pixel is unchanged.
    expect(latest.timeToX(centreTimeBefore)).toBeCloseTo(WIDTH / 2, 4)

    act(() => latest.zoomOut())
    expect(latest.pxPerSecond).toBeCloseTo(fit, 6)
  })

  it('zoom-to-fit re-arms follow-fit so a later resize stays fitted', () => {
    const { rail } = mount()
    act(() => latest.zoomIn())
    expect(latest.isZoomed).toBe(true)

    act(() => latest.zoomToFit())
    expect(latest.isZoomed).toBe(false)

    // Now resize. A fitted timeline must REFIT, not freeze at the old width.
    stubRect(rail, 550)
    act(() => {
      flushResizeObservers(550)
    })
    expect(latest.viewportWidth).toBe(550)
    expect(latest.pxPerSecond).toBeCloseTo(fitPxPerSecond(550, DURATION), 6)
  })

  it('never lets scroll leave its legal range', () => {
    mount()
    act(() => latest.zoomIn())
    act(() => latest.scrollBy(1e6))
    expect(latest.scrollLeft).toBeLessThanOrEqual(latest.duration * latest.pxPerSecond - WIDTH + 0.001)
    act(() => latest.scrollBy(-1e6))
    expect(latest.scrollLeft).toBe(0)
  })

  it('follows the playhead only when it would leave the viewport', () => {
    const { rerender } = mount({ followTime: 0 })
    act(() => latest.zoomIn()) // 55 px/s → 20s visible of 40s
    const before = latest.scrollLeft

    // Still inside: no scroll.
    rerender(<Probe followTime={5} />)
    expect(latest.scrollLeft).toBe(before)

    // Past the right edge: scroll the minimum needed.
    rerender(<Probe followTime={35} />)
    expect(latest.scrollLeft).toBeGreaterThan(before)
    const x = latest.timeToX(35)
    expect(x).toBeGreaterThanOrEqual(0)
    expect(x).toBeLessThanOrEqual(WIDTH)
  })

  it('clientXToTime accounts for the element offset, not just the scroll', () => {
    const { rail } = mount()
    stubRect(rail, WIDTH, 200) // rail starts 200px into the page
    // At fit, 1100px covers 40s → 27.5 px/s. clientX 200 is the rail's left edge.
    expect(latest.clientXToTime(200)).toBeCloseTo(0, 6)
    expect(latest.clientXToTime(200 + WIDTH / 2)).toBeCloseTo(DURATION / 2, 4)
  })
})

describe('wheel handling', () => {
  it('zooms on ctrl+wheel and cancels the event so the PAGE does not zoom', () => {
    // The load-bearing case for attaching via addEventListener with
    // { passive: false }: React's onWheel is passive, so preventDefault inside
    // one silently does nothing and Ctrl+wheel zooms the whole browser page.
    const { rail } = mount()
    const fit = latest.pxPerSecond

    const e = new WheelEvent('wheel', { deltaY: -100, ctrlKey: true, cancelable: true, bubbles: true })
    act(() => {
      rail.dispatchEvent(e)
    })
    expect(latest.pxPerSecond).toBeCloseTo(fit * 2, 6)
    expect(e.defaultPrevented).toBe(true)
  })

  it('treats a trackpad pinch (wheel + ctrlKey) as zoom, which is how pinch arrives', () => {
    const { rail } = mount()
    const fit = latest.pxPerSecond
    act(() => {
      rail.dispatchEvent(
        new WheelEvent('wheel', { deltaY: 100, ctrlKey: true, cancelable: true, bubbles: true }),
      )
    })
    // Already at fit, so zooming out is clamped — the point is that it was
    // handled as ZOOM, not scroll.
    expect(latest.pxPerSecond).toBeCloseTo(fit, 6)
    expect(latest.scrollLeft).toBe(0)
  })

  it('scrolls horizontally on a plain wheel once there is somewhere to scroll', () => {
    const { rail } = mount()
    act(() => latest.zoomIn())
    const before = latest.scrollLeft
    act(() => {
      rail.dispatchEvent(new WheelEvent('wheel', { deltaY: 120, cancelable: true, bubbles: true }))
    })
    expect(latest.scrollLeft).toBeGreaterThan(before)
  })

  it('does NOT swallow a plain wheel while fitted — the page must still scroll', () => {
    const { rail } = mount()
    const e = new WheelEvent('wheel', { deltaY: 120, cancelable: true, bubbles: true })
    act(() => {
      rail.dispatchEvent(e)
    })
    expect(e.defaultPrevented).toBe(false)
    expect(latest.scrollLeft).toBe(0)
  })
})
