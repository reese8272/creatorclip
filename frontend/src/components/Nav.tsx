import { NavLink } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { api } from '@/lib/api'
import type { Balance, CurrentUser } from '@/types'

// During the incremental migration, links split two ways: ported pages are
// SPA routes (client-side NavLink, auto active state); un-ported pages are still
// vanilla files reached by a full navigation (`external` → plain <a>). As each
// page ports, flip its entry's `external` to false and point `href` at the
// /app route.
interface NavItem {
  label: string
  href: string
  external: boolean
}

const LINKS: NavItem[] = [
  { label: 'Dashboard', href: '/dashboard', external: false },
  { label: 'Review', href: '/review', external: false },
  { label: 'Insights', href: '/insights', external: false },
  { label: 'Profile', href: '/profile', external: false },
  { label: 'Assistant', href: '/chat', external: false },
  { label: 'Analyze', href: '/analysis', external: false },
  { label: 'Pricing', href: '/pricing', external: false },
]

const LINK_BASE = 'text-muted transition-colors hover:text-fg'

async function logout() {
  await api('/auth/logout', { method: 'POST', redirectOn401: false }).catch(() => {})
  window.location.href = '/app/login'
}

export function Nav({ user, balance }: { user: CurrentUser | null; balance: Balance | null }) {
  return (
    <nav className="flex items-center gap-6 border-b border-default bg-bg px-6 py-3">
      <a href="/" className="font-semibold tracking-tight text-fg hover:text-fg">
        AutoClip
      </a>
      <div className="flex items-center gap-4 text-sm">
        {LINKS.map((l) =>
          l.external ? (
            <a key={l.href} href={l.href} className={LINK_BASE}>
              {l.label}
            </a>
          ) : (
            <NavLink
              key={l.href}
              to={l.href}
              className={({ isActive }) => cn(LINK_BASE, isActive && 'text-fg')}
            >
              {l.label}
            </NavLink>
          ),
        )}
      </div>
      <span className="flex-1" />
      {user && (
        <span className="font-mono text-xs text-subtle">{user.channel_title || user.email}</span>
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
