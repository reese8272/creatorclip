import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import type { NicheOption } from '@/types'

// Identity intake for onboarding (Issue 83 / 100). Two interchangeable modes the
// creator picks per session (Issue 96): the quick wizard form, or a guided chat
// that asks one question at a time and proposes a profile to confirm. Both write
// the SAME CreatorIdentity row via POST /creators/me/identity; saving unlocks the
// DNA build step (`onSaved`). Intake stays optional (Issue 204).
export function OnboardingIdentity({ onSaved }: { onSaved: () => void }) {
  const [mode, setMode] = useState<'form' | 'chat'>('form')
  return (
    <>
      <p className="mb-3 text-sm text-muted">
        Speeds up your DNA and keeps recommendations honest. Skip and we'll use your video data only.
      </p>
      <div role="tablist" aria-label="Intake mode" className="mb-4 flex gap-1 text-xs">
        {(['form', 'chat'] as const).map((m) => (
          <button
            key={m}
            role="tab"
            aria-selected={mode === m}
            onClick={() => setMode(m)}
            className={cn(
              'rounded-md border px-3 py-1 transition-colors',
              mode === m
                ? 'border-accent bg-accent-soft text-accent-text'
                : 'border-strong text-muted hover:border-muted hover:text-fg',
            )}
          >
            {m === 'form' ? 'Quick form' : 'Chat it out'}
          </button>
        ))}
      </div>
      {mode === 'form' ? <WizardForm onSaved={onSaved} /> : <IntakeChat onSaved={onSaved} />}
    </>
  )
}

// ── Quick wizard form (the original 45-second intake) ────────────────────────
function WizardForm({ onSaved }: { onSaved: () => void }) {
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
                ? 'border-accent bg-accent-soft text-accent-text'
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

// ── Chat-driven intake (Issue 96) ────────────────────────────────────────────
type ChatTurn = { role: 'user' | 'assistant'; content: string }
type ProfileProposal = {
  niches: string[]
  audience_summary: string
  content_pillars?: string[] | null
  tone_tags?: string[] | null
  hard_nos?: string[] | null
  mission?: string | null
}

const GREETING =
  "Hi! Tell me what your channel is about and who it's for, and I'll set up your profile. " +
  'You can skip anything you like.'

function IntakeChat({ onSaved }: { onSaved: () => void }) {
  const { data: niches = [] } = useQuery({
    queryKey: ['niches'],
    queryFn: async () => (await api<{ options: NicheOption[] }>('/creators/niches')).options,
  })
  const labelFor = (id: string) => niches.find((n) => n.id === id)?.label ?? id

  // `turns` is the API-bound history (it must start with a user message, so the
  // static greeting is display-only and excluded). `visible` adds the greeting.
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [proposal, setProposal] = useState<ProfileProposal | null>(null)
  const [error, setError] = useState<string | null>(null)
  const visible: ChatTurn[] = [{ role: 'assistant', content: GREETING }, ...turns]

  async function send() {
    const text = input.trim()
    if (!text || busy) return
    setError(null)
    setProposal(null)
    const next: ChatTurn[] = [...turns, { role: 'user', content: text }]
    setTurns(next)
    setInput('')
    setBusy(true)
    try {
      const res = await api<{ reply: string; proposal: ProfileProposal | null }>(
        '/creators/me/identity/chat',
        { method: 'POST', body: { history: next } },
      )
      setTurns([...next, { role: 'assistant', content: res.reply }])
      if (res.proposal) setProposal(res.proposal)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The assistant is unavailable — try the form.')
    } finally {
      setBusy(false)
    }
  }

  async function confirm() {
    if (!proposal) return
    setBusy(true)
    setError(null)
    try {
      await api('/creators/me/identity', { method: 'POST', body: proposal })
      onSaved()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save — try again.')
      setBusy(false)
    }
  }

  return (
    <div>
      <div
        className="mb-3 max-h-64 space-y-2 overflow-y-auto rounded-md border border-default bg-bg p-3"
        aria-live="polite"
      >
        {visible.map((m, i) => (
          <div
            key={i}
            className={cn(
              'max-w-[85%] rounded-md px-3 py-2 text-sm',
              m.role === 'assistant'
                ? 'bg-surface text-fg'
                : 'ml-auto bg-accent-soft text-accent-text',
            )}
          >
            {m.content}
          </div>
        ))}
        {busy && !proposal && <div className="text-xs text-subtle">…</div>}
      </div>

      {proposal ? (
        <div className="mb-3 rounded-md border border-accent bg-accent-soft p-3 text-sm">
          <div className="mb-2 text-xs font-medium uppercase tracking-[0.06em] text-accent-text">
            Your profile — confirm or keep chatting
          </div>
          <p className="text-fg">
            <span className="text-muted">Niche:</span> {proposal.niches.map(labelFor).join(', ')}
          </p>
          <p className="text-fg">
            <span className="text-muted">Audience:</span> {proposal.audience_summary}
          </p>
          {proposal.tone_tags?.length ? (
            <p className="text-fg">
              <span className="text-muted">Tone:</span> {proposal.tone_tags.join(', ')}
            </p>
          ) : null}
          {proposal.hard_nos?.length ? (
            <p className="text-fg">
              <span className="text-muted">Won't do:</span> {proposal.hard_nos.join(', ')}
            </p>
          ) : null}
          {proposal.mission ? (
            <p className="text-fg">
              <span className="text-muted">Building:</span> {proposal.mission}
            </p>
          ) : null}
          <Button className="mt-3 w-full" disabled={busy} onClick={confirm}>
            Save &amp; continue
          </Button>
        </div>
      ) : null}

      <div className="flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') send()
          }}
          disabled={busy}
          placeholder="Type your answer…"
          aria-label="Your message"
          className="flex-1 rounded-md border border-strong bg-bg px-3 py-2 text-sm text-fg placeholder:text-subtle focus:border-accent focus:outline-none disabled:opacity-60"
        />
        <Button variant="secondary" disabled={busy || !input.trim()} onClick={send}>
          Send
        </Button>
      </div>
      {error && <p className="mt-2 text-center text-xs text-warning">{error}</p>}
    </div>
  )
}
