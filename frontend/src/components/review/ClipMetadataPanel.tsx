import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { api } from '@/lib/api'
import {
  DESCRIPTION_MAX_BYTES,
  TITLE_MAX_CHARS,
  useApplyClipMetadata,
  utf8ByteLength,
} from '@/hooks/useApplyClipMetadata'
import { AppliedDescriptionField } from '@/components/review/AppliedDescriptionField'
import { AppliedTitleField } from '@/components/review/AppliedTitleField'
import type { CaptionHooksResponse, ReviewClip, TitleSuggestionsResponse } from '@/types'
import { Check } from '@/components/ui/icon'
import { ICON_INLINE, ICON_SIZE } from '@/components/ui/iconSizes'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
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

// ── RegenerateTitlesCard (Issue 322, relabeled by Issue 424) ─────────────────

function RegenerateTitlesCard({ clip }: { clip: ReviewClip }) {
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
        Regenerate titles
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

// ── RegenerateCaptionHooksCard (Issue 323, relabeled by Issue 424) ───────────

function RegenerateCaptionHooksCard({ clip }: { clip: ReviewClip }) {
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
        Regenerate caption hooks
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

// ── MetadataRow — one truncating line + state chip ───────────────────────────

function MetadataRow({
  label,
  value,
  applied,
  pending,
  onApply,
  onEdit,
}: {
  label: string
  value: string | null
  /** True when the shown value is creator-applied (vs an engine suggestion). */
  applied: boolean
  pending: boolean
  onApply: () => void
  onEdit: () => void
}) {
  return (
    // Issue 452: `items-start`, not `items-center` — once the value wraps, centering
    // would float the label and the Applied/Suggested/Edit controls against the middle
    // of a multi-line block.
    <div className="flex items-start gap-2 py-1" data-testid={`metadata-row-${label.toLowerCase()}`}>
      <span className="w-9 shrink-0 text-label uppercase tracking-[0.04em] text-subtle">
        {label}
      </span>
      {value ? (
        <>
          {/*
            Issue 452 — WRAP, do not truncate, and carry no native `title=` tooltip.
            Same reasoning as the pile rows (Issue 445, ClipPiles.tsx): a title or hook
            you are about to publish is exactly when you need to read it in full, and
            the native tooltip clipped long values just like the thing it was escaping.
            Owner decision 2026-08-12: expand to fit, no clamp toggle.
          */}
          <span className="min-w-0 flex-1 break-words text-xs text-fg">{value}</span>
          {applied ? (
            <span className="inline-flex shrink-0 items-center gap-1 text-xs text-success">
              <Check className={ICON_SIZE.xs} aria-hidden="true" /> Applied
            </span>
          ) : (
            <span className="inline-flex shrink-0 items-center gap-1 text-xs text-subtle">
              Suggested ·
              <button
                onClick={onApply}
                disabled={pending}
                className="text-accent-text hover:underline disabled:opacity-50"
              >
                {pending ? 'Applying…' : 'Apply'}
              </button>
            </span>
          )}
        </>
      ) : (
        <span className="min-w-0 flex-1 break-words text-xs text-subtle">—</span>
      )}
      <button
        onClick={onEdit}
        className="shrink-0 text-xs text-accent-text underline-offset-2 hover:underline"
        aria-label={`Edit ${label.toLowerCase()}`}
      >
        Edit
      </button>
    </div>
  )
}

// ── ClipMetadataPanel (L26 Issue 424 — the metadata half of WhyThisClip) ─────

// The clip's publish metadata, compacted: title and hook are ONE truncating row
// each, value precedence `applied_* ?? suggested_*` (creator-typed always wins;
// the engine's pipeline drafts fill in behind — Issue 417). Apply promotes a
// suggestion via the EXISTING useApplyClipMetadata PATCH; Edit opens the
// applied-field editors. The on-demand LLM endpoints live on under "More
// suggestions" as Regenerate, disclaimers intact. When no suggested_* exist
// (Track A not landed / older rows) the rows fall back to today's behavior —
// empty value, type-it-yourself — with no "Soon" placeholders.
export function ClipMetadataPanel({ clip }: { clip: ReviewClip }) {
  const apply = useApplyClipMetadata(clip)
  const [applyError, setApplyError] = useState('')
  const [editingTitle, setEditingTitle] = useState(false)
  const [editingDescription, setEditingDescription] = useState(false)

  const title = clip.applied_title ?? clip.suggested_title ?? null
  const hook = clip.applied_description ?? clip.suggested_description ?? null

  function applySuggestedTitle() {
    const suggested = clip.suggested_title
    if (!suggested) return
    if (suggested.length > TITLE_MAX_CHARS) {
      setApplyError(`Titles are capped at ${TITLE_MAX_CHARS} characters on YouTube.`)
      return
    }
    setApplyError('')
    apply.mutate({ applied_title: suggested })
  }

  function applySuggestedDescription(text: string | null | undefined) {
    if (!text) return
    if (utf8ByteLength(text) > DESCRIPTION_MAX_BYTES) {
      setApplyError(`Descriptions are capped at ${DESCRIPTION_MAX_BYTES} bytes on YouTube.`)
      return
    }
    setApplyError('')
    apply.mutate({ applied_description: text })
  }

  return (
    <Card className="p-4" data-testid="clip-metadata-panel">
      <h3 className="mb-2 text-label uppercase tracking-[0.06em] text-muted">Publish metadata</h3>

      {editingTitle ? (
        <AppliedTitleField clip={clip} autoEdit onClose={() => setEditingTitle(false)} />
      ) : (
        <MetadataRow
          label="Title"
          value={title}
          applied={clip.applied_title != null}
          pending={apply.isPending}
          onApply={applySuggestedTitle}
          onEdit={() => setEditingTitle(true)}
        />
      )}

      {editingDescription ? (
        <AppliedDescriptionField clip={clip} autoEdit onClose={() => setEditingDescription(false)} />
      ) : (
        <MetadataRow
          label="Hook"
          value={hook}
          applied={clip.applied_description != null}
          pending={apply.isPending}
          onApply={() => applySuggestedDescription(clip.suggested_description)}
          onEdit={() => setEditingDescription(true)}
        />
      )}

      {applyError && <p className="mt-1 text-xs text-danger">{applyError}</p>}
      {apply.isError && <p className="mt-1 text-xs text-danger">Could not apply — try again.</p>}

      <div className="mt-2 border-t border-default pt-2">
        <Disclosure summary="More suggestions">
          {clip.suggested_hook && (
            <div className="mb-2 flex items-start gap-1 text-xs">
              <span className="shrink-0 text-subtle">Suggested hook:</span>
              <span className="flex-1 text-fg">{clip.suggested_hook}</span>
              <ApplyButton
                applied={clip.applied_description === clip.suggested_hook}
                pending={apply.isPending}
                onApply={() => applySuggestedDescription(clip.suggested_hook)}
              />
              <CopyButton text={clip.suggested_hook} />
            </div>
          )}
          {/* Issues 322/323 — the on-demand generators, now the Regenerate path. */}
          <div data-testid="clip-action-row" className="flex flex-wrap gap-2">
            <RegenerateTitlesCard clip={clip} />
            <RegenerateCaptionHooksCard clip={clip} />
          </div>
        </Disclosure>
      </div>
    </Card>
  )
}
