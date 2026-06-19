import { useState, type FormEvent } from 'react'
import { api, ApiError } from '@/lib/api'
import { cn, parseCsv } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Card, CardBody, CardHeader } from '@/components/ui/card'
import type { Identity, IdentityPayload, NicheOption } from '@/types'

const inputCls =
  'w-full rounded-md border border-default bg-surface px-3 py-2 text-sm text-fg placeholder:text-subtle focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent-soft'

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-sm font-medium text-fg">{label}</label>
      {children}
      {hint && <p className="text-xs text-subtle">{hint}</p>}
    </div>
  )
}

interface Props {
  niches: NicheOption[]
  identity: Identity | null
  conflict: string | null
  onSaved: () => void
}

export function IdentitySection({ niches, identity, conflict, onSaved }: Props) {
  // Uncontrolled-with-key pattern: Profile remounts this component (via `key`)
  // when the identity loads or changes, so initialising form state from props
  // is correct and avoids a prop→state sync effect (React's recommended
  // alternative to "you might not need an effect").
  const [selected, setSelected] = useState<Set<string>>(() => new Set(identity?.niches ?? []))
  const [audience, setAudience] = useState(identity?.audience_summary ?? '')
  const [mission, setMission] = useState(identity?.mission ?? '')
  const [pillars, setPillars] = useState((identity?.content_pillars ?? []).join(', '))
  const [tone, setTone] = useState((identity?.tone_tags ?? []).join(', '))
  const [hardNos, setHardNos] = useState((identity?.hard_nos ?? []).join(', '))
  const [status, setStatus] = useState<{ text: string; tone: 'muted' | 'success' | 'danger' } | null>(null)

  const labelFor = (id: string) => niches.find((n) => n.id === id)?.label || id

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else if (next.size < 3) next.add(id)
      return next
    })
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    const payload: IdentityPayload = {
      niches: Array.from(selected),
      audience_summary: audience.trim(),
      mission: mission.trim() || null,
      content_pillars: parseCsv(pillars),
      tone_tags: parseCsv(tone),
      hard_nos: parseCsv(hardNos),
    }
    if (payload.niches.length === 0) {
      setStatus({ text: 'Pick at least one niche.', tone: 'danger' })
      return
    }
    if (!payload.audience_summary) {
      setStatus({ text: 'Tell us who your audience is.', tone: 'danger' })
      return
    }
    try {
      const data = await api<{ version: number }>('/creators/me/identity', { method: 'POST', body: payload })
      setStatus({
        text: `Saved (v${data.version}). Hit ↺ Rebuild DNA above to apply this to your next clip pass.`,
        tone: 'success',
      })
      onSaved()
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : 'Could not save — try again.'
      setStatus({ text: msg, tone: 'danger' })
    }
  }

  return (
    <Card>
      <CardHeader
        title="Your identity"
        description="Tell us who you are — we fuse this with the patterns we infer from your videos, honour your boundaries, and surface clips that fit your actual direction."
      />
      <CardBody className="flex flex-col gap-5">
        {conflict && (
          <div className="rounded-md border border-[color:var(--color-danger-border)] bg-[color:var(--color-danger-soft)] px-3 py-2 text-sm text-fg">
            {conflict}
          </div>
        )}

        {identity && (
          <div className="rounded-md border border-default bg-bg px-4 py-3 text-sm">
            <div className="mb-2 text-2xs uppercase tracking-[0.06em] text-subtle">Current</div>
            <dl className="flex flex-col gap-1 text-fg/90">
              <div>
                <dt className="inline text-muted">Niche: </dt>
                <dd className="inline">{identity.niches.map(labelFor).join(', ')}</dd>
              </div>
              <div>
                <dt className="inline text-muted">Audience: </dt>
                <dd className="inline">{identity.audience_summary}</dd>
              </div>
              {identity.mission && (
                <div>
                  <dt className="inline text-muted">Mission: </dt>
                  <dd className="inline">{identity.mission}</dd>
                </div>
              )}
            </dl>
          </div>
        )}

        <form onSubmit={submit} className="flex flex-col gap-4">
          <Field label="Niche (pick 1–3)">
            <div className="flex flex-wrap gap-2">
              {niches.length === 0 && <span className="text-sm text-muted">Loading…</span>}
              {niches.map((opt) => (
                <button
                  type="button"
                  key={opt.id}
                  onClick={() => toggle(opt.id)}
                  className={cn(
                    'rounded-full border px-3 py-1 text-xs transition-colors',
                    selected.has(opt.id)
                      ? 'border-accent bg-accent-soft text-accent-text'
                      : 'border-default text-muted hover:border-strong hover:text-fg',
                  )}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </Field>

          <Field label="Who's your audience?" hint="1–3 sentences. The clearer you are, the better the fit.">
            <textarea
              rows={2}
              required
              value={audience}
              onChange={(e) => setAudience(e.target.value)}
              placeholder="e.g. College students learning to invest without the finance-bro vibe."
              className={inputCls}
            />
          </Field>

          <Field label="What are you trying to build? (optional)">
            <textarea
              rows={2}
              value={mission}
              onChange={(e) => setMission(e.target.value)}
              placeholder="e.g. The most trusted intro-to-investing channel for under-25s."
              className={inputCls}
            />
          </Field>

          <Field label="Content pillars (optional, comma-separated)">
            <input
              value={pillars}
              onChange={(e) => setPillars(e.target.value)}
              placeholder="e.g. portfolio breakdowns, market explainers, Q&A"
              className={inputCls}
            />
          </Field>

          <Field label="Voice / tone (optional, comma-separated)">
            <input
              value={tone}
              onChange={(e) => setTone(e.target.value)}
              placeholder="e.g. calm, plainspoken, dry humour"
              className={inputCls}
            />
          </Field>

          <Field label="What you will NOT do (optional, comma-separated)" hint="We'll respect these in every clip we suggest.">
            <input
              value={hardNos}
              onChange={(e) => setHardNos(e.target.value)}
              placeholder="e.g. clickbait, fearmongering, crypto promos"
              className={inputCls}
            />
          </Field>

          <div className="flex items-center gap-3">
            <Button type="submit">Save identity</Button>
            {status && (
              <span
                className={
                  status.tone === 'success'
                    ? 'text-sm text-success'
                    : status.tone === 'danger'
                      ? 'text-sm text-danger'
                      : 'text-sm text-muted'
                }
              >
                {status.text}
              </span>
            )}
          </div>
        </form>
      </CardBody>
    </Card>
  )
}
