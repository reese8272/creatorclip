import { useEffect, useState } from 'react'
import { api, ApiError } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Card, CardBody, CardHeader } from '@/components/ui/card'
import { Select, type SelectOption } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import type { BrandKit } from '@/types'

// Same option sets the per-clip panel offers (components/review/CaptionStylePanel),
// since a brand kit is just the default those controls start from. '' means
// "unset" and is stored as null.
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

const defaultKit: BrandKit = {
  subtitle: null,
  background: null,
  captions_enabled: false,
  zoom_on_peak: false,
  denoise: false,
  aspect: null,
}

interface StyleSuggestion {
  field: string
  value: unknown
  count: number
  message: string
}

export function BrandKitSection() {
  const [kit, setKit] = useState<BrandKit>(defaultKit)
  const [status, setStatus] = useState<{ text: string; tone: 'muted' | 'success' | 'danger' } | null>(null)
  const [suggestion, setSuggestion] = useState<StyleSuggestion | null>(null)
  const [suggestionDismissed, setSuggestionDismissed] = useState(false)

  useEffect(() => {
    api<BrandKit>('/creators/me/brand-kit')
      .then((data) => setKit(data))
      .catch(() => setKit(defaultKit))
  }, [])

  useEffect(() => {
    // Fetch style-learning suggestion on mount (Issue 187).
    // 204 = no suggestion yet; any other error is silently swallowed so
    // the main brand-kit UI is never blocked by this optional feature.
    fetch('/creators/me/brand-kit/suggestion', { credentials: 'include' })
      .then((res) => {
        if (res.status === 200) return res.json() as Promise<StyleSuggestion>
        return null
      })
      .then((data) => { if (data) setSuggestion(data) })
      .catch(() => { /* non-blocking — suggestion is optional */ })
  }, [])

  const save = async () => {
    setStatus(null)
    try {
      const saved = await api<BrandKit>('/creators/me/brand-kit', {
        method: 'PUT',
        body: kit,
      })
      setKit(saved)
      setStatus({ text: 'Brand kit saved.', tone: 'success' })
    } catch (e) {
      setStatus({
        text: e instanceof ApiError ? e.message : 'Save failed — try again.',
        tone: 'danger',
      })
    }
  }

  const acceptSuggestion = async () => {
    if (!suggestion) return
    try {
      const updated = await api<BrandKit>('/creators/me/brand-kit/suggestion/accept', {
        method: 'POST',
        body: { field: suggestion.field, value: suggestion.value },
      })
      setKit(updated)
      setSuggestion(null)
      setStatus({ text: 'Default updated from your render history.', tone: 'success' })
    } catch (e) {
      setStatus({
        text: e instanceof ApiError ? e.message : 'Could not apply suggestion — try again.',
        tone: 'danger',
      })
    }
  }

  const dismissSuggestion = () => setSuggestionDismissed(true)

  return (
    <Card>
      <CardHeader
        title="Brand Kit"
        description="Default render style applied to every new clip — save time re-picking settings."
      />
      <CardBody>
        <div className="flex flex-col gap-4">
          {suggestion && !suggestionDismissed && (
            <div className="flex items-start justify-between gap-3 rounded-md border border-accent-soft bg-accent-subtle px-3 py-2 text-sm text-fg">
              <span>{suggestion.message}</span>
              <div className="flex shrink-0 gap-2">
                <Button variant="primary" size="sm" onClick={acceptSuggestion}>
                  Accept
                </Button>
                <Button variant="ghost" size="sm" onClick={dismissSuggestion}>
                  Dismiss
                </Button>
              </div>
            </div>
          )}
          <div className="flex items-center justify-between gap-3 text-sm text-fg">
            <label htmlFor="kit-subtitle">Caption style</label>
            <Select
              id="kit-subtitle"
              value={kit.subtitle ?? ''}
              onValueChange={(v) => setKit({ ...kit, subtitle: v || null })}
              options={SUBTITLE_OPTIONS}
              className="w-[58%]"
            />
          </div>

          <div className="flex items-center justify-between gap-3 text-sm text-fg">
            <label htmlFor="kit-aspect">Aspect ratio</label>
            <Select
              id="kit-aspect"
              value={kit.aspect ?? ''}
              onValueChange={(v) => setKit({ ...kit, aspect: v || null })}
              options={ASPECT_OPTIONS}
              className="w-[58%]"
            />
          </div>

          <div className="flex items-center justify-between gap-3 text-sm text-fg">
            <label htmlFor="kit-background">Background fill</label>
            <Select
              id="kit-background"
              value={kit.background ?? ''}
              onValueChange={(v) => setKit({ ...kit, background: v || null })}
              options={BACKGROUND_OPTIONS}
              className="w-[58%]"
            />
          </div>

          {/* Label as a SIBLING with htmlFor — Radix renders a button, and a
              wrapping label without htmlFor stops toggling on text click. */}
          <div className="flex items-center justify-between gap-3 text-sm text-fg">
            <label htmlFor="kit-captions">Captions on</label>
            <Switch
              id="kit-captions"
              checked={kit.captions_enabled}
              onCheckedChange={(v) => setKit({ ...kit, captions_enabled: v })}
            />
          </div>

          <div className="flex items-center justify-between gap-3 text-sm text-fg">
            <label htmlFor="kit-zoom">Punch-in at peak</label>
            <Switch
              id="kit-zoom"
              checked={kit.zoom_on_peak}
              onCheckedChange={(v) => setKit({ ...kit, zoom_on_peak: v })}
            />
          </div>

          <div className="flex items-center justify-between gap-3 text-sm text-fg">
            <label htmlFor="kit-denoise">Reduce background noise</label>
            <Switch
              id="kit-denoise"
              checked={kit.denoise}
              onCheckedChange={(v) => setKit({ ...kit, denoise: v })}
            />
          </div>

          <div className="flex items-center gap-3">
            <Button variant="primary" size="sm" onClick={save}>
              Save brand kit
            </Button>
            {status && (
              <span
                className={
                  status.tone === 'success'
                    ? 'text-sm text-success'
                    : status.tone === 'danger'
                      ? 'text-sm text-danger'
                      : 'text-sm text-subtle'
                }
              >
                {status.text}
              </span>
            )}
          </div>
        </div>
      </CardBody>
    </Card>
  )
}
