// Trim-region math for the filmstrip (Issue 306). Kept out of the component file
// so TrimFilmstrip.tsx exports only a component (react-refresh/only-export-components).

export const MIN_TRIM_S = 0.5

// Clamp a proposed handle move so start < end with a minimum selected window,
// both inside [0, dur]. Pure + exported so the drag math is unit-testable
// (jsdom has no layout, so this is where the real logic lives).
export function clampTrim(
  handle: 'start' | 'end',
  value: number,
  start: number,
  end: number,
  dur: number,
): { start: number; end: number } {
  if (handle === 'start') {
    return { start: Math.max(0, Math.min(value, end - MIN_TRIM_S)), end }
  }
  return { start, end: Math.min(dur, Math.max(value, start + MIN_TRIM_S)) }
}
