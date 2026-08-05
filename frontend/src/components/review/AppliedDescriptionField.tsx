import { useState } from 'react'
import {
  DESCRIPTION_MAX_BYTES,
  useApplyClipMetadata,
  utf8ByteLength,
} from '@/hooks/useApplyClipMetadata'
import { Button } from '@/components/ui/button'
import type { ReviewClip } from '@/types'

// Inline editable "publish description / hook" — the AppliedTitleField sibling
// (L26 Issue 424). Saving PATCHes the same /clips/{id} endpoint; saving an
// empty value clears the description (publish falls back to suggested_* or
// "#Shorts"). The counter is BYTES, not characters — YouTube's
// snippet.description limit is 5000 UTF-8 bytes and the backend validates in
// bytes, so a char count would overpromise on non-ASCII text.
export function AppliedDescriptionField({
  clip,
  autoEdit = false,
  onClose,
}: {
  clip: ReviewClip
  autoEdit?: boolean
  onClose?: () => void
}) {
  const [editing, setEditing] = useState(autoEdit)
  const [value, setValue] = useState(autoEdit ? (clip.applied_description ?? '') : '')
  const [error, setError] = useState('')
  const apply = useApplyClipMetadata(clip)

  function startEditing() {
    setValue(clip.applied_description ?? '')
    setError('')
    setEditing(true)
  }

  const bytes = utf8ByteLength(value)

  async function save() {
    const trimmed = value.trim()
    if (utf8ByteLength(trimmed) > DESCRIPTION_MAX_BYTES) {
      setError(`Descriptions are capped at ${DESCRIPTION_MAX_BYTES} bytes on YouTube.`)
      return
    }
    try {
      await apply.mutateAsync({ applied_description: trimmed === '' ? null : trimmed })
      setEditing(false)
      setError('')
      onClose?.()
    } catch {
      setError('Could not save the description — try again.')
    }
  }

  function cancel() {
    setEditing(false)
    onClose?.()
  }

  if (!editing) {
    if (!clip.applied_description) {
      return (
        <button
          onClick={startEditing}
          className="mt-2.5 w-full rounded-sm border border-dashed border-strong px-3 py-1.5 text-left text-xs text-subtle hover:border-muted hover:text-muted"
        >
          + Set the description this clip publishes with
        </button>
      )
    }
    return (
      <div className="mt-2.5 flex items-center justify-between gap-2 rounded-sm border border-default bg-bg px-3 py-1.5 text-xs">
        <span className="min-w-0 flex-1 truncate text-fg" title={clip.applied_description}>
          <span className="mr-1.5 text-subtle">Publish description:</span>
          {clip.applied_description}
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
      <textarea
        value={value}
        onChange={(e) => setValue(e.target.value)}
        rows={3}
        placeholder="Description this clip publishes with — leave empty to clear"
        aria-label="Publish description"
        className="w-full resize-y rounded-sm border border-strong bg-bg px-3 py-2 text-xs text-fg placeholder:text-subtle focus:border-accent focus:outline-none"
      />
      <div className="flex items-center justify-end gap-2">
        <span className="mr-auto font-mono text-2xs text-subtle">
          {bytes}/{DESCRIPTION_MAX_BYTES} bytes
        </span>
        {error && <span className="text-xs text-danger">{error}</span>}
        <Button variant="secondary" size="sm" onClick={cancel}>
          Cancel
        </Button>
        <Button size="sm" disabled={apply.isPending || bytes > DESCRIPTION_MAX_BYTES} onClick={save}>
          {apply.isPending ? 'Saving…' : 'Save'}
        </Button>
      </div>
    </div>
  )
}
