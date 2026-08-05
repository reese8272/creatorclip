import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { FitBadge } from '@/components/ui/fit-badge'
import { fitTier } from '@/lib/fit'
import type { ClipExplanationResponse, ReviewClip } from '@/types'
import { Badge } from '@/components/ui/badge'
import { Disclosure } from '@/components/ui/disclosure'

// ── ExplainClipCard (Issue 325) ───────────────────────────────────────────────

function ExplainClipCard({ clipId }: { clipId: string }) {
  const [open, setOpen] = useState(false)
  const mutation = useMutation({
    mutationFn: () =>
      api<ClipExplanationResponse>(`/clips/${clipId}/explanation`, { method: 'POST' }),
  })

  if (!open) {
    return (
      <button
        onClick={() => { setOpen(true); mutation.mutate() }}
        className="mt-2 text-xs text-accent-text underline-offset-2 hover:underline"
        data-testid="explain-clip-trigger"
      >
        Why this clip? (detailed explanation)
      </button>
    )
  }

  return (
    <div className="mt-3 rounded-md border border-default bg-surface p-3 text-xs" data-testid="explain-clip-card">
      <div className="mb-2 font-semibold text-fg">Why this clip</div>
      {mutation.isPending && <p className="text-muted">Generating…</p>}
      {mutation.isError && (
        <p className="text-danger">Could not load explanation. Try again.</p>
      )}
      {mutation.data && (
        <>
          <p className="mb-2 leading-relaxed text-fg">{mutation.data.explanation}</p>
          <p className="mb-1 text-subtle">
            Principle: <span className="text-accent-text">{mutation.data.cited_principle}</span>
          </p>
          <p className="text-subtle italic">{mutation.data.disclaimer}</p>
        </>
      )}
    </div>
  )
}

// ── ClipCase (L26 Issue 424 — the argued half of the old WhyThisClip) ────────

// Issue 94 transparency: the named principle + Claude's reasoning + score/timing
// the engine cited. The honest fit tier leads; the raw score stays below as the
// transparency detail (fit estimate, not a promise). The metadata half —
// titles, hooks, apply/regenerate — lives in ClipMetadataPanel on the actions
// rail; this panel only argues the case.
export function ClipCase({ clip }: { clip: ReviewClip }) {
  const setupStart = clip.setup_start_s ?? clip.start_s
  // Issue 373: a creator-made selection was never engine-scored — honest
  // provenance framing instead of a principle citation + fit tier.
  const isCreatorClip = clip.origin === 'creator'
  return (
    <div className="text-sm">
      <div className="mb-3 flex items-center justify-between gap-3 border-b border-default pb-2">
        {isCreatorClip ? (
          <Badge variant="muted" casing="sentence">
            Your selection
          </Badge>
        ) : (
          <>
            {/* The tier leads; the principle is a named badge (Issue 388). */}
            <Badge
              variant="accent"
              casing="sentence"
              data-testid="principle-badge"
              title="The storytelling principle this clip was selected for."
            >
              {clip.principle || '—'}
            </Badge>
            <FitBadge tier={fitTier(clip.score)} />
          </>
        )}
      </div>
      <div className="leading-relaxed text-fg">
        {isCreatorClip
          ? 'Created by you from the source timeline — not engine-scored.'
          : clip.reasoning ||
            'No reasoning recorded for this clip. The scoring engine still ranked it — the explanation is just not on file.'}
      </div>
      {/* The raw float and the setup→peak→end readout are internal
          representations. They stay REACHABLE — the honesty constraint requires
          the estimate framing to be available, and the wording below is
          unchanged — but the fit tier in the header is what leads. */}
      <div className="mt-3 border-t border-default pt-3">
        <Disclosure summary="Scoring details">
          <div className="flex justify-between font-mono text-mono text-muted">
            <span>Score (fit estimate, not a guarantee)</span>
            <strong className="text-fg">{clip.score != null ? clip.score.toFixed(2) : '—'}</strong>
          </div>
          <div className="flex justify-between font-mono text-mono text-muted">
            <span>Setup → peak → end</span>
            <strong className="text-fg">
              {setupStart.toFixed(1)}s → {(clip.peak_s ?? clip.start_s).toFixed(1)}s →{' '}
              {clip.end_s.toFixed(1)}s
            </strong>
          </div>
        </Disclosure>
      </div>

      {/* Issue 325 — expandable Why-This-Clip narrative */}
      <ExplainClipCard clipId={clip.id} />
    </div>
  )
}
