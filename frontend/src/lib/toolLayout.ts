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

// Because 0.5625 × 16/9 == 1 exactly, `(100dvh - X) * 0.5625` produces a 9:16
// player whose HEIGHT is exactly `100dvh - X`. So X is not a fudge factor: it is
// the total non-player vertical chrome, and both values below were MEASURED in
// Chromium at 1440×900, not estimated.
//
// ⚠ This project's root font-size is ~14.39px, NOT 16px (index.css sets a fluid
// base). Do not convert these rem figures at 16px/rem — an X derived that way
// comes out ~10% short and the viewer card silently overflows its grid row,
// clipping the meta row below the fold.

/**
 * Editor viewer width. X = 30.5rem ≈ 439px covers nav + header strip + timeline
 * dock + status bar + main/card padding + the compact meta row. Measured at
 * 1440×900: the viewer row is 584px and the card adds 117px around the video,
 * so the player gets 467px of height — 257px wide, up from a frozen 180px.
 */
export const EDITOR_PLAYER_W = 'w-[min(30rem,calc((100dvh_-_30.5rem)*0.5625))]'

/** Review player width. No timeline dock, so less chrome to subtract. */
export const REVIEW_PLAYER_W = 'w-[min(30rem,calc((100dvh_-_22rem)*0.5625))]'
