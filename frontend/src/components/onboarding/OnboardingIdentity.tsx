import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import type { NicheOption } from '@/types'

// Slim identity intake for the onboarding flow (Issue 83 / 100): niche (1–3) +
// one-line audience. Saving creates the identity row, which unlocks the DNA
// build step (`onSaved`). The full identity editor lives on the Profile page;
// this is the minimal 45-second version.
export function OnboardingIdentity({ onSaved }: { onSaved: () => void }) {
  const { data: niches = [] } = useQuery({
    queryKey: ['niches'],
    queryFn: async () => (await api<{ options: NicheOption[] }>('/creators/niches')).options,
  })
  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const [audience, setAudience] = useState('')
  const [status, setStatus] = useState<{ text: string; ok: boolean } | null>(null)

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else if (next.size < 3) next.add(id)
      return next
    })

  async function save() {
    if (selected.size === 0) {
      setStatus({ text: 'Pick at least one niche, or skip this step.', ok: false })
      return
    }
    if (!audience.trim()) {
      setStatus({ text: 'Add a one-sentence audience description, or skip.', ok: false })
      return
    }
    setStatus({ text: 'Saving…', ok: true })
    try {
      await api('/creators/me/identity', {
        method: 'POST',
        body: { niches: Array.from(selected), audience_summary: audience.trim() },
      })
      setStatus({ text: '✓ Saved. Continue to step 4.', ok: true })
      onSaved()
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : 'Could not save — try again.'
      setStatus({ text: msg, ok: false })
    }
  }

  return (
    <>
      <p className="mb-4 text-sm text-muted">
        Speeds up your DNA and keeps recommendations honest. Skip and we'll use your video data only.
      </p>

      <div className="mb-2 text-xs uppercase tracking-[0.06em] text-muted">Niche (pick 1–3)</div>
      <div className="mb-4 flex flex-wrap gap-2">
        {niches.length === 0 && <span className="text-sm text-muted">Loading…</span>}
        {niches.map((opt) => (
          <button
            type="button"
            key={opt.id}
            onClick={() => toggle(opt.id)}
            aria-pressed={selected.has(opt.id)}
            className={cn(
              'rounded-md border px-3 py-1 text-xs transition-colors',
              selected.has(opt.id)
                ? 'border-accent bg-accent-soft text-accent'
                : 'border-strong text-muted hover:border-muted hover:text-fg',
            )}
          >
            {opt.label}
          </button>
        ))}
      </div>

      <div className="mb-2 text-xs uppercase tracking-[0.06em] text-muted">Who's your audience?</div>
      <textarea
        rows={2}
        value={audience}
        onChange={(e) => setAudience(e.target.value)}
        placeholder="e.g. College students learning to invest."
        className="mb-3 w-full resize-y rounded-md border border-strong bg-bg px-3 py-2 text-sm text-fg placeholder:text-subtle focus:border-accent focus:outline-none"
      />

      <Button className="w-full" onClick={save}>
        Save &amp; continue
      </Button>
      {status && (
        <p className={cn('mt-2 text-center text-xs', status.ok ? 'text-success' : 'text-muted')}>
          {status.text}
        </p>
      )}
    </>
  )
}
