import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import { Card, CardBody, CardHeader } from '@/components/ui/card'
import type { NotificationPreferences } from '@/types'

// A single on/off toggle row. When `locked` it renders forced-on and disabled
// (used for the legally always-on transactional channel).
function ToggleRow({
  label,
  description,
  checked,
  locked,
  onChange,
}: {
  label: string
  description: string
  checked: boolean
  locked?: boolean
  onChange?: (next: boolean) => void
}) {
  return (
    <label
      className={`flex items-center justify-between gap-4 border-b border-default px-[18px] py-[15px] last:border-b-0 ${
        locked ? 'cursor-default' : 'cursor-pointer'
      }`}
    >
      <span>
        <span className="block text-small text-fg">{label}</span>
        <span className="mt-0.5 block text-small text-subtle">{description}</span>
      </span>
      <input
        type="checkbox"
        role="switch"
        aria-label={label}
        checked={checked}
        disabled={locked}
        onChange={(e) => onChange?.(e.target.checked)}
        className="h-5 w-5 accent-[color:var(--color-accent)] disabled:opacity-60"
      />
    </label>
  )
}

// Notification preferences (Issue 245). email_transactional is shown locked-on
// (legally always-on); email_lifecycle + inapp_enabled are interactive. Each
// toggle PATCHes only its own field so a change can never touch transactional.
export function NotificationPreferencesSection() {
  const [prefs, setPrefs] = useState<NotificationPreferences | null>(null)
  const [status, setStatus] = useState<{ text: string; tone: 'muted' | 'success' | 'danger' } | null>(
    null,
  )

  useEffect(() => {
    api<NotificationPreferences>('/api/notifications/preferences')
      .then(setPrefs)
      .catch(() => setStatus({ text: 'Could not load preferences.', tone: 'danger' }))
  }, [])

  const patch = async (field: 'email_lifecycle' | 'inapp_enabled', next: boolean) => {
    if (!prefs) return
    const previous = prefs
    setPrefs({ ...prefs, [field]: next })
    setStatus({ text: 'Saving…', tone: 'muted' })
    try {
      const updated = await api<NotificationPreferences>('/api/notifications/preferences', {
        method: 'PATCH',
        body: { [field]: next },
      })
      setPrefs(updated)
      setStatus({ text: 'Saved.', tone: 'success' })
    } catch {
      setPrefs(previous)
      setStatus({ text: 'Could not save — try again.', tone: 'danger' })
    }
  }

  return (
    <Card>
      <CardHeader
        title="Email & notifications"
        description="Choose which messages AutoClip sends you. Transactional emails are required for your account and can't be turned off."
      />
      <CardBody className="p-0">
        <ToggleRow
          label="Transactional emails"
          description="Clip-ready, refunds, re-auth. Always on — required for your account."
          checked={prefs?.email_transactional ?? true}
          locked
        />
        <ToggleRow
          label="Lifecycle emails"
          description="Welcome, first-clip nudges, and re-engagement. You can opt out any time."
          checked={prefs?.email_lifecycle ?? true}
          onChange={(next) => patch('email_lifecycle', next)}
        />
        <ToggleRow
          label="In-app notifications"
          description="The notification center shown in the app."
          checked={prefs?.inapp_enabled ?? true}
          onChange={(next) => patch('inapp_enabled', next)}
        />
      </CardBody>
      {status && (
        <div className="border-t border-default px-[18px] py-2">
          <span
            className={
              status.tone === 'success'
                ? 'text-small text-success'
                : status.tone === 'danger'
                  ? 'text-small text-danger'
                  : 'text-small text-muted'
            }
          >
            {status.text}
          </span>
        </div>
      )}
    </Card>
  )
}
