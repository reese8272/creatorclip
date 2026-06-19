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

// Pill nav: padding is identical for active/inactive so the active state never
// shifts layout. Active = accent-soft fill + accent text (the design system's
// selected-surface treatment); inactive hovers to an elevated surface.
const LINK_BASE =
  'rounded-sm px-2.5 py-1 text-muted transition-colors duration-fast ease-standard hover:bg-elevated hover:text-fg'
const LINK_ACTIVE = 'bg-accent-soft text-accent hover:bg-accent-soft hover:text-accent'

async function logout() {
  await api('/auth/logout', { method: 'POST', redirectOn401: false }).catch(() => {})
  window.location.href = '/app/login'
}

export function Nav({ user, balance }: { user: CurrentUser | null; balance: Balance | null }) {
  return (
    <nav className="sticky top-0 z-40 flex items-center gap-6 border-b border-default bg-bg/80 px-6 py-3 shadow-sm backdrop-blur-md">
      <a href="/" className="font-display text-md font-semibold tracking-tight text-fg hover:text-fg">
        AutoClip
      </a>
      <div className="flex items-center gap-1 text-small">
        {LINKS.map((l) =>
          l.external ? (
            <a key={l.href} href={l.href} className={LINK_BASE}>
              {l.label}
            </a>
          ) : (
            <NavLink
              key={l.href}
              to={l.href}
              className={({ isActive }) => cn(LINK_BASE, isActive && LINK_ACTIVE)}
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
            'rounded-full px-2.5 py-0.5 font-mono text-small',
            balance.low_balance
              ? 'bg-[color:var(--color-warning-soft)] text-warning'
              : 'bg-elevated text-muted',
          )}
        >
          {balance.minutes_balance} min
        </span>
      )}
      <button
        onClick={logout}
        className="rounded-sm px-2 py-1 text-small text-muted transition-colors duration-fast hover:bg-elevated hover:text-fg"
      >
        Logout
      </button>
    </nav>
  )
}
