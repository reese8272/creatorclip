import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api'
import { useCleanedUriPoll } from '@/hooks/useCleanedUriPoll'
import { Button } from '@/components/ui/button'
import type { CleanPreview, ReviewClip } from '@/types'

// Issue 134 — filler + long-silence clean pass: preview the cuts, render a
// cleaned version, then swap it in (or keep the original).
export function CleanPassPanel({ clip }: { clip: ReviewClip }) {
  const queryClient = useQueryClient()
  const [preview, setPreview] = useState<CleanPreview | null>(null)
  const [applying, setApplying] = useState(false)
  const [status, setStatus] = useState('')
  const cleanedUri = useCleanedUriPoll(clip.video_id, clip.id, applying)

  async function loadPreview() {
    setStatus('Analyzing transcript…')
    setPreview(null)
    try {
      const data = await api<CleanPreview>(`/clips/${clip.id}/clean-preview`)
      setPreview(data)
      setStatus('')
    } catch (e) {
      setStatus(e instanceof ApiError ? e.message : 'Preview failed — try again.')
    }
  }

  async function applyClean() {
    setStatus('Queueing clean render — come back in ~20s.')
    try {
      await api(`/clips/${clip.id}/clean`, { method: 'POST' })
      setApplying(true)
    } catch (e) {
      setStatus(e instanceof ApiError ? e.message : 'Clean failed — try again.')
    }
  }

  async function confirmClean() {
    try {
      await api(`/clips/${clip.id}/clean/confirm`, { method: 'POST' })
      setApplying(false)
      setStatus('Cleaned version is now the main render.')
      queryClient.invalidateQueries({ queryKey: ['review-clips', clip.video_id] })
    } catch {
      setStatus('Swap failed — try again.')
    }
  }

  function discard() {
    setApplying(false)
    setStatus('Keeping original render.')
  }

  return (
    <div className="flex flex-col gap-2 text-sm">
      <Button variant="secondary" size="sm" className="w-fit" onClick={loadPreview}>
        Preview what would be removed
      </Button>

      {preview && preview.cuts.length === 0 && (
        <p className="text-subtle">
          No filler words or long silences detected — clip is already clean.
        </p>
      )}

      {preview && preview.cuts.length > 0 && (
        <>
          <p className="text-subtle">
            {preview.cuts.length} segment(s) would be removed —{' '}
            {preview.percent_removed.toFixed(0)}% of the clip.
          </p>
          {preview.warning && <p className="font-semibold text-danger">⚠ {preview.warning}</p>}
          <div className="max-h-[140px] overflow-y-auto rounded-sm border border-default p-2 text-xs">
            {preview.cuts.map((c, i) => (
              <div key={i} className="text-subtle line-through">
                {c.start_s.toFixed(2)}s–{c.end_s.toFixed(2)}s · {(c.end_s - c.start_s).toFixed(2)}s ·{' '}
                {c.reason === 'filler' ? `filler: "${c.word}"` : 'silence'}
              </div>
            ))}
          </div>
          {!cleanedUri && (
            <Button size="sm" className="w-fit" disabled={applying} onClick={applyClean}>
              {applying ? 'Cleaning…' : 'Apply — render cleaned version'}
            </Button>
          )}
        </>
      )}

      {cleanedUri && (
        <div>
          <video
            src={cleanedUri}
            controls
            className="mt-2 w-full rounded-sm border border-default"
          />
          <div className="mt-2 flex gap-2">
            <Button size="sm" onClick={confirmClean}>
              Use cleaned version
            </Button>
            <Button variant="secondary" size="sm" onClick={discard}>
              Keep original
            </Button>
          </div>
        </div>
      )}

      {status && <div className="text-xs text-subtle">{status}</div>}
    </div>
  )
}
