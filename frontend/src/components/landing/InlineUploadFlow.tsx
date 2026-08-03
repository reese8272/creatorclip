import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { videosRefetchInterval } from '@/lib/videosPoll'
import { useStageStream } from '@/hooks/useStageStream'
import { Button } from '@/components/ui/button'
import { Chip } from '@/components/Chip'
import { StageStepper } from '@/components/dashboard/StageStepper'
import { UploadVideoForm } from '@/components/dashboard/UploadVideoForm'
import { GenerateClipsButton } from '@/components/landing/VideoPickerLanding'
import type { ReviewClip, VideoListResponse } from '@/types'

// Upload-in-place card for the standalone Review/Editor landings: the creator
// uploads here and continues here — live pipeline progress, then an explicit
// generate step, then straight into the tool via onReady(videoId).
//
// One piece of local state (the uploaded video id); every phase below is
// derived from the shared ['videos'] cache row plus the single SSE stream this
// card is allowed to hold (server cap is 3 concurrent streams per creator —
// picker rows deliberately never open one). The 5s videos poll is the durable
// fallback if the SSE drops: status still flips within a poll of reality.
//
// Phases: uploading → processing → failed | ready-to-generate → no-clips | exit.
export function InlineUploadFlow({ onReady }: { onReady: (videoId: string) => void }) {
  const queryClient = useQueryClient()
  const [uploadedVideoId, setUploadedVideoId] = useState<string | null>(null)
  const [noClips, setNoClips] = useState(false)

  const videosQuery = useQuery({
    queryKey: ['videos'],
    queryFn: () => api<VideoListResponse>('/videos'),
    refetchInterval: (query) => videosRefetchInterval(query.state.data),
  })
  const row = uploadedVideoId
    ? (videosQuery.data?.videos ?? []).find((v) => v.id === uploadedVideoId)
    : undefined

  // 'pending' before the row lands in the cache: the upload response returns
  // status "pending" and the worker chain starts emitting immediately, so the
  // stream opens right after upload instead of waiting a poll cycle.
  const stream = useStageStream(uploadedVideoId, row?.ingest_status ?? 'pending')

  const requeue = useMutation({
    mutationFn: () => api(`/videos/${uploadedVideoId}/queue`, { method: 'POST' }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['videos'] }),
  })

  function handleClips(clips: ReviewClip[]) {
    if (clips.length > 0 && uploadedVideoId) onReady(uploadedVideoId)
    else setNoClips(true)
  }

  if (!uploadedVideoId) {
    return <UploadVideoForm open onUploaded={setUploadedVideoId} />
  }

  const title = row?.title || 'Your upload'
  const failed = row?.ingest_status === 'failed' || stream.status === 'error'
  const done = row?.ingest_status === 'done'

  return (
    <div className="mb-5 rounded-md border border-accent-border bg-surface px-[18px] py-4 shadow-sm inset-shadow-highlight">
      <div className="mb-2.5 flex items-center gap-2.5">
        <Chip pose="laptop" size={30} />
        <span className="max-w-[420px] truncate text-h3 font-semibold text-fg">{title}</span>
      </div>

      {failed ? (
        <div className="flex flex-col items-start gap-2">
          <StageStepper
            stage={stream.stage}
            label={stream.label}
            status="error"
            failureReason={stream.failureReason ?? row?.failure_reason ?? null}
          />
          <p className="text-small text-muted">
            Something broke during analysis — your minutes are automatically refunded.
          </p>
          <Button
            variant="secondary"
            size="sm"
            disabled={requeue.isPending}
            onClick={() => requeue.mutate()}
          >
            {requeue.isPending ? 'Queuing…' : 'Retry analysis'}
          </Button>
        </div>
      ) : !done ? (
        <div className="flex flex-col items-start gap-2">
          <StageStepper
            stage={stream.stage}
            label={stream.label}
            status={stream.status === 'idle' ? 'streaming' : stream.status}
            isStale={stream.isStale}
          />
          <p className="text-small text-muted">
            You can stay here — we’ll offer clip generation when analysis finishes.
          </p>
        </div>
      ) : noClips ? (
        <p className="text-small text-muted">
          No strong moments matched your clipping principles — nothing was worth clipping from this
          one.{' '}
          <button
            type="button"
            className="text-accent-text hover:underline"
            onClick={() => {
              setNoClips(false)
              setUploadedVideoId(null)
            }}
          >
            Upload a different video
          </button>
        </p>
      ) : (
        <div className="flex flex-col items-start gap-2">
          <p className="text-small text-muted">
            Analysis is done. Generating clips runs AI scoring against your channel DNA and uses
            your minutes.
          </p>
          <GenerateClipsButton videoId={uploadedVideoId} onClips={handleClips} />
        </div>
      )}
    </div>
  )
}
