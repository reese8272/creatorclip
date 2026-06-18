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

// Secondary "link a video outside your catalog" affordance. POSTs form-encoded
// (the endpoint takes Form fields, not JSON) and invalidates the videos query on
// success so the new row appears. Open state is controlled by the parent so the
// empty-state hero can expand + focus it.
export function LinkVideoForm({
  open,
  onToggle,
}: {
  open: boolean
  onToggle: (open: boolean) => void
}) {
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

  return (
    <div className="mb-6">
      <button
        type="button"
        onClick={() => onToggle(!open)}
        aria-expanded={open}
        className="py-1 text-xs text-subtle hover:text-muted"
      >
        + Link a video outside your catalog
      </button>
      {open && (
        <div className="mt-2">
          <div className="flex gap-2">
            <input
              autoFocus
              type="text"
              placeholder="YouTube video ID or URL"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && link()}
              aria-label="YouTube video ID or URL"
              className="flex-1 rounded-md border border-strong bg-bg px-3 py-2 text-sm text-fg placeholder:text-subtle focus:border-accent focus:outline-none"
            />
            <Button variant="secondary" onClick={link}>
              Link video
            </Button>
          </div>
          {status && <p className="mt-2 text-xs text-subtle">{status}</p>}
        </div>
      )}
    </div>
  )
}
