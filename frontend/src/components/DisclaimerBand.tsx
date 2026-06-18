import type { ReactNode } from 'react'

// Honesty band — a CLAUDE.md hard constraint: AutoClip estimates fit with a
// creator's style and audience, it never promises virality. The copy is
// page-specific (the brief vs. the assistant word it differently), so each page
// supplies its own text; this component owns only the shared styling so the
// structural honesty test has one consistent target across the SPA.
export function DisclaimerBand({ children }: { children: ReactNode }) {
  return (
    <div className="border-b border-default bg-surface px-6 py-2 text-center text-xs text-muted">
      {children}
    </div>
  )
}
