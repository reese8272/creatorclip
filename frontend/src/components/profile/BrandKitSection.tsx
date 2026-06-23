import { useEffect, useState } from 'react'
import { api, ApiError } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Card, CardBody, CardHeader } from '@/components/ui/card'
import type { BrandKit } from '@/types'

const selectCls =
  'rounded-md border border-default bg-surface px-2 py-1.5 text-sm text-fg focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent-soft'

const defaultKit: BrandKit = {
  subtitle: null,
  background: null,
  captions_enabled: false,
  zoom_on_peak: false,
  denoise: false,
  aspect: null,
}

export function BrandKitSection() {
  const [kit, setKit] = useState<BrandKit>(defaultKit)
  const [status, setStatus] = useState<{ text: string; tone: 'muted' | 'success' | 'danger' } | null>(null)

  useEffect(() => {
    api<BrandKit>('/creators/me/brand-kit')
      .then((data) => setKit(data))
      .catch(() => setKit(defaultKit))
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

  return (
    <Card>
      <CardHeader
        title="Brand Kit"
        description="Default render style applied to every new clip — save time re-picking settings."
      />
      <CardBody>
        <div className="flex flex-col gap-4">
          <label className="flex items-center justify-between gap-3 text-sm text-fg">
            Caption style
            <select
              value={kit.subtitle ?? ''}
              onChange={(e) => setKit({ ...kit, subtitle: e.target.value || null })}
              className={selectCls}
            >
              <option value="">None — no captions</option>
              <option value="bold_pop">Bold Pop — one word, scale-pops</option>
              <option value="bold_pop_highlight">Bold Pop Highlight — keywords in yellow</option>
              <option value="gradient_slide">Gradient Slide — indigo→white fade-in</option>
              <option value="minimal">Minimal — plain phrase captions</option>
            </select>
          </label>

          <label className="flex items-center justify-between gap-3 text-sm text-fg">
            Aspect ratio
            <select
              value={kit.aspect ?? ''}
              onChange={(e) => setKit({ ...kit, aspect: e.target.value || null })}
              className={selectCls}
            >
              <option value="">9:16 — vertical Short (default)</option>
              <option value="1:1">1:1 — square</option>
              <option value="16:9">16:9 — horizontal</option>
            </select>
          </label>

          <label className="flex items-center justify-between gap-3 text-sm text-fg">
            Background fill
            <select
              value={kit.background ?? ''}
              onChange={(e) => setKit({ ...kit, background: e.target.value || null })}
              className={selectCls}
            >
              <option value="">Default (black)</option>
              <option value="blur">Blur</option>
              <option value="black">Black</option>
            </select>
          </label>

          <label className="flex items-center justify-between gap-3 text-sm text-fg">
            Captions on
            <input
              type="checkbox"
              checked={kit.captions_enabled}
              onChange={(e) => setKit({ ...kit, captions_enabled: e.target.checked })}
            />
          </label>

          <label className="flex items-center justify-between gap-3 text-sm text-fg">
            Punch-in at peak
            <input
              type="checkbox"
              checked={kit.zoom_on_peak}
              onChange={(e) => setKit({ ...kit, zoom_on_peak: e.target.checked })}
            />
          </label>

          <label className="flex items-center justify-between gap-3 text-sm text-fg">
            Reduce background noise
            <input
              type="checkbox"
              checked={kit.denoise}
              onChange={(e) => setKit({ ...kit, denoise: e.target.checked })}
            />
          </label>

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
