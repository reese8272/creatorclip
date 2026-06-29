import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { StageStepper } from '@/components/dashboard/StageStepper'
import { useStageStream } from '@/hooks/useStageStream'
import type { AnalysisMode, IngestStatus, Video } from '@/types'

export interface ClipInfo {
  total: number
  rendered: number
  loading: boolean
}

const STATUS_VARIANT: Record<IngestStatus, 'muted' | 'warning' | 'success' | 'danger'> = {
  pending: 'muted',
  running: 'warning',
  done: 'success',
  failed: 'danger',
}

// Issue 100: self-explaining hover copy for the static status badge, mirroring the
// plain-language definitions in the first-run Walkthrough (panel 04 "what those
// badges mean"). In-flight videos get the labeled StageStepper instead (Issue 214).
const STATUS_HELP: Record<IngestStatus, string> = {
  pending: "Waiting in line — we'll start any second.",
  running: 'Ingesting, transcribing, and finding signals (~2–5 min on a 20-min video).',
  done: 'Clips are scored — “Generate clips” / “Review” is your next move.',
  failed: 'Something broke; your minutes are automatically refunded.',
}

const SOURCE_NEEDED_HELP =
  'We never download your video from YouTube (per their Terms of Service). To generate clips, ' +
  'upload the original file — for example export it from Google Takeout — and it will process ' +
  'automatically.'

// SPA analysis route (85e ported). React Router prefixes the /app basename.
function titlesUrl(v: Video): string {
  return `/analysis?video_id=${v.id}&video_title=${encodeURIComponent(v.title || '')}`
}

// A single video row. Owns the local button state for the Queue / Generate
// actions (Queuing… → Queued ✓ → Retry) and invalidates the videos query so the
// list reflects the new status.
function VideoRow({
  video,
  clipInfo,
  analysisMode,
}: {
  video: Video
  clipInfo: ClipInfo | undefined
  analysisMode: AnalysisMode
}) {
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [label, setLabel] = useState<string | null>(null)

  async function act(url: string, pending: string, done: string) {
    setBusy(true)
    setLabel(pending)
    const resp = await fetch(url, { method: 'POST', credentials: 'include' })
    if (resp.ok) {
      setLabel(done)
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ['videos'] }), 3000)
    } else {
      setBusy(false)
      setLabel('Retry')
    }
  }

  const streamState = useStageStream(
    video.clippable ? video.id : null,
    video.ingest_status,
  )

  // Show the live StageStepper only while actively in-flight and the SSE hook
  // has opened a connection (streaming). Otherwise fall back to the static Badge
  // so done/failed rows never hold an SSE slot.
  const showStepper = streamState.status === 'streaming'

  return (
    <tr className="border-b border-default hover:bg-elevated">
      <td className="px-4 py-3.5 align-middle">
        <div className="max-w-[280px] truncate text-fg">{video.title || '—'}</div>
        <div className="font-mono text-xs text-subtle">
          {video.kind} · {video.youtube_video_id}
        </div>
      </td>
      <td className="px-3 py-3.5 align-middle">
        {showStepper ? (
          <StageStepper
            stage={streamState.stage}
            label={streamState.label}
            status={streamState.status}
            isStale={streamState.isStale}
            failureReason={streamState.failureReason}
          />
        ) : (
          <span
            title={
              video.ingest_status === 'failed' && video.failure_reason
                ? video.failure_reason
                : STATUS_HELP[video.ingest_status]
            }
            className="cursor-help"
          >
            <Badge variant={STATUS_VARIANT[video.ingest_status]}>{video.ingest_status}</Badge>
            {video.ingest_status === 'failed' && video.failure_reason && (
              <span className="mt-1 block max-w-[260px] text-xs font-normal text-subtle">
                {video.failure_reason}
              </span>
            )}
          </span>
        )}
      </td>
      <td className="px-3 py-3.5 align-middle">
        <ClipsCell video={video} clipInfo={clipInfo} />
      </td>
      <td className="px-4 py-3.5 align-middle">
        <ActionCell
          video={video}
          clipInfo={clipInfo}
          analysisMode={analysisMode}
          busy={busy}
          label={label}
          onQueue={() =>
            act(`/videos/${video.id}/queue`, 'Queuing…', 'Queued ✓')
          }
          onGenerate={() =>
            act(`/videos/${video.id}/clips/generate`, 'Generating…', 'Queued ✓')
          }
        />
      </td>
    </tr>
  )
}

// Clips column (Issue 305): surface the rendered count that already lives in
// clipInfoByVideo. Em-dash while the pipeline hasn't finished; "0" for a done
// video with no clips; "{rendered} rendered" once clips exist.
function ClipsCell({ video, clipInfo }: { video: Video; clipInfo: ClipInfo | undefined }) {
  if (video.ingest_status !== 'done') {
    return <span className="font-mono text-sm text-subtle">—</span>
  }
  if (!clipInfo || clipInfo.loading) {
    return <span className="font-mono text-sm text-subtle">…</span>
  }
  if (clipInfo.total === 0) {
    return <span className="font-mono text-base font-semibold text-muted">0</span>
  }
  return (
    <span>
      <span className="font-mono text-base font-semibold text-fg">{clipInfo.rendered}</span>
      <span className="text-xs text-subtle"> rendered</span>
    </span>
  )
}

function ActionCell({
  video,
  clipInfo,
  analysisMode,
  busy,
  label,
  onQueue,
  onGenerate,
}: {
  video: Video
  clipInfo: ClipInfo | undefined
  analysisMode: AnalysisMode
  busy: boolean
  label: string | null
  onQueue: () => void
  onGenerate: () => void
}) {
  const titlesLink = (
    <Link to={titlesUrl(video)}>
      <Button variant="secondary" size="sm">
        Titles
      </Button>
    </Link>
  )

  if (video.ingest_status === 'pending' && !video.clippable) {
    // Issue 139: linked video with no stored source — honest upload path, not a
    // queue CTA that would fail ingest.
    return (
      <span
        title={SOURCE_NEEDED_HELP}
        className="cursor-help border-b border-dotted border-default text-sm text-subtle"
      >
        Upload source file to clip
      </span>
    )
  }

  if (video.ingest_status === 'pending') {
    return (
      <Button
        variant={analysisMode === 'auto' ? 'secondary' : 'primary'}
        size="sm"
        disabled={busy}
        onClick={onQueue}
      >
        {label ?? 'Queue for analysis'}
      </Button>
    )
  }

  // Failed: give the creator a way to act on the "please try again" message
  // instead of leaving the row to linger as terminal clutter. Re-queue restarts
  // the pipeline (POST /videos/{id}/queue now accepts a failed video). A failed
  // video with no stored source can't be re-run — point at the upload path.
  if (video.ingest_status === 'failed') {
    if (!video.clippable) {
      return (
        <span
          title={SOURCE_NEEDED_HELP}
          className="cursor-help border-b border-dotted border-default text-sm text-subtle"
        >
          Upload source file to clip
        </span>
      )
    }
    return (
      <Button variant="secondary" size="sm" disabled={busy} onClick={onQueue}>
        {label ?? 'Retry'}
      </Button>
    )
  }

  if (video.ingest_status === 'done') {
    if (!clipInfo || clipInfo.loading) {
      return <span className="text-sm text-subtle">…</span>
    }
    if (clipInfo.total > 0) {
      // Count now lives in the Clips column (Issue 305) — the action is just "Review".
      return (
        <div className="flex flex-wrap gap-2">
          <Link to={`/review?video_id=${video.id}`}>
            <Button size="sm">Review</Button>
          </Link>
          <Link to={`/video/${video.id}`}>
            <Button variant="secondary" size="sm">
              Timeline
            </Button>
          </Link>
          {titlesLink}
        </div>
      )
    }
    // Done + 0 clips: offer Generate CTA and an honest "Why?" link.
    // The "Why?" link navigates to the per-video timeline map where skip_reason_label
    // is rendered in the empty state (Issue 217). The label is grounded in named
    // CLIPPING_PRINCIPLES — no virality language.
    return (
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" disabled={busy} onClick={onGenerate}>
          {label ?? 'Generate clips'}
        </Button>
        <Link
          to={`/video/${video.id}`}
          title="See why no clips were generated for this video"
          className="text-xs text-subtle hover:text-accent-text"
          aria-label="Why weren't clips generated? See explanation"
        >
          Why no clips?
        </Link>
        {titlesLink}
      </div>
    )
  }

  return null
}

export function VideoTable({
  videos,
  clipInfoByVideo,
  analysisMode,
}: {
  videos: Video[]
  clipInfoByVideo: Record<string, ClipInfo>
  analysisMode: AnalysisMode
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr>
            {['Video', 'Status', 'Clips', 'Actions'].map((h) => (
              <th
                key={h}
                className="border-b border-default px-4 py-[11px] text-left text-xs font-medium uppercase tracking-[0.06em] text-subtle"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {videos.map((v) => (
            <VideoRow
              key={v.id}
              video={v}
              clipInfo={clipInfoByVideo[v.id]}
              analysisMode={analysisMode}
            />
          ))}
        </tbody>
      </table>
    </div>
  )
}
