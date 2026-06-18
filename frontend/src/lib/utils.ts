import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

// shadcn-convention class combiner: merge conditional classes and de-dupe
// conflicting Tailwind utilities (last wins).
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}

// Relative "time ago" string — ported from profile.html `_relativeTime`.
export function relativeTime(iso: string | null): string {
  if (!iso) return 'Never'
  const then = new Date(iso).getTime()
  const diffS = Math.max(0, Math.floor((Date.now() - then) / 1000))
  if (diffS < 60) return 'Just now'
  if (diffS < 3600) return `${Math.floor(diffS / 60)}m ago`
  if (diffS < 86400) return `${Math.floor(diffS / 3600)}h ago`
  const days = Math.floor(diffS / 86400)
  if (days < 30) return `${days}d ago`
  const months = Math.floor(days / 30)
  if (months < 12) return `${months}mo ago`
  return `${Math.floor(months / 12)}y ago`
}

export function parseCsv(s: string): string[] {
  return (s || '')
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean)
}
