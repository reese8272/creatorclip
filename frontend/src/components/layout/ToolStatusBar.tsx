import { LegalLinks } from '@/components/LegalLinks'

/**
 * The honesty statement, verbatim.
 *
 * CLAUDE.md makes this a hard constraint: it must appear on every interface, and
 * the wording is load-bearing ("predicts fit ... does not promise virality" /
 * "estimates, not a guarantee"). Issue 389 moved it out of a full-width band in
 * the work area and into this docked status bar WITHOUT changing a character —
 * the ten inline copies in Editor.tsx and Review.tsx were byte-identical to this
 * string. Do not reword, truncate, or abbreviate it to fit a layout; if it ever
 * stops fitting, the layout gives way, not the sentence.
 */
export const HONESTY_STATEMENT =
  'AutoClip predicts fit with your style and audience — it does not promise virality. ' +
  'All scores are estimates grounded in your own channel data.'

/**
 * ToolStatusBar — the persistent bottom strip of the tool-route shell.
 *
 * Carries the honesty statement plus the legal links, so a tool route can drop
 * the marketing footer without losing either. It is a sibling of <main>, never a
 * child, so it maps to `contentinfo` and never scrolls with the work area.
 *
 * Rendered FIRST in the DOM and reordered to the bottom at `lg` (Issue 389):
 * below the breakpoint the shell is disengaged and this reproduces exactly where
 * today's DisclaimerBand sits; at `lg` the compliance statement is still
 * announced before the content it qualifies, which is the safer direction for a
 * visual/DOM order mismatch.
 */
export function ToolStatusBar() {
  return (
    <footer className="order-first flex shrink-0 flex-wrap items-center justify-between gap-x-6 gap-y-1 border-b border-default bg-surface px-6 py-1.5 text-xs text-muted lg:order-last lg:min-h-9 lg:border-b-0 lg:border-t">
      <span>{HONESTY_STATEMENT}</span>
      <LegalLinks className="text-subtle" />
    </footer>
  )
}
