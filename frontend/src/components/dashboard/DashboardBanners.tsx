import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import type { Balance, SetupStep } from '@/types'

// DNA setup CTA — shown until onboarding reaches `complete`. `link_first_video`
// is intentionally excluded: the EmptyHero already owns that nudge, so showing
// both would double up. Mirrors static/index.html `init()`.
//
// The server's next_action_url still points at /static/* pages; now that
// onboarding (85d) + profile (85a) are ported, route the rendered steps to
// their SPA equivalents instead (sync_catalog/build_dna → onboarding,
// confirm_dna → the profile brief). Falls back to the server URL for any
// unexpected step.
const STEP_ROUTE: Partial<Record<SetupStep['step'], string>> = {
  sync_catalog: '/onboarding',
  build_dna: '/onboarding',
  confirm_dna: '/profile',
}

export function DnaCta({ setup }: { setup: SetupStep | null | undefined }) {
  if (!setup || setup.step === 'complete' || setup.step === 'link_first_video') return null
  const spaRoute = STEP_ROUTE[setup.step]
  return (
    <div className="mb-6 flex items-center justify-between gap-4 rounded-md border border-default border-l-2 border-l-accent bg-surface px-5 py-4">
      <p className="text-sm text-muted">{setup.label}</p>
      {spaRoute ? (
        <Link to={spaRoute}>
          <Button>Set up →</Button>
        </Link>
      ) : (
        <a href={setup.next_action_url ?? '/static/onboarding.html'}>
          <Button>Set up →</Button>
        </a>
      )}
    </div>
  )
}

const DISMISS_KEY = 'creatorclip:trial_banner_dismissed_day'

function readDismissedDay(): number | null {
  try {
    const v = localStorage.getItem(DISMISS_KEY)
    return v === null ? null : Number(v)
  } catch {
    return null
  }
}

// Trial countdown. Dismissal is bucketed per days-remaining so the banner
// re-asserts when the count drops (passing a new threshold is worth re-surfacing);
// the final day overrides any dismissal. Ported from static/index.html.
export function TrialBanner({ balance }: { balance: Balance | null }) {
  const days = balance?.trial_days_remaining ?? 0
  const finalDay = days <= 1
  const [dismissedDay, setDismissedDay] = useState<number | null>(() => readDismissedDay())

  if (!balance?.trial_active) return null
  if (dismissedDay !== null && dismissedDay === days && !finalDay) return null

  const minutes = balance.minutes_balance
  const dayWord = days === 1 ? 'day' : 'days'
  const headline = finalDay
    ? `Trial ends today — ${minutes} min remaining`
    : `Trial ends in ${days} ${dayWord} — ${minutes} min remaining`
  const sub = finalDay
    ? 'Add minutes now to keep transcription and clip generation running.'
    : `Trials are limited to ${days} more ${dayWord}. Add minutes anytime — they never expire.`

  function dismiss() {
    try {
      localStorage.setItem(DISMISS_KEY, String(days))
    } catch {
      /* ignore — dismissal is best-effort */
    }
    setDismissedDay(days)
  }

  return (
    <div
      role="status"
      aria-live="polite"
      className={`mb-6 flex items-center justify-between gap-4 rounded-md border bg-surface px-5 py-4 ${
        finalDay ? 'border-warning-border' : 'border-default'
      }`}
    >
      <div>
        <strong className="block text-sm text-fg">{headline}</strong>
        <span className="text-xs text-muted">{sub}</span>
      </div>
      <div className="flex items-center gap-2">
        <Link to="/pricing">
          <Button size="sm">Add minutes</Button>
        </Link>
        {!finalDay && (
          <button
            type="button"
            onClick={dismiss}
            aria-label="Dismiss"
            className="px-1 text-lg leading-none text-subtle hover:text-fg"
          >
            ×
          </button>
        )}
      </div>
    </div>
  )
}

// Pre-action low-balance warning, surfaced above the video table (where the
// Queue / Generate actions live) so the creator sees it before clicking.
export function LowBalanceWarning({ balance }: { balance: Balance | null }) {
  if (!balance?.low_balance) return null
  return (
    <div
      role="status"
      className="mb-4 rounded-md border border-warning-border bg-[color:var(--color-warning-soft)] px-4 py-3 text-sm text-fg"
    >
      Low balance — <strong className="font-mono">{balance.minutes_balance} min</strong> left.{' '}
      <Link to="/pricing" className="text-accent hover:text-accent-hover">
        Add minutes
      </Link>{' '}
      before queuing more clips.
    </div>
  )
}
