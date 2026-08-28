import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, ApiError } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Download } from '@/components/ui/icon'
import { ICON_SIZE } from '@/components/ui/iconSizes'

interface ExportStatus {
  status: 'none' | 'pending' | 'ready' | 'failed'
  requested_at: string | null
  completed_at: string | null
  error: string | null
}

// GDPR Art. 15/20 data export (Issue 526). The backend has been complete since
// Issue 247 — three endpoints, a Celery build, R2/local artifact — and the
// Privacy Policy promises "a download from your account"; this section is the
// missing account surface. Server truth only, no optimistic state: the button
// POSTs, the status is polled while pending, and Download is a plain authed
// same-origin link (the server 302s to a presigned URL in prod).
export function DataExportSection() {
  const queryClient = useQueryClient()
  const [requesting, setRequesting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const statusQuery = useQuery({
    queryKey: ['data-export'],
    queryFn: () => api<ExportStatus>('/creators/me/export'),
    // Poll only while a build is in flight; a settled export stays put.
    refetchInterval: (query) => (query.state.data?.status === 'pending' ? 4000 : false),
  })
  const status = statusQuery.data?.status ?? 'none'

  async function requestExport() {
    if (requesting) return
    setRequesting(true)
    setError(null)
    try {
      await api('/creators/me/export', { method: 'POST' })
      await queryClient.invalidateQueries({ queryKey: ['data-export'] })
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not start the export — try again.')
    } finally {
      setRequesting(false)
    }
  }

  const completedAt = statusQuery.data?.completed_at
  return (
    <section className="rounded-md border border-default bg-surface p-5 shadow-sm inset-shadow-highlight">
      <h2 className="mb-1 text-sm font-semibold text-fg">Export your data</h2>
      <p className="mb-4 text-sm text-muted">
        Download everything AutoClip stores about you and your channel as a single JSON file —
        your profile, videos, clips, ratings, and settings. Building it takes a minute or two.
      </p>
      {error && <p className="mb-3 text-sm text-danger">{error}</p>}
      {status === 'failed' && statusQuery.data?.error && (
        <p className="mb-3 text-sm text-danger">
          The last export failed: {statusQuery.data.error}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant="secondary"
          disabled={requesting || status === 'pending'}
          onClick={() => void requestExport()}
        >
          {status === 'pending'
            ? 'Building your export…'
            : status === 'ready' || status === 'failed'
              ? 'Request a fresh export'
              : requesting
                ? 'Requesting…'
                : 'Request export'}
        </Button>
        {status === 'ready' && (
          <a
            href="/creators/me/export/download"
            download
            className="inline-flex h-9 items-center gap-1.5 rounded-sm border border-strong bg-bg px-3 text-sm text-muted inset-shadow-highlight hover:bg-elevated hover:text-fg"
          >
            <Download className={ICON_SIZE.sm} aria-hidden="true" /> Download JSON
          </a>
        )}
        {status === 'ready' && completedAt && (
          <span className="text-xs text-subtle">
            Ready — built {new Date(completedAt).toLocaleString()}
          </span>
        )}
        {status === 'pending' && (
          <span role="status" aria-live="polite" className="text-xs text-subtle">
            We’re gathering your data — this page updates on its own.
          </span>
        )}
      </div>
    </section>
  )
}
