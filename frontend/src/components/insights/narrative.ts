// Per-performer static "why" narrative — derived from the backend-computed score
// components (0–100, where 50 = channel average). Static copy from the existing
// payload — no extra LLM call. Lives in its own module (not InsightsNarrative.tsx)
// so that component file only exports components (react-refresh/only-export-components).

export function deriveWhyNarrative(
  kind: 'top' | 'bottom',
  components:
    | { retention: number | null; engagement: number | null; views: number | null }
    | null
    | undefined,
): string {
  if (!components) {
    return kind === 'top'
      ? 'Outperformed your channel average across measured signals.'
      : 'Underperformed vs your channel average across measured signals.'
  }

  const { retention, engagement, views } = components
  const signals: string[] = []

  if (retention != null) {
    if (retention >= 65) signals.push('strong watch-through')
    else if (retention <= 35) signals.push('lower watch-through than usual')
  }
  if (engagement != null) {
    if (engagement >= 65) signals.push('above-average likes + comments')
    else if (engagement <= 35) signals.push('below-average engagement')
  }
  if (views != null) {
    if (views >= 65) signals.push('above-average reach')
    else if (views <= 35) signals.push('below-average reach')
  }

  if (signals.length === 0) {
    return kind === 'top'
      ? 'Near-average on all measured signals — consistent with your DNA baseline.'
      : 'Near-average on all measured signals — no single factor stands out.'
  }

  if (kind === 'top') {
    return `Drove ${signals.join(' and ')} relative to your channel average.`
  }
  return `Showed ${signals.join(' and ')} relative to your channel average.`
}
