import { cn } from '@/lib/utils'
import { api } from '@/lib/api'
import type { Balance, CurrentUser } from '@/types'

// Links point at the existing vanilla pages during the incremental migration;
// only the ported page (Profile) targets the SPA route under /app.
const LINKS = [
  { label: 'Dashboard', href: '/' },
  { label: 'Review', href: '/static/review.html' },
  { label: 'Insights', href: '/static/insights.html' },
  { label: 'Profile', href: '/app/profile', active: true },
  { label: 'Assistant', href: '/app/chat' },
  { label: 'Analyze', href: '/static/analysis.html' },
  { label: 'Pricing', href: '/static/pricing.html' },
]

async function logout() {
  await api('/auth/logout', { method: 'POST', redirectOn401: false }).catch(() => {})
  window.location.href = '/static/login.html'
}

export function Nav({ user, balance }: { user: CurrentUser | null; balance: Balance | null }) {
  return (
    <nav className="flex items-center gap-6 border-b border-default bg-bg px-6 py-3">
      <a href="/" className="font-semibold tracking-tight text-fg hover:text-fg">
        AutoClip
      </a>
      <div className="flex items-center gap-4 text-sm">
        {LINKS.map((l) => (
          <a
            key={l.href}
            href={l.href}
            className={cn(
              'text-muted transition-colors hover:text-fg',
              l.active && 'text-fg',
            )}
          >
            {l.label}
          </a>
        ))}
      </div>
      <span className="flex-1" />
      {user && (
        <span className="font-mono text-xs text-subtle">
          {user.channel_title || user.email}
        </span>
      )}
      {balance && (
        <span
          className={cn(
            'rounded-sm px-2 py-0.5 text-xs',
            balance.low_balance ? 'bg-elevated text-warning' : 'text-muted',
          )}
        >
          {balance.minutes_balance} min
        </span>
      )}
      <button onClick={logout} className="text-xs text-muted hover:text-fg">
        Logout
      </button>
    </nav>
  )
}
