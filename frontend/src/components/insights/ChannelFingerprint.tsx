import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { Panel, Cell, gridCls } from '@/components/insights/InsightsPanel'
import { EmptyStatePrompt } from '@/components/EmptyStatePrompt'
import { Chip } from '@/components/Chip'
import type { DnaStats, FingerprintShare } from '@/types'

/**
 * Channel Fingerprint (Issue 379) — the DNA rendered as an artifact a
 * creator would actually want to look at, in two strictly separate tiers:
 *
 *  1. PRIVATE (always shown here) — the analytics-derived DNA stats
 *     (optimal clip length, best source region, upload gap). Shown only to
 *     the authorizing creator, permitted under YouTube API Services
 *     Policies III.E.3.b. This never leaves the page.
 *
 *  2. SHAREABLE (only after the explicit "Create shareable fingerprint"
 *     click) — calls `POST /creators/me/fingerprint/share`, which returns
 *     ONLY self-declared identity fields + our own feedback-distilled style
 *     summary + the honesty line. No analytics value is ever in that
 *     response — see routers/creators.py::FingerprintShareOut and
 *     docs/COMPLIANCE.md for the field-by-field audit. Nothing is generated,
 *     stored, or shareable until the creator clicks the button below —
 *     default private.
 */
export function ChannelFingerprint({ dna }: { dna: DnaStats }) {
  const [card, setCard] = useState<FingerprintShare | null>(null)
  const [copied, setCopied] = useState(false)

  const shareMutation = useMutation({
    mutationFn: () => api<FingerprintShare>('/creators/me/fingerprint/share', { method: 'POST' }),
    onSuccess: (data) => setCard(data),
  })

  const version = dna.version != null ? `v${dna.version} · ${dna.status}` : 'Not built yet'

  // Without a built DNA every cell below reads "—" and the share action would
  // produce an empty card — a dead end wearing a full panel (Issue 355).
  if (dna.version == null) {
    return (
      <Panel id="channel-fingerprint" title="Your channel fingerprint" sub={version}>
        <EmptyStatePrompt
          title="Your fingerprint isn’t built yet."
          detail="Build your Creator DNA and this becomes the shareable summary of what your channel actually is."
          actionLabel="Build your DNA"
          actionTo="/onboarding"
        />
      </Panel>
    )
  }

  function copyCard() {
    if (!card) return
    const lines = [
      card.niches.length > 0 ? `Niche: ${card.niches.join(', ')}` : null,
      card.content_pillars && card.content_pillars.length > 0
        ? `Content pillars: ${card.content_pillars.join(', ')}`
        : null,
      card.tone_tags && card.tone_tags.length > 0 ? `Tone: ${card.tone_tags.join(', ')}` : null,
      card.mission ? `Mission: ${card.mission}` : null,
      card.style_summary ? `Style: ${card.style_summary}` : null,
      '',
      card.honesty_line,
    ].filter((l): l is string => l !== null)
    navigator.clipboard.writeText(lines.join('\n')).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  return (
    <Panel id="channel-fingerprint" title="Your channel fingerprint" sub={version}>
      {/* Tier 1 — private. Analytics-derived; visible only to you. */}
      <div className={gridCls}>
        <Cell
          label="Optimal clip"
          value={dna.optimal_clip_len_s != null ? `${dna.optimal_clip_len_s.toFixed(0)}s` : '—'}
        />
        <Cell
          label="Best region"
          value={dna.best_source_region ? dna.best_source_region.replace('_', ' ') : '—'}
        />
        <Cell
          label="Upload gap"
          value={dna.optimal_upload_gap_h != null ? `${dna.optimal_upload_gap_h.toFixed(1)}h` : '—'}
        />
      </div>
      <p className="mt-2 text-xs text-subtle">
        Grounded in your own YouTube analytics — visible only to you, never included in anything
        shareable below.
      </p>

      {/* Tier 2 — shareable, explicit action only. Nothing is generated until clicked. */}
      <div className="mt-5 border-t border-default pt-4">
        {!card ? (
          <button
            onClick={() => shareMutation.mutate()}
            disabled={shareMutation.isPending}
            className="rounded-md border border-default bg-bg px-3 py-2 text-sm font-medium text-fg transition-colors hover:bg-surface disabled:opacity-50"
          >
            {shareMutation.isPending ? 'Building your fingerprint…' : '✦ Create shareable fingerprint'}
          </button>
        ) : (
          <div className="rounded-md border border-dashed border-default bg-bg p-4">
            <div className="mb-3 flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <Chip pose="idea" size={28} />
                <span className="text-xs font-semibold uppercase tracking-[0.08em] text-muted">
                  Shareable card preview
                </span>
              </div>
              <button
                onClick={copyCard}
                className="rounded px-2 py-1 text-xs text-muted transition-colors hover:text-fg"
              >
                {copied ? '✓ Copied' : 'Copy'}
              </button>
            </div>

            {card.niches.length > 0 && (
              <div className="mb-2 flex flex-wrap gap-1.5">
                {card.niches.map((n) => (
                  <span
                    key={n}
                    className="rounded-full border border-default bg-surface px-2.5 py-1 text-xs text-fg"
                  >
                    {n}
                  </span>
                ))}
              </div>
            )}
            {(card.content_pillars ?? []).concat(card.tone_tags ?? []).length > 0 && (
              <div className="mb-2 flex flex-wrap gap-1.5">
                {[...(card.content_pillars ?? []), ...(card.tone_tags ?? [])].map((t) => (
                  <span
                    key={t}
                    className="rounded-full border border-default bg-surface px-2.5 py-1 text-xs text-muted"
                  >
                    {t}
                  </span>
                ))}
              </div>
            )}
            {card.mission && <p className="mt-2 text-sm text-fg">{card.mission}</p>}
            {card.style_summary && (
              <p className="mt-2 text-sm leading-relaxed text-muted">{card.style_summary}</p>
            )}
            {!card.mission && !card.style_summary && card.niches.length === 0 && (
              <p className="text-sm text-muted">
                Nothing to show yet — set your identity in Settings and this card fills in.
              </p>
            )}

            <p className="mt-3 border-t border-default pt-2 text-xs italic text-subtle">
              {card.honesty_line}
            </p>
          </div>
        )}
        {shareMutation.isError && (
          <p className="mt-2 text-sm text-danger">Could not build your fingerprint — try again.</p>
        )}
        <p className="mt-2 text-xs text-subtle">
          Nothing above is generated until you click — this card never includes your view counts,
          engagement, or any other analytics value.
        </p>
      </div>
    </Panel>
  )
}
