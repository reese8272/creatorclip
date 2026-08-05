import { useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { useUploader } from '@/hooks/useUploader'
import type { UploadQueueItem } from '@/hooks/useUploader'

// Pull the 11-char video ID out of a watch / youtu.be / shorts URL, or accept a
// bare ID. The server validates again — this is just so a pasted URL works. The
// association is OPTIONAL: leave it blank for a standalone raw upload.
function extractYouTubeId(input: string): string {
  try {
    const url = new URL(input)
    if (url.hostname === 'youtu.be') return url.pathname.slice(1).split('/')[0]
    if (url.hostname.includes('youtube.com')) {
      const v = url.searchParams.get('v')
      if (v) return v
      const shorts = url.pathname.match(/\/shorts\/([^/?]+)/)
      if (shorts) return shorts[1]
    }
  } catch {
    /* not a URL — fall through to treating it as a bare ID */
  }
  return input.split('?')[0]
}

const STATUS_LABEL: Record<UploadQueueItem['status'], string> = {
  queued: 'Ready',
  uploading: 'Uploading…',
  paused: 'Paused',
  done: 'Uploaded — analysing now.',
  error: 'Failed',
}

// Inline "upload a video" panel (Issue 317; rebuilt for Issue 395). The raw
// file IS the source media: under the YouTube ToS we never download from a
// link. Uploads go straight from the browser to R2 via presigned multipart
// (drag-and-drop, multi-file queue, per-file progress of R2-acked bytes,
// pause-free part retries, resume across reloads); on the local-disk dev
// backend the same queue falls back to the legacy proxy POST. The uploader is
// app-wide — closing this panel does not stop an in-flight upload.
//
// `youtube_video_id` is optional and applies when exactly one file is queued:
// it ties the upload to a published video so its later performance feeds the
// outcome loop. The in-app channel picker (ChannelBrowser, Issue 310) is the
// no-paste alternative.
export function UploadVideoForm({
  open,
  onUploaded,
}: {
  open: boolean
  /** Called with the new video id on success — lets the standalone landings track the upload in place. */
  onUploaded?: (videoId: string) => void
}) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [association, setAssociation] = useState('')
  const [notice, setNotice] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const { ready, items, sessionExpired, addFiles, start, retryFile, removeFile } =
    useUploader(onUploaded)

  const pending = items.filter((i) => i.status === 'queued')
  const active = items.some((i) => i.status === 'uploading')

  function takeFiles(files: FileList | null) {
    if (!files?.length) return
    setNotice(addFiles(Array.from(files)) ?? '')
    if (fileRef.current) fileRef.current.value = ''
  }

  function begin() {
    if (!pending.length && !items.some((i) => i.status === 'error')) {
      setNotice('Choose a video file to upload.')
      return
    }
    setNotice('')
    const ytId = extractYouTubeId(association.trim())
    start(ytId || undefined)
    setAssociation('')
  }

  if (!open) return null

  return (
    <div className="mb-5 animate-slide-up rounded-md border border-accent-border bg-surface px-[18px] py-4 shadow-sm inset-shadow-highlight">
      <div
        role="button"
        tabIndex={0}
        aria-label="Add video files — click to browse or drop files here"
        onClick={() => fileRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') fileRef.current?.click()
        }}
        onDragOver={(e) => {
          e.preventDefault()
          setDragOver(true)
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragOver(false)
          takeFiles(e.dataTransfer.files)
        }}
        className={`flex min-h-20 cursor-pointer flex-col items-center justify-center rounded-sm border border-dashed px-4 py-5 text-center transition-colors ${
          dragOver ? 'border-accent bg-accent-soft' : 'border-strong bg-bg'
        }`}
      >
        <p className="text-body text-fg">Drop video files here, or click to browse</p>
        <p className="mt-1 text-small text-subtle">MP4, MOV, MKV, WebM or M4V — multiple files welcome</p>
        <input
          ref={fileRef}
          type="file"
          multiple
          accept="video/*,.mp4,.mov,.mkv,.webm,.m4v"
          aria-label="Video files to upload"
          onChange={(e) => takeFiles(e.target.files)}
          className="hidden"
        />
      </div>

      <input
        type="text"
        value={association}
        onChange={(e) => setAssociation(e.target.value)}
        placeholder="Optional: paste the published YouTube URL to track its performance (single file only)"
        aria-label="Associate with a published YouTube video (optional)"
        className="mt-2.5 h-10 w-full rounded-sm border border-strong bg-bg px-3.5 text-body text-fg placeholder:text-subtle focus:border-accent focus:outline-none"
      />

      {items.length > 0 && (
        <ul className="mt-2.5 space-y-2">
          {items.map((item) => (
            <li key={item.id} className="rounded-sm border border-strong bg-bg px-3 py-2">
              <div className="flex items-center justify-between gap-2">
                <span className="min-w-0 flex-1 truncate text-body text-fg">{item.name}</span>
                <span className="shrink-0 text-small text-subtle">
                  {item.status === 'uploading'
                    ? `${item.progressPct}%`
                    : (item.error ?? STATUS_LABEL[item.status])}
                </span>
                {item.status === 'error' && (
                  <Button size="sm" variant="outline" onClick={() => retryFile(item.id)}>
                    Retry
                  </Button>
                )}
                {item.status !== 'done' && item.status !== 'uploading' && (
                  <Button size="sm" variant="ghost" onClick={() => removeFile(item.id)}>
                    Remove
                  </Button>
                )}
              </div>
              {(item.status === 'uploading' || item.status === 'paused') && (
                <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-surface">
                  <div
                    className="h-full rounded-full bg-accent transition-all"
                    style={{ width: `${item.progressPct}%` }}
                  />
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {sessionExpired && (
        <p className="mt-2.5 rounded-sm border border-strong bg-bg px-3 py-2 text-small text-fg">
          Your session expired mid-upload — nothing is lost.{' '}
          <a
            href="/app/login"
            target="_blank"
            rel="noreferrer"
            className="text-accent-text underline"
          >
            Sign in again
          </a>{' '}
          in a new tab, then press Retry: already-uploaded parts are kept for 7 days and are not
          re-sent.
        </p>
      )}

      <div className="mt-2.5 flex items-center gap-2.5">
        <Button onClick={begin} disabled={!ready || active}>
          {active ? 'Uploading…' : 'Upload'}
        </Button>
        <p className="text-small text-subtle">
          {notice || (
            <>
              Uploads go straight to storage — you can keep working while they run. We never
              download from YouTube. Already recording?{' '}
              <span className="text-accent-text">Connect OBS</span> and local recordings ingest
              automatically.
            </>
          )}
        </p>
      </div>
    </div>
  )
}
