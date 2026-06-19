import type { FitTier } from '@/components/ui/fit-badge'

// Map a clip's 0..1 engine fit score (clip_engine/scoring.py: 0.0 = poor fit,
// 1.0 = excellent fit for THIS creator) to a confidence tier. Tier thresholds
// are a product decision (docs/UI.md "Confidence badges"; docs/DECISIONS
// 2026-06-19) — kept here as the single source of truth so they're tuned in one
// place, never inlined per component. Raw scores are never surfaced as the
// headline; the tier is. (The Review detail panel still shows the number for
// Issue 94 transparency.)
//   strong    ≥ 0.70  high-confidence fit
//   moderate  ≥ 0.45  plausible fit (the engine's 0.5 default lands here)
//   else       exploratory
export function fitTier(score: number | null | undefined): FitTier {
  if (score == null) return 'exploratory'
  if (score >= 0.7) return 'strong'
  if (score >= 0.45) return 'moderate'
  return 'exploratory'
}
