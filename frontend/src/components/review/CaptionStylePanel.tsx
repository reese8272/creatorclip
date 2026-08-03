import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api'
import { subscribeToTaskStream, type StreamSubscription } from '@/lib/taskStream'
import { Button } from '@/components/ui/button'
import { Select, type SelectOption } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import type { BrandKit, ReviewClip } from '@/types'

// Every option list here keeps '' as its default value ("None", "9:16",
// "Default (black)") — the Select primitive maps that to Radix's reserved
// sentinel internally, so these read exactly as the old <option value=""> did.
const SUBTITLE_OPTIONS: SelectOption[] = [
  { value: '', label: 'None — no captions' },
  { value: 'bold_pop', label: 'Bold Pop — one word, scale-pops' },
  { value: 'bold_pop_highlight', label: 'Bold Pop Highlight — keywords in yellow' },
  { value: 'gradient_slide', label: 'Gradient Slide — indigo→white fade-in' },
  { value: 'minimal', label: 'Minimal — plain phrase captions' },
]
const ASPECT_OPTIONS: SelectOption[] = [
  { value: '', label: '9:16 — vertical Short (default)' },
  { value: '1:1', label: '1:1 — square' },
  { value: '16:9', label: '16:9 — horizontal' },
]
const BACKGROUND_OPTIONS: SelectOption[] = [
  { value: '', label: 'Default (black)' },
  { value: 'blur', label: 'Blur' },
  { value: 'black', label: 'Black' },
]

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
        setStatus('Styled render ready')
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
      <div className="flex items-center justify-between gap-3">
        <label htmlFor="caption-style">Caption style</label>
        <Select
          id="caption-style"
          value={subtitle}
          onValueChange={setSubtitle}
          options={SUBTITLE_OPTIONS}
          size="sm"
          className="w-[62%]"
        />
      </div>
      <div className="flex items-center justify-between gap-3">
        <label htmlFor="caption-aspect">Aspect ratio</label>
        <Select
          id="caption-aspect"
          value={aspect}
          onValueChange={setAspect}
          options={ASPECT_OPTIONS}
          size="sm"
          className="w-[62%]"
        />
      </div>
      <div className="flex items-center justify-between gap-3">
        <label htmlFor="caption-background">Background fill</label>
        <Select
          id="caption-background"
          value={background}
          onValueChange={setBackground}
          options={BACKGROUND_OPTIONS}
          size="sm"
          className="w-[62%]"
        />
      </div>
      {/* Switch, not Checkbox: each of these applies to the next render as soon
          as it is toggled — nothing is submitted. The label is a SIBLING with
          htmlFor, because Radix renders a button and a wrapping label with no
          htmlFor would silently stop toggling on text click. */}
      <div className="flex items-center justify-between gap-3">
        <label htmlFor="captions-enabled">Captions on</label>
        <Switch
          id="captions-enabled"
          checked={captionsEnabled}
          onCheckedChange={setCaptionsEnabled}
        />
      </div>
      <div className="flex items-center justify-between gap-3">
        <label htmlFor="zoom-on-peak">Punch-in at peak</label>
        <Switch id="zoom-on-peak" checked={zoomOnPeak} onCheckedChange={setZoomOnPeak} />
      </div>
      <div className="flex items-center justify-between gap-3">
        <label htmlFor="denoise">Reduce background noise</label>
        <Switch id="denoise" checked={denoise} onCheckedChange={setDenoise} />
      </div>
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
