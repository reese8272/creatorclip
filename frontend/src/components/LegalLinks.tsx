import { cn } from '@/lib/utils'

// The three legal destinations Google OAuth verification requires to be reachable
// from every surface (CLAUDE.md pre-launch list). They are server-rendered static
// pages, not SPA routes, so these stay plain <a> and never become <Link>.
const LEGAL_HREFS = [
  { href: '/static/tos.html', label: 'Terms' },
  { href: '/static/privacy.html', label: 'Privacy' },
  { href: '/static/accessibility.html', label: 'Accessibility' },
] as const

/**
 * LegalLinks — Terms · Privacy · Accessibility · © notice.
 *
 * Extracted from Footer (Issue 389) so the tool routes' docked status bar and the
 * marketing footer render the same href set from one place. The two consumers
 * differ only in surrounding chrome, so this owns the links and the copyright and
 * nothing else.
 */
export function LegalLinks({ className }: { className?: string }) {
  return (
    <span className={cn('flex items-center gap-4', className)}>
      {LEGAL_HREFS.map(({ href, label }) => (
        <a key={href} href={href} className="hover:text-fg">
          {label}
        </a>
      ))}
      <span>© AutoClip 2026</span>
    </span>
  )
}
