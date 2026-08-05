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
 * The stage grid cell (L26 Issue 424 — the sizing inversion). Applied to the
 * grid cell that hosts the ShortStage: it becomes a size container, so the
 * stage derives the media size from ITS OWN cell via cq units instead of
 * subtracting a hand-measured tower of page chrome from 100dvh (the documented
 * re-measure trap above — every dock/toolbar change used to invalidate X).
 * The grid row (`minmax(0,1fr)`) hands the cell whatever height is left; the
 * cell needs no knowledge of what the page stacked around it.
 */
export const STAGE_CELL = 'lg:[container-type:size]'

/**
 * Stage media width (the 9:16 player INSIDE the stage card). The subtrahend is
 * stage-INTERNAL chrome only — everything the card stacks in the height budget
 * besides the raw 9:16 video, MEASURED in Chromium at 1440×900 with the fluid
 * root at exactly 14.4px/rem (see the warning above — a 16px/rem conversion
 * lands ~10% short; note p-4 here is 14.4px, not 16px, for the same reason):
 *
 *   card padding p-4              2 × 14.4px = 28.8px
 *   card border                   2 × 1px    =  2.0px
 *   player transport bar (full density)      = 36.97px  (border-t 1 + py 10.8 + 25.2)
 *   gap-3, player → meta row                 = 10.8px
 *   meta row (mono line + fit pill)          = 21.05px
 *   ─────────────────────────────────────────  99.6px = 6.92rem → 7rem (1.2px slack)
 *
 * `min()` caps: 34rem stops the media crowding the rails on very tall
 * displays; 100cqw stops it overflowing a narrow center column. The stage's
 * `below` slot (Review's trim filmstrip, measured 112px) is deliberately NOT
 * in the budget — the media is sized to the cell and the cell scrolls to the
 * filmstrip, which is what buys the ~1.9× media area (377×672 vs 273×486) at
 * 1440×900. Below lg the shell disengages and the media takes a plain
 * viewport-relative width, as before.
 */
export const STAGE_MEDIA_W =
  'w-[min(24rem,80vw)] lg:w-[min(34rem,100cqw,calc((100cqh_-_7rem)*0.5625))]'

/**
 * Editor viewer width. X = 31.5rem ≈ 453px covers nav + header strip + timeline
 * dock + status bar + main/card padding + the compact meta row. Re-measured at
 * 1440×900 after Issue 390 gave the dock a zoom toolbar and a real ruler, which
 * cost the region grid ~13px; the e2e "card must fit its row" guard caught the
 * overflow rather than it shipping as a clipped meta row.
 */
export const EDITOR_PLAYER_W = 'w-[min(30rem,calc((100dvh_-_31.5rem)*0.5625))]'

/**
 * Review player width. X = 29.5rem — no timeline dock, but ClipPlayer wraps the
 * video in 287px of its own chrome (trim filmstrip, fit badge, Next). Measured
 * at 1440×900: 773px row, so the player gets 486px of height.
 */
export const REVIEW_PLAYER_W = 'w-[min(30rem,calc((100dvh_-_29.5rem)*0.5625))]'

/**
 * Long-form SOURCE player width. 1.7778 = 16/9, so this bounds a landscape
 * player's height the same way — X = 41rem leaves room for the Chip callout, the
 * master timeline and the clip lists below it.
 *
 * Without this the placeholder/player is `aspect-video w-full`: at the ~1075px
 * column width that is 605px tall, which pushes the master timeline to the
 * bottom edge of the viewport and the clip lists entirely off-screen. `min(100%)`
 * keeps it from ever exceeding its column on a short, wide viewport.
 */
export const SOURCE_PLAYER_W = 'w-[min(100%,calc((100dvh_-_41rem)*1.7778))]'
