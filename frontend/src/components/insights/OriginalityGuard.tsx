import { EmptyStatePrompt } from '@/components/EmptyStatePrompt'
import { Panel } from '@/components/insights/InsightsPanel'
import type { OriginalityAdvisory } from '@/types'

/**
 * Originality guard — sameness advisory over the creator's OWN recent clips
 * (Issue 375). Advisory only.
 *
 * ⛔ Copy in this file is honesty-load-bearing. Issue 383 (2026-07-30) found
 * that no primary source describes a "three-strike ladder" for YouTube's
 * inauthentic-content policy, and that the policy is not new (renamed 15 July
 * 2025; the 16 July 2026 event was a clarification, not a launch). Never add
 * a strike count, a suspension timeline, or "new policy" framing here — state
 * consequences qualitatively ("may affect monetization eligibility") and let
 * the dated `policy_url` citation carry the rest.
 *
 * `checked === false` is a distinct state from "checked, nothing found" — it
 * means the comparison could not run yet (not enough recent clips have a
 * usable content embedding). Collapsing it into "all clear" would be a false
 * reassurance, so it renders its own quiet copy instead of nothing.
 */
export function OriginalityGuard({
  advisory,
  isError,
}: {
  advisory: OriginalityAdvisory | undefined
  isError?: boolean
}) {
  if (isError) {
    return (
      <Panel title="Originality guard">
        <p className="text-sm text-danger">Could not load your originality advisory.</p>
      </Panel>
    )
  }

  if (!advisory) {
    return (
      <Panel title="Originality guard">
        <p className="text-sm text-muted">Checking your recent clips…</p>
      </Panel>
    )
  }

  if (!advisory.checked) {
    return (
      <Panel title="Originality guard">
        <EmptyStatePrompt
          title="Not enough recent clips to check yet."
          detail="This needs at least two clips with transcript coverage to compare."
          actionLabel="Generate more clips"
          actionTo="/dashboard"
        />
      </Panel>
    )
  }

  if (!advisory.flagged) {
    return (
      <Panel title="Originality guard">
        <p className="text-sm text-fg">
          Nothing flagged across your last{' '}
          <span className="font-mono font-semibold text-accent-text">{advisory.window}</span>{' '}
          clips.
        </p>
        <p className="mt-2 text-xs text-subtle">
          Advisory only, based on your own recent clips — not a certification of monetization
          eligibility or a statement from YouTube.
        </p>
      </Panel>
    )
  }

  return (
    <Panel title="Originality guard">
      <p className="text-sm text-fg">
        <span className="font-mono font-semibold text-accent-text">{advisory.cluster_size}</span>{' '}
        of your last <span className="font-mono">{advisory.window}</span> clips are near-identical
        in spoken content.
        {advisory.common_structural_features.length > 0 && (
          <>
            {' '}
            They also share the same{' '}
            <span className="text-muted">{advisory.common_structural_features.join(', ')}</span>.
          </>
        )}
      </p>
      <p className="mt-2 text-sm text-muted">
        YouTube's guidance on original content says mass-produced or repetitive output may affect
        monetization eligibility —{' '}
        <a
          href={advisory.policy_url}
          target="_blank"
          rel="noreferrer"
          className="underline text-accent-text"
        >
          see YouTube's policy
        </a>{' '}
        (checked {advisory.policy_checked}).
      </p>
      <p className="mt-2 text-xs italic text-subtle">
        This is an advisory based on your own recent clips, not a certification of monetization
        eligibility or a statement from YouTube.
      </p>
    </Panel>
  )
}
