import type { IngestStatus } from '@/types'

// Shared ingest-status presentation constants (VideoTable + the standalone
// Review/Editor landings). Own module so component files export only
// components (react-refresh/only-export-components, repo convention).
export const STATUS_VARIANT: Record<IngestStatus, 'muted' | 'warning' | 'success' | 'danger'> = {
  pending: 'muted',
  running: 'warning',
  done: 'success',
  failed: 'danger',
}

// Issue 139: honest affordance for a linked video with no stored source.
export const SOURCE_NEEDED_HELP =
  'We never download your video from YouTube (per their Terms of Service). To generate clips, ' +
  'upload the original file — for example export it from Google Takeout — and it will process ' +
  'automatically.'
