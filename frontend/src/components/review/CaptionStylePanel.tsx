import { useState } from 'react'
import { api, ApiError } from '@/lib/api'
import { Button } from '@/components/ui/button'
import type { ReviewClip } from '@/types'

const selectCls =
  'rounded-sm border border-strong bg-bg px-2 py-1 text-xs text-fg focus:border-accent focus:outline-none'

// Issue 119 + 133 — animated caption styles baked into a re-render.
export function CaptionStylePanel({ clip }: { clip: ReviewClip }) {
  const [subtitle, setSubtitle] = useState('')
  const [background, setBackground] = useState('')
  const [captionsEnabled, setCaptionsEnabled] = useState(false)
  const [status, setStatus] = useState('')

  async function apply() {
    setStatus('Queueing styled render…')
    try {
      await api(`/clips/${clip.id}/render`, {
        method: 'POST',
        body: {
          subtitle: subtitle || null,
          background: background || null,
          captions_enabled: captionsEnabled,
        },
      })
      setStatus('Render queued — come back in ~30s.')
    } catch (e) {
      setStatus(e instanceof ApiError ? e.message : 'Render failed — try again.')
    }
  }

  return (
    <div className="flex flex-col gap-3 text-xs text-muted">
      <label className="flex items-center justify-between gap-3">
        Caption style
        <select value={subtitle} onChange={(e) => setSubtitle(e.target.value)} className={selectCls}>
          <option value="">None — no captions</option>
          <option value="bold_pop">Bold Pop — one word, scale-pops</option>
          <option value="gradient_slide">Gradient Slide — indigo→white fade-in</option>
          <option value="minimal">Minimal — plain phrase captions</option>
        </select>
      </label>
      <label className="flex items-center justify-between gap-3">
        Background fill
        <select
          value={background}
          onChange={(e) => setBackground(e.target.value)}
          className={selectCls}
        >
          <option value="">Default (black)</option>
          <option value="blur">Blur</option>
          <option value="black">Black</option>
        </select>
      </label>
      <label className="flex items-center justify-between gap-3">
        Captions on
        <input
          type="checkbox"
          checked={captionsEnabled}
          onChange={(e) => setCaptionsEnabled(e.target.checked)}
        />
      </label>
      <Button variant="secondary" size="sm" className="mt-1 w-fit" onClick={apply}>
        Render with style
      </Button>
      {status && <div className="text-subtle">{status}</div>}
    </div>
  )
}
