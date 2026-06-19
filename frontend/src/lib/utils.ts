import { clsx, type ClassValue } from 'clsx'
import { extendTailwindMerge } from 'tailwind-merge'

// The design system defines custom text-size utilities (text-body, text-h1,
// text-small, text-md, text-2xs, …) via @theme. Stock tailwind-merge doesn't know
// these are FONT SIZES, so it conflated them with custom text-COLOR utilities
// (text-bg, text-on-accent) in the same conflict group and dropped the color —
// filled buttons silently lost their text color and inherited the page fg, failing
// WCAG contrast (Issue 165, DECISIONS 2026-06-19). Registering the size scale keeps
// size and color in separate groups so both survive a merge.
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      'font-size': [
        {
          text: [
            '2xs', 'xs', 'sm', 'small', 'base', 'body', 'md', 'lg', 'xl', '2xl',
            'h1', 'h2', 'h3', 'label', 'mono',
          ],
        },
      ],
    },
  },
})

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
