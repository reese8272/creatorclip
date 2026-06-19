import { useState } from 'react'
import { api, ApiError } from '@/lib/api'
import { Button } from '@/components/ui/button'

// Right-to-erasure (Issue 158). Calls DELETE /auth/me, which revokes the Google
// OAuth grant, purges source media (R2), and cascade-deletes all creator data;
// the endpoint also clears the session cookie. Two-step confirm so a destructive,
// irreversible action can't be triggered by a stray click.
export function AccountDeletion() {
  const [confirming, setConfirming] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function remove() {
    setDeleting(true)
    setError(null)
    try {
      await api('/auth/me', { method: 'DELETE', redirectOn401: false })
      // Session cookie is cleared server-side; a full load lands on login.
      window.location.href = '/app/login'
    } catch (e) {
      setDeleting(false)
      setError(e instanceof ApiError ? e.message : 'Could not delete your account — try again.')
    }
  }

  return (
    <section className="rounded-lg border border-[color:var(--color-danger-border)] bg-surface p-5">
      <h2 className="mb-1 text-sm font-semibold text-fg">Delete account</h2>
      <p className="mb-4 text-sm text-muted">
        Permanently deletes your account: revokes AutoClip's access to your YouTube channel,
        purges your uploaded source media, and erases all of your data. This cannot be undone.
      </p>
      {error && <p className="mb-3 text-sm text-danger">{error}</p>}
      {!confirming ? (
        <Button variant="danger" onClick={() => setConfirming(true)}>
          Delete my account
        </Button>
      ) : (
        <div className="flex items-center gap-3">
          <Button variant="danger" disabled={deleting} onClick={remove}>
            {deleting ? 'Deleting…' : 'Yes, permanently delete'}
          </Button>
          <button
            type="button"
            disabled={deleting}
            onClick={() => setConfirming(false)}
            className="text-sm text-muted hover:text-fg disabled:opacity-50"
          >
            Cancel
          </button>
        </div>
      )}
    </section>
  )
}
