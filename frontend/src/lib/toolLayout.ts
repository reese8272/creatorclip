// Layout constants for the tool-route app shell (Issue 389).
//
// These are COMPLETE literal class strings on purpose. Tailwind v4 scans .ts
// files as plain text, so a full literal is picked up by the compiler — a string
// assembled by concatenation or interpolation is not, and would silently emit no
// CSS. Never build these from parts.
//
// The players are sized from the viewport height rather than pinned to a fixed
// px width: the shell is exactly one viewport tall, so the space available to a
// 9:16 player is a function of the height left after the chrome, and 0.5625 is
// 9/16. `min()` caps growth so the player never crowds the side panels on a very
// tall display. Note the underscores — an arbitrary value cannot contain literal
// spaces, so calc() operands are joined with `_`.

/** Editor viewer: full height minus nav, header strip, timeline dock and status bar. */
export const EDITOR_PLAYER_W = 'w-[min(26rem,calc((100dvh_-_24rem)*0.5625))]'

/** Review player: full height minus nav, toolbar strip and status bar (no dock). */
export const REVIEW_PLAYER_W = 'w-[min(26rem,calc((100dvh_-_20rem)*0.5625))]'

/**
 * Height of the Editor's docked timeline rail.
 *
 * Budget: ruler ~20px + wave/cut lane ~96px + action row ~28px + padding. Issue
 * 390 rebuilds the timeline into this box, so it must stay a definite height —
 * an `auto` anywhere in the chain makes the rail measure 0 and divides by zero
 * in the zoom math. It is also the value ToolChrome feeds to --app-bottom-inset.
 */
export const TIMELINE_RAIL_H = 176
