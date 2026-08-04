import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { TimelineRuler } from './TimelineRuler'
import { fitPxPerSecond, type Viewport } from '@/lib/timelineZoom'

function viewport(over: Partial<Viewport> = {}): Viewport {
  const duration = over.duration ?? 60
  const viewportWidth = over.viewportWidth ?? 400
  return {
    duration,
    viewportWidth,
    pxPerSecond: fitPxPerSecond(viewportWidth, duration),
    scrollLeft: 0,
    ...over,
  }
}

function labels(): string[] {
  return [...document.querySelectorAll('span')].map((s) => s.textContent ?? '')
}

describe('TimelineRuler', () => {
  it('emits human-round labels at fit — never duration/n', () => {
    // 400px / 60s = 6.67 px/s → the smallest nice interval clearing 80px is 15s.
    render(<TimelineRuler viewport={viewport()} />)
    expect(labels()).toEqual(['0:00', '0:15', '0:30', '0:45', '1:00'])
  })

  it('gets finer as you zoom in, and every label stays at least minSpacing apart', () => {
    const v = viewport({ pxPerSecond: 110 }) // 10s visible in 1100px… at width 400, 3.6s
    render(<TimelineRuler viewport={v} />)
    const xs = [...document.querySelectorAll('span')].map((s) =>
      Number.parseFloat((s as HTMLElement).style.left),
    )
    for (let i = 1; i < xs.length; i++) {
      expect(xs[i] - xs[i - 1]).toBeGreaterThanOrEqual(80)
    }
    // Finer than the fit case above.
    expect(labels().length).toBeGreaterThan(1)
    expect(labels()[0]).toBe('0:00')
  })

  it('follows the scroll window rather than always starting at zero', () => {
    render(<TimelineRuler viewport={viewport({ pxPerSecond: 110, scrollLeft: 550 })} />)
    // Left edge is 5s in; the first tick at or before it is what shows.
    expect(labels()[0]).not.toBe('0:00')
    expect(Number.parseFloat((document.querySelector('span') as HTMLElement).style.left)).toBeLessThanOrEqual(0)
  })

  it('positions labels in px from TIME, not from the map index', () => {
    // The no-synthetic-waveform gate forbids geometry derived from a loop index;
    // this asserts the actual behaviour that rule protects.
    const v = viewport({ duration: 60, viewportWidth: 400 })
    render(<TimelineRuler viewport={v} />)
    const first = document.querySelectorAll('span')[1] as HTMLElement
    // Second tick is 15s → 15 * 6.667 = 100px.
    expect(Number.parseFloat(first.style.left)).toBeCloseTo(100, 0)
  })

  it('renders nothing but stays mounted at zero measured width', () => {
    // First paint before measurement: must not throw, must not emit a bogus tick.
    render(<TimelineRuler viewport={viewport({ viewportWidth: 0, pxPerSecond: 0 })} />)
    expect(labels()).toEqual([])
  })

  it('is hidden from assistive tech — the playhead announces position instead', () => {
    const { container } = render(<TimelineRuler viewport={viewport()} />)
    expect(container.firstElementChild).toHaveAttribute('aria-hidden', 'true')
    expect(screen.queryByRole('presentation')).toBeNull()
  })

  it('shows hours on a long source and sub-second precision when zoomed in', () => {
    render(<TimelineRuler viewport={viewport({ duration: 7200, viewportWidth: 1100 })} />)
    expect(labels().some((l) => /^\d:\d\d:\d\d$/.test(l))).toBe(true)

    document.body.innerHTML = ''
    render(<TimelineRuler viewport={viewport({ duration: 40, pxPerSecond: 2000 })} />)
    expect(labels().some((l) => l.includes('.'))).toBe(true)
  })
})
