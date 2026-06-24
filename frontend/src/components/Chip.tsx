import type { CSSProperties } from 'react'
import { cn } from '@/lib/utils'
import type { ChipPose } from '@/components/chip/poses'

export type { ChipPose }

interface ChipProps {
  pose: ChipPose
  size?: number
  className?: string
  style?: CSSProperties
}

// Chip — the AutoClip mascot ("Chip the AI Clip Editor"). Issue 304 (design handoff).
// Sprites live in frontend/public/chip/ and Vite copies them to dist/chip/, served
// under the SPA base (/app/chip/<pose>.png). The src MUST be base-relative: a bare
// /chip/... is rooted at the domain and 404s because the SPA lives under /app/.
// import.meta.env.BASE_URL is '/app/' in the build (and always ends with '/').
// The pose registry + concept map live in ./chip/poses.
//
// Chip is decorative — it sits beside headers and inside loading chrome that
// already has a visible/textual label. Per W3C WAI (Decorative Images) the
// correct treatment is an empty alt so screen readers skip it rather than
// announcing "Chip" at every header. Deviates from the handoff's alt="Chip"
// (a11y-only; no visual change). See docs/DECISIONS.md (Issue 304).
export function Chip({ pose, size = 48, className, style }: ChipProps) {
  return (
    <img
      src={`${import.meta.env.BASE_URL}chip/chip-${pose}.png`}
      alt=""
      aria-hidden="true"
      width={size}
      height={size}
      loading="lazy"
      draggable={false}
      className={cn('object-contain select-none', className)}
      style={style}
    />
  )
}
