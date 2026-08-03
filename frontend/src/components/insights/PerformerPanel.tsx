import { useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api'
import { EmptyStatePrompt } from '@/components/EmptyStatePrompt'
import { Panel } from '@/components/insights/InsightsPanel'
import { deriveWhyNarrative } from '@/components/insights/narrative'
import type { Performer, PerformerInsight } from '@/types'
import { Select, type SelectOption } from '@/components/ui/select'
import { Star } from '@/components/ui/icon'
import { ICON_SIZE } from '@/components/ui/iconSizes'

type SortMode = 'score-desc' | 'score-asc' | 'title'

const SORT_OPTIONS: SelectOption[] = [
  { value: 'score-desc', label: 'Score: high → low' },
  { value: 'score-asc', label: 'Score: low → high' },
  { value: 'title', label: 'Title: A → Z' },
]

function sortPerformers(rows: Performer[], mode: SortMode): Performer[] {
  const sorted = rows.slice()
  if (mode === 'title') {
    return sorted.sort((a, b) =>
      String(a.title || a.youtube_video_id || '').localeCompare(
        String(b.title || b.youtube_video_id || ''),
      ),
    )
  }
  const dir = mode === 'score-asc' ? 1 : -1
  return sorted.sort((a, b) => {
    const sa = a.performance_score
    const sb = b.performance_score
    if (sa == null && sb == null) return 0
    if (sa == null) return 1 // unscored rows always sort last
    if (sb == null) return -1
    return (sa - sb) * dir
  })
}

// One performer row owns its own AI-analysis lifecycle (analyze → show → save).
function PerformerRow({ p, kind }: { p: Performer; kind: 'top' | 'bottom' }) {
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [label, setLabel] = useState('Analyze')
  const [analysis, setAnalysis] = useState<{ id: string; content: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  async function analyze() {
    setBusy(true)
    setLabel('Analyzing…')
    setError(null)
    try {
      const data = await api<PerformerInsight>('/creators/me/insights/analyze-performer', {
        method: 'POST',
        body: { video_id: p.video_id, performer_kind: kind },
      })
      setAnalysis({ id: data.id, content: data.content })
      setLabel('Analyzed')
    } catch (e) {
      setBusy(false)
      setLabel('Retry')
      setError(e instanceof ApiError ? e.message : 'Analysis failed — try again.')
    }
  }

  async function save() {
    if (!analysis) return
    await api(`/creators/me/insights/save/${analysis.id}`, { method: 'POST' }).catch(() => {})
    setSaved(true)
    queryClient.invalidateQueries({ queryKey: ['saved-insights'] })
  }

  const score = p.performance_score != null ? p.performance_score.toFixed(0) : '—'
  // Static one-line "why" derived from backend-computed score components — no LLM call.
  // On-demand deep AI analysis is still available via the Analyze button below.
  const whyNarrative = deriveWhyNarrative(kind, p.performance_score_components)
  const videoTitle = p.title || p.youtube_video_id

  return (
    <div className="grid grid-cols-[1fr_auto_auto_auto] items-start gap-3 border-b border-default py-3 last:border-b-0">
      <div className="min-w-0">
        {/* Title + deep-link to video timeline (Issue 213) */}
        <a
          href={`/app/video/${p.video_id}`}
          className="truncate block text-sm text-fg hover:text-accent-text hover:underline"
          aria-label={`View timeline for ${videoTitle}`}
        >
          {videoTitle}
        </a>
        {/* Static per-row "why" — always visible, sourced from DNA score components */}
        {p.performance_score != null && (
          <div
            className="mt-1 text-xs text-muted"
            data-testid="performer-why"
          >
            {whyNarrative}
          </div>
        )}
        {error && <div className="mt-2 text-xs text-danger">{error}</div>}
        {analysis && (
          <div className="mt-2 text-xs leading-relaxed text-muted">
            {analysis.content}{' '}
            {!saved ? (
              <button
                onClick={save}
                className="ml-1 inline-flex items-center gap-1 text-accent-text hover:text-fg"
              >
                <Star className={ICON_SIZE.xs} aria-hidden="true" /> Save
              </button>
            ) : (
              <span className="ml-1 inline-flex items-center gap-1 text-success">
                <Star className={ICON_SIZE.xs} aria-hidden="true" /> Saved
              </span>
            )}
          </div>
        )}
      </div>
      <span className="font-mono text-xs uppercase text-subtle">{p.kind}</span>
      <span className="text-right font-mono text-sm font-medium text-accent-text">{score}</span>
      <button
        onClick={analyze}
        disabled={busy && label === 'Analyzing…'}
        className="whitespace-nowrap rounded-md border border-strong px-2 py-0.5 text-xs text-muted hover:border-accent hover:text-accent-text disabled:opacity-50"
      >
        {label}
      </button>
    </div>
  )
}

export function PerformerPanel({
  kind,
  title,
  sub,
  performers,
}: {
  kind: 'top' | 'bottom'
  title: string
  sub: string
  performers: Performer[]
}) {
  const [mode, setMode] = useState<SortMode>('score-desc')
  const sorted = useMemo(() => sortPerformers(performers, mode), [performers, mode])

  const aside = (
    <div className="ml-auto flex items-center gap-2 whitespace-nowrap text-xs text-muted">
      Sort
      <Select
        value={mode}
        onValueChange={(v) => setMode(v as SortMode)}
        options={SORT_OPTIONS}
        aria-label={`Sort ${title}`}
        size="sm"
        className="w-auto"
      />
    </div>
  )

  return (
    <Panel title={title} sub={sub} aside={aside}>
      {sorted.length === 0 ? (
        <EmptyStatePrompt
          title="Build your DNA to surface this list."
          detail="Your Creator DNA is what this list is ranked against — without it there is nothing to compare your videos to."
          actionLabel="Build your DNA"
          actionTo="/onboarding"
        />
      ) : (
        sorted.map((p) => <PerformerRow key={p.video_id} p={p} kind={kind} />)
      )}
    </Panel>
  )
}
