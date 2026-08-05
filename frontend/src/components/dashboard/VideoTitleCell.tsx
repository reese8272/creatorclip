import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { untitledLabel } from '@/lib/videoMeta'
import type { Video } from '@/types'

export const VIDEO_TITLE_MAX_CHARS = 200

// Inline video rename (Issue 435) — the dashboard analogue of the clip-level
// AppliedTitleField: read row with a Rename affordance, input on edit, PATCH
// /videos/{id} on save (empty clears back to the Untitled fallback). The
// review/editor pickers read the same ['videos'] cache, so a rename propagates
// everywhere without extra wiring.
export function VideoTitleCell({ video }: { video: Video }) {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState('')
  const [error, setError] = useState('')

  const rename = useMutation({
    mutationFn: (title: string | null) =>
      api(`/videos/${video.id}`, { method: 'PATCH', body: { title } }),
    onSuccess: async () => {
      // Awaited so isPending holds until the row shows the fresh name.
      await queryClient.invalidateQueries({ queryKey: ['videos'] })
    },
  })

  function startEditing() {
    setValue(video.title ?? '')
    setError('')
    setEditing(true)
  }

  async function save() {
    const trimmed = value.trim()
    try {
      await rename.mutateAsync(trimmed === '' ? null : trimmed)
      setEditing(false)
    } catch {
      setError('Could not rename — try again.')
    }
  }

  if (editing) {
    return (
      <div className="flex max-w-[220px] items-center gap-1.5">
        <input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void save()
            else if (e.key === 'Escape') setEditing(false)
            else return
            e.preventDefault()
          }}
          maxLength={VIDEO_TITLE_MAX_CHARS}
          placeholder="Video title"
          aria-label="Video title"
          autoFocus
          className="w-full min-w-0 rounded-sm border border-strong bg-bg px-2 py-1 text-sm text-fg placeholder:text-subtle focus:border-accent focus:outline-none"
        />
        <button
          onClick={() => void save()}
          disabled={rename.isPending}
          className="shrink-0 text-xs text-accent-text underline-offset-2 hover:underline disabled:opacity-50"
        >
          {rename.isPending ? 'Saving…' : 'Save'}
        </button>
        {error && <span className="shrink-0 text-xs text-danger">{error}</span>}
      </div>
    )
  }

  return (
    <div className="group/title flex max-w-[220px] items-center gap-1.5">
      <span className="min-w-0 truncate text-fg" title={video.title ?? undefined}>
        {video.title || <span className="italic text-muted">{untitledLabel(video)}</span>}
      </span>
      <button
        onClick={startEditing}
        aria-label={`Rename ${video.title || 'untitled video'}`}
        className="shrink-0 text-xs text-subtle opacity-0 underline-offset-2 hover:underline focus-visible:opacity-100 group-hover/title:opacity-100"
      >
        Rename
      </button>
    </div>
  )
}
