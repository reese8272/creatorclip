import { EmptyStatePrompt } from '@/components/EmptyStatePrompt'
import { Panel } from '@/components/insights/InsightsPanel'
import type { ProofOfLift as ProofOfLiftData } from '@/types'

/**
 * Proof of Lift (Issue 374) — what actually HAPPENED to clips published via
 * AutoClip. Reports outcomes, never predictions.
 *
 * Three honesty rules are load-bearing here and mirror the API:
 *  1. `deferred_count` is clips published but not yet judgeable (fewer than 3
 *     comparable Shorts to form a baseline). They are shown as their own
 *     bucket — never folded into "didn't beat", which would invent bad news.
 *  2. A count is a fact; a rate is an inference. Counts always render. The
 *     rate only renders once the API supplies one (>= min_judged_for_rate).
 *  3. When a rate renders it carries its Wilson interval, so a 10/10 run reads
 *     as a range, not "100%".
 *
 * The empty state is the DEFAULT state at beta launch — creators will have
 * ~zero published clips for weeks — so it is written to be useful, not apologetic.
 */
export function ProofOfLift({
  lift,
  isError,
}: {
  lift: ProofOfLiftData | undefined
  isError?: boolean
}) {
  if (isError) {
    return (
      <Panel title="Proof of lift">
        <p className="text-sm text-danger">Could not load your published-clip outcomes.</p>
      </Panel>
    )
  }

  if (!lift) {
    return (
      <Panel title="Proof of lift">
        <p className="text-sm text-muted">Loading your published-clip outcomes…</p>
      </Panel>
    )
  }

  // Empty state — designed first, because it ships as the default for weeks.
  if (lift.published_count === 0) {
    return (
      <Panel title="Proof of lift">
        <EmptyStatePrompt
          title="Nothing published through AutoClip yet — so there is nothing to report."
          detail="Once you publish clips from here, this panel tracks how each one actually did against your own channel, and feeds that back into how clips are ranked for you."
          actionLabel="Review clips to publish"
          actionTo="/review"
        />
      </Panel>
    )
  }

  const pct = (v: number) => `${Math.round(v * 100)}%`

  return (
    <Panel title="Proof of lift">
      <p className="text-sm text-fg">
        <span className="font-mono font-semibold text-accent-text">{lift.published_count}</span>{' '}
        {lift.published_count === 1 ? 'clip' : 'clips'} published through AutoClip.{' '}
        {lift.judged_count > 0 ? (
          <>
            <span className="font-mono font-semibold text-accent-text">
              {lift.beat_median_count}
            </span>{' '}
            of <span className="font-mono">{lift.judged_count}</span> judged{' '}
            {lift.judged_count === 1 ? 'clip' : 'clips'} beat your channel median.
          </>
        ) : (
          <>None have a verdict yet.</>
        )}
      </p>

      {lift.deferred_count > 0 && (
        <p className="mt-2 text-sm italic text-subtle">
          {lift.deferred_count} {lift.deferred_count === 1 ? 'clip is' : 'clips are'} still
          unjudged — there aren’t enough comparable Shorts yet to set a fair baseline. Not a
          result either way.
        </p>
      )}

      {lift.rate !== null && lift.rate_ci_low !== null && lift.rate_ci_high !== null ? (
        <p className="mt-3 text-sm text-fg">
          That’s{' '}
          <span className="font-mono font-semibold text-accent-text">{pct(lift.rate)}</span>{' '}
          <span className="text-muted">
            (95% confidence interval {pct(lift.rate_ci_low)}–{pct(lift.rate_ci_high)})
          </span>
          .
        </p>
      ) : (
        <p className="mt-3 text-sm italic text-subtle">
          Too few judged clips to state a rate yet — we show one at{' '}
          {lift.min_judged_for_rate}. Until then these are raw counts, not a trend.
        </p>
      )}

      {lift.contrast && (
        <div className="mt-4 border-t border-default pt-3">
          <p className="text-sm text-fg">
            What the {lift.contrast.winners_n} that beat your median had in common, versus the{' '}
            {lift.contrast.underperformers_n} that didn’t:
          </p>
          <dl className="mt-2 space-y-1 text-sm">
            <ContrastRow
              label="Median length"
              winner={fmtSeconds(lift.contrast.winners.median_duration_s)}
              loser={fmtSeconds(lift.contrast.underperformers.median_duration_s)}
            />
            <ContrastRow
              label="Median setup lead"
              winner={fmtSeconds(lift.contrast.winners.median_setup_lead_s)}
              loser={fmtSeconds(lift.contrast.underperformers.median_setup_lead_s)}
            />
            <ContrastRow
              label="Avg fit score"
              winner={fmtNum(lift.contrast.winners.avg_score)}
              loser={fmtNum(lift.contrast.underperformers.avg_score)}
            />
          </dl>
          <p className="mt-2 text-xs italic text-subtle">
            Drawn from these clips’ own stored features — not general advice.
          </p>
        </div>
      )}

      <p className="mt-4 text-xs text-subtle">
        Measured as: {lift.metric} Checkpoint: {lift.checkpoint}.
      </p>
    </Panel>
  )
}

function ContrastRow({
  label,
  winner,
  loser,
}: {
  label: string
  winner: string
  loser: string
}) {
  return (
    <div className="flex items-center justify-between">
      <dt className="text-muted">{label}</dt>
      <dd className="font-mono">
        <span className="text-accent-text">{winner}</span>
        <span className="text-subtle"> vs </span>
        <span className="text-muted">{loser}</span>
      </dd>
    </div>
  )
}

function fmtSeconds(v: number | null): string {
  return v === null ? '—' : `${v.toFixed(0)}s`
}

function fmtNum(v: number | null): string {
  return v === null ? '—' : v.toFixed(2)
}
