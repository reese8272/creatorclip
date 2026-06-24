import { useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'

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

// Inline "upload a video" panel (Issue 317 — replaces the retired paste-a-URL
// LinkVideoForm). The raw file IS the source media: under the YouTube ToS we
// never download from a link, so a link alone could only sit at "pending"
// forever. Uploads multipart/form-data to /videos/upload (the endpoint takes
// Form fields, not JSON) and invalidates the videos query on success so the new
// row appears with its live ingest stepper.
//
// `youtube_video_id` is optional: filling the association field ties the upload
// to a published video so its later performance feeds the outcome loop; leaving
// it blank uploads standalone footage (e.g. an OBS recording or unpublished
// cut). The in-app channel picker (Issue 310) will replace manual entry.
export function UploadVideoForm({ open }: { open: boolean }) {
  const queryClient = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const [association, setAssociation] = useState('')
  const [status, setStatus] = useState('')
  const [progress, setProgress] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)

  function upload() {
    const file = fileRef.current?.files?.[0]
    if (!file) {
      setStatus('Choose a video file to upload.')
      return
    }

    const form = new FormData()
    form.append('file', file)
    const ytId = extractYouTubeId(association.trim())
    if (ytId) form.append('youtube_video_id', ytId)

    // XMLHttpRequest (not fetch) so we can surface upload progress — a
    // multi-hundred-MB source file would otherwise look frozen.
    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/videos/upload')
    xhr.withCredentials = true

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) setProgress(Math.round((e.loaded / e.total) * 100))
    }
    xhr.onload = () => {
      setBusy(false)
      setProgress(null)
      let data: { video_id?: string; detail?: string } = {}
      try {
        data = JSON.parse(xhr.responseText)
      } catch {
        /* non-JSON error body */
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        setStatus(`Uploaded — analysing now.`)
        setAssociation('')
        if (fileRef.current) fileRef.current.value = ''
        queryClient.invalidateQueries({ queryKey: ['videos'] })
      } else {
        setStatus(data.detail || `Upload failed (${xhr.status}).`)
      }
    }
    xhr.onerror = () => {
      setBusy(false)
      setProgress(null)
      setStatus('Upload failed — connection lost. Please retry.')
    }

    setBusy(true)
    setProgress(0)
    setStatus('Uploading…')
    xhr.send(form)
  }

  if (!open) return null

  return (
    <div className="mb-5 animate-slide-up rounded-md border border-accent-border bg-surface px-[18px] py-4 shadow-sm shadow-inset">
      <div className="flex flex-wrap items-center gap-2.5">
        <input
          ref={fileRef}
          type="file"
          accept="video/*,.mp4,.mov,.mkv,.webm"
          aria-label="Video file to upload"
          disabled={busy}
          className="h-10 min-w-[240px] flex-1 rounded-sm border border-strong bg-bg px-3.5 py-2 text-body text-fg file:mr-3 file:rounded-sm file:border-0 file:bg-accent-soft file:px-3 file:py-1 file:text-accent-text focus:border-accent focus:outline-none"
        />
        <Button onClick={upload} disabled={busy}>
          {busy ? 'Uploading…' : 'Upload'}
        </Button>
      </div>
      <input
        type="text"
        value={association}
        onChange={(e) => setAssociation(e.target.value)}
        placeholder="Optional: paste the published YouTube URL to track its performance"
        aria-label="Associate with a published YouTube video (optional)"
        disabled={busy}
        className="mt-2.5 h-10 w-full rounded-sm border border-strong bg-bg px-3.5 text-body text-fg placeholder:text-subtle focus:border-accent focus:outline-none"
      />
      {progress !== null && (
        <div className="mt-2.5 h-1.5 w-full overflow-hidden rounded-full bg-bg">
          <div
            className="h-full rounded-full bg-accent transition-all"
            style={{ width: `${progress}%` }}
          />
        </div>
      )}
      <p className="mt-2.5 text-small text-subtle">
        {status || (
          <>
            Upload your source file — we never download from YouTube. Already recording?{' '}
            <span className="text-accent-text">Connect OBS</span> and local recordings ingest
            automatically.
          </>
        )}
      </p>
    </div>
  )
}
