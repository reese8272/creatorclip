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
      <td className="px-3 py-3 align-middle">
        <div className="max-w-[320px] truncate">{video.title || '—'}</div>
        <div className="font-mono text-xs text-subtle">{video.youtube_video_id}</div>
      </td>
      <td className="px-3 py-3 align-middle">{video.kind}</td>
      <td className="px-3 py-3 align-middle">
        {showStepper ? (
          <StageStepper
            stage={streamState.stage}
            label={streamState.label}
            status={streamState.status}
            isStale={streamState.isStale}
            failureReason={streamState.failureReason}
          />
        ) : (
          <Badge variant={STATUS_VARIANT[video.ingest_status]}>{video.ingest_status}</Badge>
        )}
      </td>
      <td className="px-3 py-3 align-middle">
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

  if (video.ingest_status === 'done') {
    if (!clipInfo || clipInfo.loading) {
      return <span className="text-sm text-subtle">…</span>
    }
    if (clipInfo.total > 0) {
      const text =
        clipInfo.rendered === clipInfo.total
          ? `${clipInfo.total} clips`
          : `${clipInfo.rendered}/${clipInfo.total} rendered`
      return (
        <div className="flex flex-wrap gap-2">
          <Link to={`/review?video_id=${video.id}`}>
            <Button variant="secondary" size="sm">
              {text}
            </Button>
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
            {['Title / ID', 'Kind', 'Status', 'Actions'].map((h) => (
              <th
                key={h}
                className="border-b border-default px-3 py-2 text-left text-xs font-medium uppercase tracking-[0.06em] text-subtle"
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
