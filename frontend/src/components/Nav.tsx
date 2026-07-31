import { Fragment, useState } from 'react'
import { Link, NavLink } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { api } from '@/lib/api'
import type { Balance, CurrentUser } from '@/types'

// Issue 355 finding 1: nav vocabulary has to match what each page calls itself,
// or a first-run creator can't map a label to a destination. Every label below
// is the page's own h1 (or gained a matching kicker on the page), and every entry
// carries a one-line "what is this" — desktop as a tooltip, mobile as a visible
// sub-line. `group` splits the doing-work items from the account items; the flat
// 9-item bar interleaved them with no signal that they were different kinds of
// thing. The migration-era `external` flag is gone: every page is an SPA route.
interface NavItem {
  label: string
  href: string
  blurb: string
  group: 'work' | 'account'
}

const LINKS: NavItem[] = [
  {
    label: 'Videos',
    href: '/dashboard',
    blurb: "Everything you've uploaded, and what's been clipped",
    group: 'work',
  },
  {
    label: 'Review',
    href: '/review',
    blurb: 'Keep or drop each clip the engine argued for',
    group: 'work',
  },
  {
    label: 'Editor',
    href: '/editor',
    blurb: 'Fine-tune captions, trims, and pacing on one clip',
    group: 'work',
  },
  {
    label: 'Insights',
    href: '/insights',
    blurb: "Your channel read back to you — what's working and what changed",
    group: 'work',
  },
  // No 'Analyze' entry (Issue 355 finding 2): reaching /analysis from the nav
  // showed only a URL box + free-text question, because its four generators are
  // gated on ?video_id= — a strictly worse Assistant. The route stays live and
  // is reached from a video row or the AskSurfaceTabs row on these three pages.
  {
    label: 'Assistant',
    href: '/chat',
    blurb: 'Ask anything about your channel, in your own words',
    group: 'work',
  },
  {
    label: 'Channel',
    href: '/profile',
    blurb: 'Your Creator DNA, identity, and saved insights',
    group: 'account',
  },
  {
    label: 'Settings',
    href: '/settings',
    blurb: 'How clips get produced, published, and notified',
    group: 'account',
  },
  {
    label: 'Pricing',
    href: '/pricing',
    blurb: 'Buy minutes — they never expire',
    group: 'account',
  },
]

// Pill nav: padding is identical for active/inactive so the active state never
// shifts layout. Active = accent-soft fill + accent text (the design system's
// selected-surface treatment); inactive hovers to an elevated surface.
const LINK_BASE =
  'rounded-sm px-2.5 py-1 text-muted transition-colors duration-fast ease-standard hover:bg-elevated hover:text-fg'
const LINK_ACTIVE = 'bg-accent-soft text-accent-text hover:bg-accent-soft hover:text-accent-text'

async function logout() {
  await api('/auth/logout', { method: 'POST', redirectOn401: false }).catch(() => {})
  window.location.href = '/app/login'
}

// Desktop row: one line, with a hairline divider where the work items end and
// the account items begin. Account items sit a shade quieter so the eye lands on
// the work group first.
function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <>
      {LINKS.map((l, i) => (
        <Fragment key={l.href}>
          {l.group === 'account' && LINKS[i - 1]?.group === 'work' && (
            <span aria-hidden="true" className="mx-1 h-4 w-px bg-[color:var(--color-border)]" />
          )}
          <NavLink
            to={l.href}
            title={l.blurb}
            onClick={onNavigate}
            className={({ isActive }) =>
              cn(LINK_BASE, l.group === 'account' && 'text-subtle', isActive && LINK_ACTIVE)
            }
          >
            {l.label}
          </NavLink>
        </Fragment>
      ))}
    </>
  )
}

// Mobile panel: the tooltip has nowhere to live on touch, so the blurb becomes a
// visible sub-line — which is where a first-run creator most needs it.
function NavLinksStacked({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <>
      {LINKS.map((l) => (
        <NavLink
          key={l.href}
          to={l.href}
          onClick={onNavigate}
          className={({ isActive }) =>
            cn('rounded-sm px-2.5 py-1.5', isActive ? LINK_ACTIVE : 'hover:bg-elevated')
          }
        >
          <span className="block text-small text-fg">{l.label}</span>
          <span className="block text-xs text-subtle">{l.blurb}</span>
        </NavLink>
      ))}
    </>
  )
}

// Links to Pricing: "how many minutes do I have left" and "get more" are the
// same thought, and the adjacency is the category convention (Issue 355).
function BalancePill({ balance }: { balance: Balance }) {
  return (
    <Link
      to="/pricing"
      title="Add minutes"
      className={cn(
        'rounded-full px-2.5 py-0.5 font-mono text-small',
        balance.low_balance
          ? 'bg-[color:var(--color-warning-soft)] text-warning'
          : 'bg-elevated text-muted',
      )}
    >
      {balance.minutes_balance} min
    </Link>
  )
}

export function Nav({ user, balance }: { user: CurrentUser | null; balance: Balance | null }) {
  const [open, setOpen] = useState(false)

  return (
    <nav
      aria-label="Primary"
      className="sticky top-0 z-40 border-b border-default bg-bg/80 shadow-sm backdrop-blur-md"
    >
      <div className="flex items-center gap-6 px-6 py-3">
        {/* Signed in, the wordmark is "home" and home is the app — a bare href="/"
            left the SPA for the marketing landing, which the server then bounced
            straight back to /app/dashboard (Issue 355). */}
        {user ? (
          <Link
            to="/dashboard"
            className="font-display text-md font-semibold tracking-tight text-fg hover:text-fg"
          >
            AutoClip
          </Link>
        ) : (
          <a
            href="/"
            className="font-display text-md font-semibold tracking-tight text-fg hover:text-fg"
          >
            AutoClip
          </a>
        )}
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
            <NavLinksStacked onNavigate={() => setOpen(false)} />
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
