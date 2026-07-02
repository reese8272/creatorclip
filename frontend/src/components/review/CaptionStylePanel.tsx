import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api'
import { subscribeToTaskStream, type StreamSubscription } from '@/lib/taskStream'
import { Button } from '@/components/ui/button'
import type { BrandKit, ReviewClip } from '@/types'

const selectCls =
  'rounded-sm border border-strong bg-bg px-2 py-1 text-xs text-fg focus:border-accent focus:outline-none'

// POST /clips/{id}/render response envelope (routers/clips.py RenderQueuedOut).
interface RenderQueued {
  task_id: string
  status: string
  stream_url: string | null
}

// The server rejects a 4th concurrent SSE stream with this named error event
// (routers/tasks.py) — the render itself is still queued and running.
const SSE_CAP_MESSAGE = 'too many open streams'

// Issue 119 + 133 — animated caption styles baked into a re-render.
// Issue 186 — defaults pre-populated from the creator's saved brand kit.
// Issue 352 Batch K (carried from the 2026-06-24 e2e assessment): subscribe to
// the render task's owner-stamped SSE returned by the POST, so the styled
// render_uri surfaces via query invalidation instead of "come back in ~30s".
export function CaptionStylePanel({ clip }: { clip: ReviewClip }) {
  const queryClient = useQueryClient()
  const [subtitle, setSubtitle] = useState('')
  const [background, setBackground] = useState('')
  const [captionsEnabled, setCaptionsEnabled] = useState(false)
  const [zoomOnPeak, setZoomOnPeak] = useState(false)
  const [denoise, setDenoise] = useState(false)
  const [aspect, setAspect] = useState('')
  const [status, setStatus] = useState('')
  const [rendering, setRendering] = useState(false)

  // Live render progress via the task SSE (DnaCard idiom: subscribe in the
  // event handler, close on unmount).
  const subRef = useRef<StreamSubscription | null>(null)
  useEffect(() => () => subRef.current?.close(), [])

  function follow(streamUrl: string) {
    subRef.current?.close()
    subRef.current = subscribeToTaskStream(streamUrl, {
      onDone: () => {
        setRendering(false)
        setStatus('Styled render ready ✓')
        // Editor + Review read the clip list under this key — refetch so the
        // new render_uri (and render_status) surface without a manual refresh.
        void queryClient.invalidateQueries({ queryKey: ['review-clips', clip.video_id] })
      },
      onError: (message) => {
        setRendering(false)
        setStatus(
          message === SSE_CAP_MESSAGE
            ? 'Render queued — live progress unavailable right now; check back in ~30s.'
            : `Render failed — ${message || 'try again.'}`,
        )
      },
    })
  }

  // Pre-populate from the creator's brand kit on mount.
  useEffect(() => {
    api<BrandKit>('/creators/me/brand-kit')
      .then((kit) => {
        setSubtitle(kit.subtitle ?? '')
        setBackground(kit.background ?? '')
        setCaptionsEnabled(kit.captions_enabled)
        setZoomOnPeak(kit.zoom_on_peak)
        setDenoise(kit.denoise)
        setAspect(kit.aspect ?? '')
      })
      .catch(() => {
        // Brand-kit load failure is non-fatal — keep empty defaults.
      })
  }, [])

  async function apply() {
    setStatus('Queueing styled render…')
    try {
      const queued = await api<RenderQueued>(`/clips/${clip.id}/render`, {
        method: 'POST',
        body: {
          subtitle: subtitle || null,
          background: background || null,
          captions_enabled: captionsEnabled,
          zoom_on_peak: zoomOnPeak,
          denoise: denoise,
          aspect: aspect || null,
        },
      })
      if (queued.stream_url) {
        // Follow the render live; the SSE handlers land the result.
        setStatus('Rendering…')
        setRendering(true)
        follow(queued.stream_url)
      } else {
        // Redis blip fail-open path: the job is queued but has no stream.
        setStatus('Render queued — come back in ~30s.')
      }
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
          <option value="bold_pop_highlight">Bold Pop Highlight — keywords in yellow</option>
          <option value="gradient_slide">Gradient Slide — indigo→white fade-in</option>
          <option value="minimal">Minimal — plain phrase captions</option>
        </select>
      </label>
      <label className="flex items-center justify-between gap-3">
        Aspect ratio
        <select value={aspect} onChange={(e) => setAspect(e.target.value)} className={selectCls}>
          <option value="">9:16 — vertical Short (default)</option>
          <option value="1:1">1:1 — square</option>
          <option value="16:9">16:9 — horizontal</option>
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
      <label className="flex items-center justify-between gap-3">
        Punch-in at peak
        <input
          type="checkbox"
          checked={zoomOnPeak}
          onChange={(e) => setZoomOnPeak(e.target.checked)}
        />
      </label>
      <label className="flex items-center justify-between gap-3">
        Reduce background noise
        <input type="checkbox" checked={denoise} onChange={(e) => setDenoise(e.target.checked)} />
      </label>
      <Button
        variant="secondary"
        size="sm"
        className="mt-1 w-fit"
        onClick={apply}
        disabled={rendering}
      >
        Render with style
      </Button>
      {status && <div className="text-subtle">{status}</div>}
    </div>
  )
}
