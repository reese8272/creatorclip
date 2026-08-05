import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api'
import type { ReviewClip } from '@/types'

export interface ClipRenderState {
  /** A render is in flight — server `running` or a click we just made. */
  rendering: boolean
  renderError: string
  /** Retention purge: rendering can never succeed until a re-upload. */
  sourceExpired: boolean
  triggerRender: () => Promise<void>
}

// The render-trigger state machine, lifted verbatim from ClipPlayer (Issue 423)
// so Review and the Editor share one set of render/failure/expired semantics.
//
// Auto-render normally renders clips in the background right after generation,
// so the manual trigger is a fallback/retry affordance. Charges no extra
// minutes (paid at upload). Only `running` (or a click we just made) shows the
// spinner; a `pending` clip keeps the manual button so a never-queued clip
// can't spin forever. The review-clips poll swaps in the video once render_uri
// lands.
export function useClipRender(clip: ReviewClip): ClipRenderState {
  const queryClient = useQueryClient()
  const [renderError, setRenderError] = useState('')
  const [requesting, setRequesting] = useState(false)
  const [sourceExpired, setSourceExpired] = useState(false)

  const rendering = requesting || clip.render_status === 'running'

  async function triggerRender() {
    setRenderError('')
    setRequesting(true)
    try {
      await api(`/clips/${clip.id}/render`, { method: 'POST' })
      await queryClient.invalidateQueries({ queryKey: ['review-clips'] })
    } catch (e) {
      if (e instanceof ApiError && e.code === 'source_expired') {
        // Retention purge: this render can never succeed until the creator
        // re-uploads the source — show the dedicated card, not a retry spinner.
        setSourceExpired(true)
      } else if (e instanceof ApiError && e.status === 409) {
        // Any other 409 = a render is already in progress; treat as success
        // (the poll picks it up).
        await queryClient.invalidateQueries({ queryKey: ['review-clips'] })
      } else {
        setRenderError(e instanceof ApiError ? e.message : 'Render failed — try again.')
      }
    } finally {
      // Clear the optimistic flag once the request settles. From here the
      // spinner is driven by server state (render_status === 'running' set by
      // the worker), so a render that fails fast — e.g. the source media was
      // purged — surfaces as "Render failed" + retry instead of spinning
      // forever. Previously `requesting` was only reset on the error path, so a
      // 202/409 latched the spinner permanently whenever no render_uri ever
      // landed (the "render loop").
      setRequesting(false)
    }
  }

  return { rendering, renderError, sourceExpired, triggerRender }
}
