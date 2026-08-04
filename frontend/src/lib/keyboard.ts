// Shared keyboard-shortcut guards (Issue 390).
//
// These lived inside `video-player.tsx` and were module-private. Issue 390 adds a
// second shortcut owner (the timeline: I/O, Delete, zoom, fine-step arrows) and
// Issue 391 adds a third (undo/redo), and all three must agree EXACTLY on what
// counts as "the user is typing" — a second, drifting copy of this predicate is
// how a space bar starts playing video while someone types a title.
//
// They live in `lib/` rather than in the component file because a non-component
// export from a `.tsx` trips `react-refresh/only-export-components` (the same
// reason `iconSizes.ts` and `buttonVariants.ts` are separate modules).

/**
 * True when a keystroke belongs to whatever the user is typing in, not to us.
 *
 * `button` is in the tag list deliberately: Space and Enter activate a focused
 * button, and stealing Space there would break every button on the page.
 * `[role="dialog"]` is matched WITHOUT requiring `aria-modal`, because a
 * non-modal popover still owns its own keys while focus is inside it.
 */
export function isTypingTarget(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  if (!el || typeof el.closest !== 'function') return false
  if (el.isContentEditable) return true
  if (/^(input|textarea|select|button)$/i.test(el.tagName)) return true
  return el.closest('[contenteditable="true"], [role="textbox"], [role="dialog"]') !== null
}

/**
 * True when a modal dialog is open anywhere on the page.
 *
 * Distinct from the `[role="dialog"]` branch above: that one asks "is the EVENT
 * TARGET inside a dialog", which cannot fire when focus is on `document.body`.
 * This asks "is anything modal on screen at all", which is what a document-level
 * listener needs — a modal takes the whole page's keys regardless of focus.
 */
export function isModalOpen(): boolean {
  return document.querySelector('[role="dialog"][aria-modal="true"]') !== null
}
