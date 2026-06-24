import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'

// Pull the 11-char video ID out of a watch / youtu.be / shorts URL, or accept a
// bare ID. The server validates again — this is just so a pasted URL works.
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

// Inline "link a video" panel. Issue 305: the toggle now lives in the Dashboard
// header ("Link a video" button) and the EmptyHero, so this renders only the panel
// itself when `open`. POSTs form-encoded (the endpoint takes Form fields, not JSON)
// and invalidates the videos query on success so the new row appears.
export function LinkVideoForm({ open }: { open: boolean }) {
  const queryClient = useQueryClient()
  const [value, setValue] = useState('')
  const [status, setStatus] = useState('')

  async function link() {
    const ytId = extractYouTubeId(value.trim())
    if (!ytId) {
      setStatus('Enter a YouTube video ID or URL.')
      return
    }
    setStatus('Linking…')
    const resp = await fetch('/videos/link', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: `youtube_video_id=${encodeURIComponent(ytId)}`,
    })
    const data = await resp.json().catch(() => ({}))
    if (resp.ok) {
      setStatus(`Linked (${data.video_id}).`)
      setValue('')
      queryClient.invalidateQueries({ queryKey: ['videos'] })
    } else {
      setStatus(data.detail || 'Error linking video.')
    }
  }

  if (!open) return null

  return (
    <div className="mb-5 animate-slide-up rounded-md border border-accent-border bg-surface px-[18px] py-4 shadow-sm shadow-inset">
      <div className="flex flex-wrap gap-2.5">
        <input
          autoFocus
          type="text"
          placeholder="Paste a YouTube URL…"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && link()}
          aria-label="YouTube video ID or URL"
          className="h-10 min-w-[240px] flex-1 rounded-sm border border-strong bg-bg px-3.5 text-body text-fg placeholder:text-subtle focus:border-accent focus:outline-none"
        />
        <Button onClick={link}>Link</Button>
      </div>
      <p className="mt-2.5 text-small text-subtle">
        {status || (
          <>
            We never download from YouTube. Already recording?{' '}
            <span className="text-accent-text">Connect OBS</span> and local recordings ingest
            automatically.
          </>
        )}
      </p>
    </div>
  )
}
