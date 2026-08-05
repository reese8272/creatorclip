import { useState } from 'react'
import { TITLE_MAX_CHARS, useApplyClipMetadata } from '@/hooks/useApplyClipMetadata'
import { Button } from '@/components/ui/button'
import type { ReviewClip } from '@/types'

// Inline editable "publish title" row. Shows the applied title (set from the
// metadata-panel Apply buttons or typed here); saving PATCHes the same
// /clips/{id} endpoint. Collapsed to a subtle affordance while unset; saving an
// empty value clears the title (publish falls back to the video title).
//
// L26 (Issue 424) additive props: ClipMetadataPanel renders its own compact
// row and mounts this field only while editing — `autoEdit` opens the input
// immediately (one click, not two) and `onClose` lets the host collapse back
// to its row on Cancel/Save. Both optional; behavior without them is unchanged.
export function AppliedTitleField({
  clip,
  autoEdit = false,
  onClose,
}: {
  clip: ReviewClip
  autoEdit?: boolean
  onClose?: () => void
}) {
  const [editing, setEditing] = useState(autoEdit)
  const [value, setValue] = useState(autoEdit ? (clip.applied_title ?? '') : '')
  const [error, setError] = useState('')
  const apply = useApplyClipMetadata(clip)

  function startEditing() {
    setValue(clip.applied_title ?? '')
    setError('')
    setEditing(true)
  }

  async function save() {
    const trimmed = value.trim()
    try {
      await apply.mutateAsync({ applied_title: trimmed === '' ? null : trimmed })
      setEditing(false)
      setError('')
      onClose?.()
    } catch {
      setError('Could not save the title — try again.')
    }
  }

  function cancel() {
    setEditing(false)
    onClose?.()
  }

  if (!editing) {
    if (!clip.applied_title) {
      return (
        <button
          onClick={startEditing}
          className="mt-2.5 w-full rounded-sm border border-dashed border-strong px-3 py-1.5 text-left text-xs text-subtle hover:border-muted hover:text-muted"
        >
          + Set the title this clip publishes with
        </button>
      )
    }
    return (
      <div className="mt-2.5 flex items-center justify-between gap-2 rounded-sm border border-default bg-bg px-3 py-1.5 text-xs">
        <span className="min-w-0 flex-1 truncate text-fg" title={clip.applied_title}>
          <span className="mr-1.5 text-subtle">Publish title:</span>
          {clip.applied_title}
        </span>
        <button
          onClick={startEditing}
          className="shrink-0 text-accent-text underline-offset-2 hover:underline"
        >
          Edit
        </button>
      </div>
    )
  }

  return (
    <div className="mt-2.5 flex flex-col gap-1.5">
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        maxLength={TITLE_MAX_CHARS}
        placeholder="Title this clip publishes with — leave empty to clear"
        aria-label="Publish title"
        className="w-full rounded-sm border border-strong bg-bg px-3 py-2 text-xs text-fg placeholder:text-subtle focus:border-accent focus:outline-none"
      />
      <div className="flex items-center justify-end gap-2">
        <span className="mr-auto font-mono text-2xs text-subtle">
          {value.length}/{TITLE_MAX_CHARS}
        </span>
        {error && <span className="text-xs text-danger">{error}</span>}
        <Button variant="secondary" size="sm" onClick={cancel}>
          Cancel
        </Button>
        <Button size="sm" disabled={apply.isPending} onClick={save}>
          {apply.isPending ? 'Saving…' : 'Save'}
        </Button>
      </div>
    </div>
  )
}
