import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { FitBadge } from '@/components/ui/fit-badge'
import { fitTier } from '@/lib/fit'
import type {
  CaptionHooksResponse,
  ClipExplanationResponse,
  ReviewClip,
  TitleSuggestionsResponse,
} from '@/types'

// ── CopyButton — click-to-copy affordance ─────────────────────────────────────

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  function handleCopy() {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }
  return (
    <button
      onClick={handleCopy}
      className="ml-2 rounded px-1.5 py-0.5 text-xs text-muted hover:text-fg transition-colors"
      aria-label="Copy to clipboard"
    >
      {copied ? '✓ Copied' : 'Copy'}
    </button>
  )
}

// ── TitleSuggestionsCard (Issue 322) ──────────────────────────────────────────

function TitleSuggestionsCard({ clipId }: { clipId: string }) {
  const [open, setOpen] = useState(false)
  const mutation = useMutation({
    mutationFn: () =>
      api<TitleSuggestionsResponse>(`/clips/${clipId}/title-suggestions`, { method: 'POST' }),
  })

  if (!open) {
    return (
      <button
        onClick={() => { setOpen(true); mutation.mutate() }}
        className="mt-2 text-xs text-accent-text underline-offset-2 hover:underline"
      >
        Suggest titles / rewrite hook
      </button>
    )
  }

  return (
    <div className="mt-3 rounded-md border border-default bg-surface p-3 text-xs">
      <div className="mb-2 font-semibold text-fg">AI title suggestions</div>
      {mutation.isPending && <p className="text-muted">Generating…</p>}
      {mutation.isError && (
        <p className="text-error">Could not generate suggestions. Try again.</p>
      )}
      {mutation.data && (
        <>
          <p className="mb-2 text-subtle italic">{mutation.data.disclaimer}</p>
          <ul className="space-y-1.5">
            {mutation.data.titles.map((t, i) => (
              <li key={i} className="flex items-start gap-1">
                <span className="shrink-0 font-mono text-accent-text">
                  {t.ctr_signal === 'up' ? '↑' : t.ctr_signal === 'down' ? '↓' : '–'}
                </span>
                <span className="flex-1 text-fg">{t.title}</span>
                <CopyButton text={t.title} />
              </li>
            ))}
          </ul>
          {mutation.data.hook_rewrites.length > 0 && (
            <>
              <div className="mt-3 mb-1 font-semibold text-fg">Hook rewrites</div>
              <ul className="space-y-1.5">
                {mutation.data.hook_rewrites.map((h, i) => (
                  <li key={i} className="flex items-start gap-1">
                    <span className="flex-1 text-fg">{h.rewrite}</span>
                    <CopyButton text={h.rewrite} />
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </div>
  )
}

// ── CaptionHooksCard (Issue 323) ──────────────────────────────────────────────

function CaptionHooksCard({ clipId }: { clipId: string }) {
  const [open, setOpen] = useState(false)
  const mutation = useMutation({
    mutationFn: () =>
      api<CaptionHooksResponse>(`/clips/${clipId}/caption-hooks`, { method: 'POST' }),
  })

  if (!open) {
    return (
      <button
        onClick={() => { setOpen(true); mutation.mutate() }}
        className="mt-1 text-xs text-accent-text underline-offset-2 hover:underline"
      >
        Suggest caption / overlay text
      </button>
    )
  }

  return (
    <div className="mt-3 rounded-md border border-default bg-surface p-3 text-xs">
      <div className="mb-2 font-semibold text-fg">Caption hook suggestions</div>
      {mutation.isPending && <p className="text-muted">Generating…</p>}
      {mutation.isError && (
        <p className="text-error">Could not generate suggestions. Try again.</p>
      )}
      {mutation.data && (
        <>
          <p className="mb-2 text-subtle italic">{mutation.data.disclaimer}</p>
          <ul className="space-y-1.5">
            {mutation.data.options.map((o, i) => (
              <li key={i} className="flex items-start gap-1">
                <span className="flex-1 font-semibold text-fg">{o.text}</span>
                <CopyButton text={o.text} />
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

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
        <p className="text-error">Could not load explanation. Try again.</p>
      )}
      {mutation.data && (
        <>
          <p className="mb-2 leading-relaxed text-fg">{mutation.data.explanation}</p>
          <p className="mb-1 text-subtle">
            Principle: <span className="font-mono text-accent-text">{mutation.data.cited_principle}</span>
          </p>
          <p className="text-subtle italic">{mutation.data.disclaimer}</p>
        </>
      )}
    </div>
  )
}

// ── WhyThisClip (Issue 94 + 322 + 323 + 325) ─────────────────────────────────

// Issue 94 transparency: the named principle + Claude's reasoning + score/timing
// the engine cited. The honest fit tier leads; the raw score stays below as the
// transparency detail (fit estimate, not a promise).
// Issues 322/323/325 add on-demand suggestion cards (lazy — no request until clicked).
export function WhyThisClip({ clip }: { clip: ReviewClip }) {
  const setupStart = clip.setup_start_s ?? clip.start_s
  return (
    <div className="text-sm">
      <div className="mb-3 flex items-center justify-between gap-3 border-b border-default pb-2">
        <span className="font-mono text-xs text-accent-text">[principle] {clip.principle || '—'}</span>
        <FitBadge tier={fitTier(clip.score)} />
      </div>
      <div className="leading-relaxed text-fg">
        {clip.reasoning ||
          'No reasoning recorded for this clip. The scoring engine still ranked it — the explanation is just not on file.'}
      </div>
      <div className="mt-3 flex justify-between border-t border-default pt-3 font-mono text-xs text-subtle">
        <span>Score (fit estimate, not a guarantee)</span>
        <strong className="text-fg">{clip.score != null ? clip.score.toFixed(2) : '—'}</strong>
      </div>
      <div className="flex justify-between font-mono text-xs text-subtle">
        <span>Setup → peak → end</span>
        <strong className="text-fg">
          {setupStart.toFixed(1)}s → {(clip.peak_s ?? clip.start_s).toFixed(1)}s →{' '}
          {clip.end_s.toFixed(1)}s
        </strong>
      </div>

      {/* Issue 325 — expandable Why-This-Clip narrative */}
      <ExplainClipCard clipId={clip.id} />

      {/* Issues 322/323 — on-demand title + caption suggestions */}
      <div className="mt-3 border-t border-default pt-3">
        <TitleSuggestionsCard clipId={clip.id} />
        <CaptionHooksCard clipId={clip.id} />
      </div>
    </div>
  )
}
