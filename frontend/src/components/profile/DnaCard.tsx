import { useEffect, useRef, useState } from 'react'
import { api } from '@/lib/api'
import { subscribeToTaskStream, type StreamSubscription } from '@/lib/taskStream'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardBody, CardHeader } from '@/components/ui/card'
import { Chip } from '@/components/Chip'
import { Brief } from './Brief'
import type { DnaProfile, DnaResponse } from '@/types'

const fmtDate = (iso: string) =>
  new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-default bg-bg px-3 py-2">
      <div className="text-2xs uppercase tracking-[0.06em] text-subtle">{label}</div>
      <div className="mt-0.5 font-mono text-sm text-fg">{value}</div>
    </div>
  )
}

function stats(p: DnaProfile): { label: string; value: string }[] {
  const out: { label: string; value: string }[] = []
  if (p.optimal_clip_len_s) out.push({ label: 'Optimal clip', value: `${p.optimal_clip_len_s.toFixed(0)}s` })
  if (p.best_source_region) out.push({ label: 'Best region', value: p.best_source_region.replace('_', ' ') })
  if (p.optimal_upload_gap_h) out.push({ label: 'Upload gap', value: `${p.optimal_upload_gap_h.toFixed(1)}h` })
  return out
}

export function DnaCard({ identityCreatedAt }: { identityCreatedAt: string | null }) {
  const [profile, setProfile] = useState<DnaProfile | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [status, setStatus] = useState<{ text: string; tone: 'muted' | 'success' | 'danger' } | null>(null)
  const [stream, setStream] = useState('')
  const subRef = useRef<StreamSubscription | null>(null)

  const applyDna = (data: DnaResponse) => {
    setProfile(data.profile)
    setMessage(data.profile ? null : data.message || 'No DNA yet.')
    setLoading(false)
  }
  const reload = () =>
    api<DnaResponse>('/creators/me/dna')
      .then(applyDna)
      .catch(() => setLoading(false))

  useEffect(() => {
    api<DnaResponse>('/creators/me/dna')
      .then(applyDna)
      .catch(() => setLoading(false))
    return () => subRef.current?.close()
  }, [])

  const confirm = async () => {
    try {
      const data = await api<{ version: number }>('/creators/me/dna/confirm', { method: 'POST' })
      setStatus({ text: `DNA confirmed and active (v${data.version}). Redirecting…`, tone: 'success' })
      setTimeout(() => (window.location.href = '/'), 1500)
    } catch {
      setStatus({ text: 'Error confirming — try again.', tone: 'danger' })
    }
  }

  const rebuild = async () => {
    subRef.current?.close()
    setStream('')
    setStatus({ text: 'Rebuild queued…', tone: 'muted' })
    try {
      const data = await api<{ task_id: string; stream_url?: string }>('/creators/me/dna/build', {
        method: 'POST',
      })
      setStatus({ text: 'Rebuilding DNA — watch the progress below.', tone: 'muted' })
      if (data.stream_url) {
        subRef.current = subscribeToTaskStream(data.stream_url, {
          onRender: setStream,
          onDone: () => reload(),
        })
      }
    } catch {
      setStatus({ text: 'Could not queue rebuild — try again.', tone: 'danger' })
    }
  }

  // "Synced with DNA" when the DNA was built at/after the latest identity edit.
  const syncState =
    profile && identityCreatedAt
      ? new Date(profile.created_at) >= new Date(identityCreatedAt)
      : null

  const provenance = profile ? (
    <div className="flex items-center gap-2" title={`Internal version v${profile.version}`}>
      {syncState !== null && (
        <Badge variant={syncState ? 'success' : 'warning'}>
          {syncState ? 'Synced' : 'Out of sync'}
        </Badge>
      )}
      <Badge variant={profile.status === 'active' ? 'success' : 'muted'}>{profile.status}</Badge>
      <span className="text-xs text-subtle">Updated {fmtDate(profile.created_at)}</span>
    </div>
  ) : null

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <Chip pose="book" size={26} />
            Your Creator DNA
          </span>
        }
        description="What we've learned about your channel from your own analytics — grounded in your data, not a generic virality score."
        aside={provenance}
      />
      <CardBody className="flex flex-col gap-5">
        {loading ? (
          <p className="text-sm text-muted">Loading your creator brief…</p>
        ) : profile ? (
          <>
            {profile.brief_text ? (
              <Brief markdown={profile.brief_text} />
            ) : (
              <p className="text-sm text-muted">(no brief text)</p>
            )}
            {stats(profile).length > 0 && (
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                {stats(profile).map((s) => (
                  <Stat key={s.label} {...s} />
                ))}
              </div>
            )}
            <div className="flex flex-wrap gap-2">
              <Button variant="confirm" onClick={confirm}>
                Confirm &amp; activate
              </Button>
              <Button variant="outline" onClick={rebuild}>
                ↺ Rebuild DNA
              </Button>
            </div>
            {stream && (
              <pre className="max-h-64 overflow-auto rounded-md border border-default bg-bg p-3 font-mono text-xs text-muted">
                {stream}
              </pre>
            )}
            {status && (
              <p
                className={
                  status.tone === 'success'
                    ? 'text-sm text-success'
                    : status.tone === 'danger'
                      ? 'text-sm text-danger'
                      : 'text-sm text-muted'
                }
              >
                {status.text}
              </p>
            )}
          </>
        ) : (
          <p className="text-sm text-muted">{message}</p>
        )}
      </CardBody>
    </Card>
  )
}
