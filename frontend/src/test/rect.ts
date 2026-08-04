import { vi } from 'vitest'

// Layout stubs for jsdom, which has no layout engine and returns an all-zero
// DOMRect for every element.
//
// PER ELEMENT, not on the prototype. A prototype-wide spy hands the same rect to
// every element, so a component cannot distinguish its scroll viewport from its
// content — under which `viewportWidth === contentWidth` unconditionally and any
// zoom assertion passes for the wrong reason. That was a real defect in the old
// Timeline test, logged in docs/OFF_COURSE_BUGS.md and fixed by Issue 390.

export function rect(width: number, height = 100, left = 0, top = 0): DOMRect {
  return {
    width,
    height,
    left,
    top,
    right: left + width,
    bottom: top + height,
    x: left,
    y: top,
    toJSON: () => ({}),
  } as DOMRect
}

/** Give one element a measurable box for the rest of the test. */
export function stubRect(el: Element, width: number, height = 100, left = 0): void {
  vi.spyOn(el, 'getBoundingClientRect').mockReturnValue(rect(width, height, left))
}

/**
 * Render with a measurable viewport, then narrow the stub to one element.
 *
 * A component that measures in a LAYOUT effect does so during the initial render,
 * before any handle to its DOM exists — so there is no way to stub the specific
 * element first. This stubs the prototype for that single pass, restores it, and
 * hands back a `narrow` function so the test can then give individual elements
 * their own boxes.
 */
export function withMeasuredRender<T>(width: number, height: number, render: () => T): T {
  const spy = vi
    .spyOn(Element.prototype, 'getBoundingClientRect')
    .mockReturnValue(rect(width, height))
  try {
    return render()
  } finally {
    spy.mockRestore()
  }
}
