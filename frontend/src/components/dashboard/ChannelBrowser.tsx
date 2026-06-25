import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { Button } from '@/components/ui/button'
import type { CatalogListResponse, Video } from '@/types'

const PAGE_SIZE = 50

// We never download the original video from YouTube (their Terms of Service), so
// picking a channel video here only skips the URL paste — the source file must
// still be uploaded to generate clips. Mirrors VideoTable's SOURCE_NEEDED_HELP.
const SOURCE_NEEDED_HELP =
  'Clipping a YouTube video still needs the source file — we never download from YouTube ' +
  '(their Terms of Service). Picking it here just skips the URL paste; upload the original ' +
  'file (e.g. from Google Takeout) and it processes automatically.'

// One synced-channel row. Owns its local "Clip this" button state. Promotion
// posts multipart/form-data to /videos/link (the endpoint takes Form fields, not
// JSON — using the JSON api() helper would send application/json and 422), then
// invalidates the videos + catalog queries so the row moves into "Your videos".
function CatalogRow({ video }: { video: Video }) {
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [label, setLabel] = useState<string | null>(null)

  async function clip() {
    // /videos/link adopts a catalog row by its YouTube id (a required Form field),
    // so a row without one (nullable since Issue 317) cannot be promoted here.
    if (!video.youtube_video_id) {
      setLabel('Unavailable')
      return
    }
    setBusy(true)
    setLabel('Adding…')
    const form = new FormData()
    form.append('youtube_video_id', video.youtube_video_id)
    const resp = await fetch('/videos/link', {
      method: 'POST',
      credentials: 'include',
      body: form,
    })
    if (resp.ok) {
      setLabel('Added ✓')
      // The promoted row is now origin=link: it appears in /videos and leaves
      // /videos/catalog. Invalidate both so the UI reflects the move.
      queryClient.invalidateQueries({ queryKey: ['videos'] })
      queryClient.invalidateQueries({ queryKey: ['catalog'] })
    } else {
      setBusy(false)
      setLabel('Retry')
    }
  }

  return (
    <tr className="border-b border-default hover:bg-elevated">
      <td className="px-4 py-3.5 align-middle">
        <div className="max-w-[280px] truncate text-fg">{video.title || '—'}</div>
        <div className="font-mono text-xs text-subtle">
          {video.kind} · {video.youtube_video_id}
        </div>
      </td>
      <td className="px-4 py-3.5 align-middle text-right">
        <Button size="sm" disabled={busy} onClick={clip}>
          {label ?? 'Clip this'}
        </Button>
      </td>
    </tr>
  )
}

// Modal panel listing the creator's synced channel videos (origin=catalog) so
// they can promote one into the clip pipeline without pasting a URL (Issue 310).
export function ChannelBrowser({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [offset, setOffset] = useState(0)

  const catalogQuery = useQuery({
    queryKey: ['catalog', offset],
    queryFn: () =>
      api<CatalogListResponse>(`/videos/catalog?limit=${PAGE_SIZE}&offset=${offset}`),
    enabled: open,
  })

  if (!open) return null

  const data = catalogQuery.data
  const videos = data?.videos ?? []
  const total = data?.total ?? 0
  const limit = data?.limit ?? PAGE_SIZE
  const hasPrev = offset > 0
  const hasNext = offset + limit < total

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/60 p-4 sm:p-8"
      role="dialog"
      aria-modal="true"
      aria-label="Browse my channel"
      onClick={onClose}
    >
      <div
        className="mt-8 w-full max-w-3xl rounded-md border border-default bg-surface shadow-sm shadow-inset"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-default px-5 py-4">
          <div>
            <h2 className="font-display text-h2 text-fg">Browse my channel</h2>
            <p className="mt-1 text-small text-muted">
              Pick one of your published videos to clip — no URL to paste.
            </p>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close">
            ✕
          </Button>
        </div>

        <p className="px-5 pt-3 text-small text-subtle">{SOURCE_NEEDED_HELP}</p>

        <div className="px-2 py-2">
          {catalogQuery.isPending ? (
            <p className="py-8 text-center text-sm text-subtle">Loading…</p>
          ) : videos.length === 0 ? (
            <p className="py-8 text-center text-sm text-subtle">
              No synced channel videos yet — sync your catalog to see them here.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-sm">
                <thead>
                  <tr>
                    {['Video', 'Action'].map((h) => (
                      <th
                        key={h}
                        className="border-b border-default px-4 py-[11px] text-left text-xs font-medium uppercase tracking-[0.06em] text-subtle last:text-right"
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {videos.map((v) => (
                    <CatalogRow key={v.id} video={v} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="flex items-center justify-between border-t border-default px-5 py-3">
          <span className="text-small text-subtle">
            {total} video{total === 1 ? '' : 's'}
          </span>
          <div className="flex gap-2.5">
            <Button
              variant="secondary"
              size="sm"
              disabled={!hasPrev}
              onClick={() => setOffset((o) => Math.max(0, o - limit))}
            >
              ← Prev
            </Button>
            <Button
              variant="secondary"
              size="sm"
              disabled={!hasNext}
              onClick={() => setOffset((o) => o + limit)}
            >
              Next →
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
