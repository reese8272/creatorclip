import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardBody, CardHeader } from '@/components/ui/card'
import { useState } from 'react'
import { useAuth } from '@/hooks/useAuth'

// Why this component exists.
//
// While the Google OAuth app is in Testing publishing status, Google expires
// every refresh token after 7 days, so EVERY user must reconnect weekly
// (docs/GO_LIVE.md; Issue #29 is the only real fix). The app already told people
// to do this — worker/tasks.py emits a `reauth_required` notification titled
// "Reconnect your YouTube account" and notify/copy.py sends the matching email —
// but both linked to /app/profile, which has been read-only since Issue 308 and
// contained no reconnect control. Settings only offers PublishingSection, which
// is the separate youtube.upload grant. The only working path was typing
// /auth/login by hand.
//
// So the most frequently-hit recovery flow in the beta dead-ended. This card is
// the destination those notifications assume exists.

const DAY_MS = 86_400_000

/** Days from `now` until `iso`, or null when absent/unparseable. Negative once expired.
 *
 * Ceil, not floor: a token with 23 hours left has 1 day remaining, not 0. With
 * floor, a timestamp exactly 24h out reads as "expires today" the instant any
 * time elapses between the API response and the render.
 *
 * `now` is a parameter rather than a `Date.now()` call so this stays pure —
 * reading the clock during render trips react-hooks/purity, and the caller
 * captures the timestamp once at mount.
 */
function daysUntil(iso: string | null | undefined, now: number): number | null {
  if (!iso) return null
  const ms = new Date(iso).getTime()
  if (Number.isNaN(ms)) return null
  return Math.ceil((ms - now) / DAY_MS)
}

export function YouTubeConnectionCard() {
  const { user, loading } = useAuth()
  // Captured once at mount so the render stays pure (react-hooks/purity).
  const [now] = useState(() => Date.now())

  // While /auth/me is in flight `user` is null, which would otherwise render the
  // red "Disconnected" state on every single page load before settling. Hold a
  // neutral state instead — a false disconnection warning is worse than a beat
  // of nothing, especially on the page the reauth notification links to.
  if (loading) {
    return (
      <Card>
        <CardHeader title="YouTube connection" aside={<Badge variant="muted">Checking</Badge>} />
        <CardBody>
          <p className="text-small text-muted">Checking your YouTube connection…</p>
        </CardBody>
      </Card>
    )
  }

  const connected = user?.youtube_connected ?? false
  const daysLeft = daysUntil(user?.youtube_expires_at, now)
  // Google's Testing-mode refresh tokens last 7 days; warn inside the last two
  // so a creator can reconnect before a background analytics refresh fails.
  const expiringSoon = connected && daysLeft !== null && daysLeft <= 2

  const state = !connected
    ? {
        badge: <Badge variant="danger">Disconnected</Badge>,
        detail:
          'AutoClip can’t reach your channel. Analytics, catalog sync, and Creator DNA are paused until you reconnect.',
      }
    : expiringSoon
      ? {
          badge: <Badge variant="warning">Expiring</Badge>,
          detail:
            daysLeft !== null && daysLeft <= 0
              ? 'Your connection expires today. Reconnect to keep analytics and DNA up to date.'
              : `Your connection expires in ${daysLeft} day${daysLeft === 1 ? '' : 's'}. Reconnect to keep analytics and DNA up to date.`,
        }
      : {
          badge: <Badge variant="success">Connected</Badge>,
          detail:
            'Google currently expires this connection every 7 days while AutoClip is in review, so you’ll need to reconnect weekly.',
        }

  return (
    <Card>
      <CardHeader
        title="YouTube connection"
        // Right-aligned status, matching DnaCard's provenance slot.
        aside={state.badge}
      />
      <CardBody>
        <p className="text-small text-muted">{state.detail}</p>
        <div className="mt-4">
          {/* A plain anchor, not react-router: /auth/login is a server route that
              302s to Google. A <Link> would try to resolve it client-side. */}
          <a href="/auth/login">
            <Button variant={connected && !expiringSoon ? 'secondary' : 'primary'}>
              {connected ? 'Reconnect YouTube' : 'Connect YouTube'}
            </Button>
          </a>
        </div>
      </CardBody>
    </Card>
  )
}
