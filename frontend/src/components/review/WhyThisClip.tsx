import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { api } from '@/lib/api'
import {
  DESCRIPTION_MAX_BYTES,
  TITLE_MAX_CHARS,
  useApplyClipMetadata,
  utf8ByteLength,
} from '@/hooks/useApplyClipMetadata'
import { FitBadge } from '@/components/ui/fit-badge'
import { fitTier } from '@/lib/fit'
import type {
  CaptionHooksResponse,
  ClipExplanationResponse,
  ReviewClip,
  TitleSuggestionsResponse,
} from '@/types'
import { Check } from '@/components/ui/icon'
import { ICON_INLINE, ICON_SIZE } from '@/components/ui/iconSizes'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Disclosure } from '@/components/ui/disclosure'

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
      {copied ? (
        <>
          <Check className={`${ICON_SIZE.xs} ${ICON_INLINE}`} aria-hidden="true" /> Copied
        </>
      ) : (
        'Copy'
      )}
    </button>
  )
}

// ── ApplyButton — apply a suggestion as the clip's publish metadata ───────────

function ApplyButton({
  applied,
  pending,
  onApply,
}: {
  applied: boolean
  pending: boolean
  onApply: () => void
}) {
  if (applied)
    return (
      <span className="ml-1 inline-flex shrink-0 items-center gap-1 px-1.5 py-0.5 text-xs text-success">
        <Check className={ICON_SIZE.xs} aria-hidden="true" /> Applied
      </span>
    )
  return (
    <button
      onClick={onApply}
      disabled={pending}
      className="ml-1 shrink-0 rounded px-1.5 py-0.5 text-xs text-accent-text hover:underline disabled:opacity-50"
    >
      {pending ? 'Applying…' : 'Apply'}
    </button>
  )
}

// ── TitleSuggestionsCard (Issue 322) ──────────────────────────────────────────

function TitleSuggestionsCard({ clip }: { clip: ReviewClip }) {
  const [open, setOpen] = useState(false)
  const [applyError, setApplyError] = useState('')
  const mutation = useMutation({
    mutationFn: () =>
      api<TitleSuggestionsResponse>(`/clips/${clip.id}/title-suggestions`, { method: 'POST' }),
  })
  const apply = useApplyClipMetadata(clip)

  function applyTitle(title: string) {
    if (title.length > TITLE_MAX_CHARS) {
      setApplyError(`Titles are capped at ${TITLE_MAX_CHARS} characters on YouTube.`)
      return
    }
    setApplyError('')
    apply.mutate({ applied_title: title })
  }

  function applyHook(rewrite: string) {
    if (utf8ByteLength(rewrite) > DESCRIPTION_MAX_BYTES) {
      setApplyError(`Descriptions are capped at ${DESCRIPTION_MAX_BYTES} bytes on YouTube.`)
      return
    }
    setApplyError('')
    apply.mutate({ applied_description: rewrite })
  }

  if (!open) {
    return (
      <Button variant="ghost" size="sm" onClick={() => { setOpen(true); mutation.mutate() }}>
        Suggest titles / rewrite hook
      </Button>
    )
  }

  return (
    <div className="mt-3 basis-full rounded-md border border-default bg-surface p-3 text-xs">
      <div className="mb-2 font-semibold text-fg">AI title suggestions</div>
      {mutation.isPending && <p className="text-muted">Generating…</p>}
      {mutation.isError && (
        <p className="text-danger">Could not generate suggestions. Try again.</p>
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
                <ApplyButton
                  applied={clip.applied_title === t.title}
                  pending={apply.isPending}
                  onApply={() => applyTitle(t.title)}
                />
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
                    <ApplyButton
                      applied={clip.applied_description === h.rewrite}
                      pending={apply.isPending}
                      onApply={() => applyHook(h.rewrite)}
                    />
                    <CopyButton text={h.rewrite} />
                  </li>
                ))}
              </ul>
            </>
          )}
          {applyError && <p className="mt-2 text-danger">{applyError}</p>}
          {apply.isError && <p className="mt-2 text-danger">Could not apply — try again.</p>}
        </>
      )}
    </div>
  )
}

// ── CaptionHooksCard (Issue 323) ──────────────────────────────────────────────

function CaptionHooksCard({ clip }: { clip: ReviewClip }) {
  const [open, setOpen] = useState(false)
  const [applyError, setApplyError] = useState('')
  const mutation = useMutation({
    mutationFn: () =>
      api<CaptionHooksResponse>(`/clips/${clip.id}/caption-hooks`, { method: 'POST' }),
  })
  const apply = useApplyClipMetadata(clip)

  function applyCaption(text: string) {
    if (utf8ByteLength(text) > DESCRIPTION_MAX_BYTES) {
      setApplyError(`Descriptions are capped at ${DESCRIPTION_MAX_BYTES} bytes on YouTube.`)
      return
    }
    setApplyError('')
    apply.mutate({ applied_description: text })
  }

  if (!open) {
    return (
      <Button variant="ghost" size="sm" onClick={() => { setOpen(true); mutation.mutate() }}>
        Suggest caption / overlay text
      </Button>
    )
  }

  return (
    <div className="mt-3 basis-full rounded-md border border-default bg-surface p-3 text-xs">
      <div className="mb-2 font-semibold text-fg">Caption hook suggestions</div>
      {mutation.isPending && <p className="text-muted">Generating…</p>}
      {mutation.isError && (
        <p className="text-danger">Could not generate suggestions. Try again.</p>
      )}
      {mutation.data && (
        <>
          <p className="mb-2 text-subtle italic">{mutation.data.disclaimer}</p>
          <ul className="space-y-1.5">
            {mutation.data.options.map((o, i) => (
              <li key={i} className="flex items-start gap-1">
                <span className="flex-1 font-semibold text-fg">{o.text}</span>
                <ApplyButton
                  applied={clip.applied_description === o.text}
                  pending={apply.isPending}
                  onApply={() => applyCaption(o.text)}
                />
                <CopyButton text={o.text} />
              </li>
            ))}
          </ul>
          {applyError && <p className="mt-2 text-danger">{applyError}</p>}
          {apply.isError && <p className="mt-2 text-danger">Could not apply — try again.</p>}
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

// ── WhyThisClip (Issue 94 + 322 + 323 + 325) ─────────────────────────────────

// Issue 94 transparency: the named principle + Claude's reasoning + score/timing
// the engine cited. The honest fit tier leads; the raw score stays below as the
// transparency detail (fit estimate, not a promise).
// Issues 322/323/325 add on-demand suggestion cards (lazy — no request until clicked).
export function WhyThisClip({ clip }: { clip: ReviewClip }) {
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
            {/* Was `[principle] Open Loop` in bracketed monospace — which reads as
                a log line, and a log line in a product surface reads as
                unfinished. The tier leads; the principle is a named badge. */}
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

      {/* Issues 322/323 — on-demand title + caption suggestions */}
      <div
        data-testid="clip-action-row"
        className="mt-3 flex flex-wrap gap-2 border-t border-default pt-3"
      >
        <TitleSuggestionsCard clip={clip} />
        <CaptionHooksCard clip={clip} />
      </div>
    </div>
  )
}
