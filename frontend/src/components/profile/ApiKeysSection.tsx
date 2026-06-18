import { useEffect, useState, type FormEvent } from 'react'
import { api, ApiError } from '@/lib/api'
import { relativeTime } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Card, CardBody, CardHeader } from '@/components/ui/card'
import { Modal } from '@/components/ui/modal'
import type { ApiKey } from '@/types'

const inputCls =
  'flex-1 rounded-md border border-default bg-surface px-3 py-2 text-sm text-fg placeholder:text-subtle focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent-soft'

export function ApiKeysSection() {
  const [keys, setKeys] = useState<ApiKey[] | null>(null)
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [revealKey, setRevealKey] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [revokeTarget, setRevokeTarget] = useState<ApiKey | null>(null)

  const reload = () =>
    api<{ keys: ApiKey[] }>('/creators/me/api-keys')
      .then((data) => setKeys(data.keys || []))
      .catch(() => {
        setKeys([])
        setError('Could not load API keys — refresh to retry.')
      })

  useEffect(() => {
    api<{ keys: ApiKey[] }>('/creators/me/api-keys')
      .then((data) => setKeys(data.keys || []))
      .catch(() => {
        setKeys([])
        setError('Could not load API keys — refresh to retry.')
      })
  }, [])

  const create = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    if (!name.trim()) {
      setError('Name is required.')
      return
    }
    try {
      const data = await api<{ raw_key: string }>('/creators/me/api-keys', {
        method: 'POST',
        body: { name: name.trim() },
      })
      setName('')
      setRevealKey(data.raw_key)
      setCopied(false)
      reload()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create key — try again.')
    }
  }

  const revoke = async () => {
    if (!revokeTarget) return
    try {
      await api(`/creators/me/api-keys/${encodeURIComponent(revokeTarget.id)}`, { method: 'DELETE' })
    } finally {
      setRevokeTarget(null)
      reload()
    }
  }

  const copy = async () => {
    if (!revealKey) return
    try {
      await navigator.clipboard.writeText(revealKey)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      /* clipboard blocked — user can select manually */
    }
  }

  return (
    <Card>
      <CardHeader
        title="API keys"
        description="For the OBS companion app. Generate a key on the device that runs OBS — it stores the key in your OS keyring and uploads replay-buffer clips into your review queue."
      />
      <CardBody className="flex flex-col gap-4">
        <form onSubmit={create} className="flex gap-2">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={64}
            placeholder="OBS MacBook"
            className={inputCls}
          />
          <Button type="submit">Generate key</Button>
        </form>
        {error && <p className="text-sm text-danger">{error}</p>}

        {keys === null ? (
          <p className="text-sm text-muted">Loading…</p>
        ) : keys.length === 0 ? (
          <p className="text-sm text-muted">No API keys yet. Generate one above to connect the OBS companion app.</p>
        ) : (
          <div className="flex flex-col divide-y divide-[color:var(--color-default)]">
            {keys.map((k) => (
              <div key={k.id} className="flex items-center justify-between gap-4 py-3">
                <div className="min-w-0">
                  <div className="text-sm font-medium text-fg">{k.name}</div>
                  <div className="truncate font-mono text-xs text-subtle">
                    ack_{k.key_prefix}••••••••••••••••
                  </div>
                  <div className="text-2xs text-subtle">
                    Last used {relativeTime(k.last_used_at)} · Created {relativeTime(k.created_at)}
                  </div>
                </div>
                <Button variant="danger" size="sm" onClick={() => setRevokeTarget(k)}>
                  Revoke
                </Button>
              </div>
            ))}
          </div>
        )}
      </CardBody>

      <Modal open={revealKey !== null} title="Copy your API key" onClose={() => setRevealKey(null)}>
        <p className="mb-3 text-sm text-warning">Copy your key now — you won't be able to see it again.</p>
        <div className="flex gap-2">
          <input readOnly value={revealKey ?? ''} className={`${inputCls} font-mono`} />
          <Button variant="secondary" onClick={copy}>
            {copied ? '✓ Copied' : 'Copy'}
          </Button>
        </div>
        <div className="mt-4 flex justify-end">
          <Button onClick={() => setRevealKey(null)}>Done</Button>
        </div>
      </Modal>

      <Modal open={revokeTarget !== null} title="Revoke this API key?" onClose={() => setRevokeTarget(null)}>
        <p className="mb-2 text-sm text-fg">Key "{revokeTarget?.name}" will be revoked.</p>
        <p className="mb-4 text-sm text-warning">
          Any applications using this key will stop working immediately. This cannot be undone.
        </p>
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setRevokeTarget(null)}>
            Cancel
          </Button>
          <Button variant="danger" onClick={revoke}>
            Revoke key
          </Button>
        </div>
      </Modal>
    </Card>
  )
}
