import { useState } from 'react'
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

// Single source of link markup, reused by the desktop row and the mobile panel.
// `onNavigate` lets the mobile panel close itself when a destination is chosen.
function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <>
      {LINKS.map((l) =>
        l.external ? (
          <a key={l.href} href={l.href} className={LINK_BASE} onClick={onNavigate}>
            {l.label}
          </a>
        ) : (
          <NavLink
            key={l.href}
            to={l.href}
            onClick={onNavigate}
            className={({ isActive }) => cn(LINK_BASE, isActive && LINK_ACTIVE)}
          >
            {l.label}
          </NavLink>
        ),
      )}
    </>
  )
}

function BalancePill({ balance }: { balance: Balance }) {
  return (
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
  )
}

export function Nav({ user, balance }: { user: CurrentUser | null; balance: Balance | null }) {
  const [open, setOpen] = useState(false)

  return (
    <nav className="sticky top-0 z-40 border-b border-default bg-bg/80 shadow-sm backdrop-blur-md">
      <div className="flex items-center gap-6 px-6 py-3">
        <a href="/" className="font-display text-md font-semibold tracking-tight text-fg hover:text-fg">
          AutoClip
        </a>
        {/* Desktop link row — collapses into the hamburger panel below 640px. */}
        <div className="hidden items-center gap-1 text-small sm:flex">
          <NavLinks />
        </div>
        <span className="flex-1" />
        {user && (
          <span className="hidden font-mono text-xs text-subtle sm:inline">
            {user.channel_title || user.email}
          </span>
        )}
        {balance && <BalancePill balance={balance} />}
        <button
          onClick={logout}
          className="hidden rounded-sm px-2 py-1 text-small text-muted transition-colors duration-fast hover:bg-elevated hover:text-fg sm:block"
        >
          Logout
        </button>
        {/* Mobile menu toggle — only rendered below 640px. */}
        <button
          type="button"
          aria-label={open ? 'Close menu' : 'Open menu'}
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
          className="rounded-sm px-2 py-1 text-md text-muted transition-colors duration-fast hover:bg-elevated hover:text-fg sm:hidden"
        >
          {open ? '✕' : '☰'}
        </button>
      </div>

      {/* Mobile panel — links + channel + Logout, shown only when toggled open. */}
      {open && (
        <div className="border-t border-default px-6 py-3 sm:hidden">
          <div className="flex flex-col gap-1 text-small">
            <NavLinks onNavigate={() => setOpen(false)} />
          </div>
          <div className="mt-3 flex items-center justify-between border-t border-default pt-3">
            {user && (
              <span className="font-mono text-xs text-subtle">
                {user.channel_title || user.email}
              </span>
            )}
            <button
              onClick={logout}
              className="rounded-sm px-2 py-1 text-small text-muted transition-colors duration-fast hover:bg-elevated hover:text-fg"
            >
              Logout
            </button>
          </div>
        </div>
      )}
    </nav>
  )
}
