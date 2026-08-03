import { useState } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { ToolMain, ToolShell } from '@/components/layout/ToolShell'
import { Button } from '@/components/ui/button'
import { Chip } from '@/components/Chip'
import { LongFormEditor } from '@/components/editor/LongFormEditor'
import { ShortFormEditor } from '@/components/editor/ShortFormEditor'
import { QueryErrorState } from '@/components/QueryErrorState'
import { VideoPickerLanding } from '@/components/landing/VideoPickerLanding'
import type { ReviewClip, ReviewClipListResponse, VideoListResponse } from '@/types'
import { RectangleHorizontal, RectangleVertical } from '@/components/ui/icon'
import { ICON_SIZE } from '@/components/ui/iconSizes'

// ── Editor page ──────────────────────────────────────────────────────────────

/**
 * Editor route — Issue 188, restructured by Issue 389.
 *
 * This is route plumbing plus the short/long mode toggle. The two workspaces are
 * components: ShortFormEditor (single-clip refine) and LongFormEditor (full source
 * timeline). Both render inside ToolShell, which owns the <main> landmark and the
 * docked honesty statement.
 *
 * Opened via Review's "Refine →" button for the current clip, or straight from the
 * nav with no clip, in which case long-form source mode opens (Issue 307).
 */
export function Editor() {
  const [params, setParams] = useSearchParams()
  const videoId = params.get('video_id')
  const clipId = params.get('clip_id')
  const navigate = useNavigate()
  // Issue 307: short-form clip editor vs long-form source editor. Default to the
  // mode implied by the URL — a clip_id means "refine this clip" (short).
  const [editorMode, setEditorMode] = useState<'short' | 'long'>(clipId ? 'short' : 'long')

  // ── Clip data ────────────────────────────────────────────────────────────

  const {
    data: clipsData,
    isPending: clipsPending,
    isError: clipsError,
    refetch: refetchClips,
  } = useQuery({
    queryKey: ['review-clips', videoId],
    queryFn: () => api<ReviewClipListResponse>(`/videos/${videoId}/clips`),
    enabled: !!videoId,
  })

  const clips: ReviewClip[] = clipsData?.clips ?? []
  const clip: ReviewClip | undefined = clipId
    ? clips.find((c) => c.id === clipId)
    : clips[0]

  // Videos-list row for long-form mode (Issue 372): real source duration +
  // clippable (source presence). Shared ['videos'] cache with Dashboard/landing.
  const videosQuery = useQuery({
    queryKey: ['videos'],
    queryFn: () => api<VideoListResponse>('/videos'),
    enabled: !!videoId,
  })
  const videoRow = (videosQuery.data?.videos ?? []).find((v) => v.id === videoId)

  // ── Guard states ────────────────────────────────────────────────────────

  // Every branch renders inside ToolShell, so the honesty statement and the legal
  // links are structurally impossible to omit — including the loading and error
  // states, which each carried their own copy of the band before Issue 389.
  function message(text: string) {
    return (
      <ToolShell>
        <ToolMain>
          <p className="text-center text-sm text-muted">{text}</p>
        </ToolMain>
      </ToolShell>
    )
  }

  // Editor is a top-nav destination (Issue 304): a bare /editor with no video
  // lands on the standalone picker + upload-in-place landing. With a video but
  // no clip, we open long-form source mode (Issue 307).
  if (!videoId) {
    return (
      <ToolShell>
        <VideoPickerLanding tool="editor" />
      </ToolShell>
    )
  }
  if (clipsPending) return message('Loading…')
  // A failed load must NOT fall through to the no-clips UI — a creator whose
  // clips exist would be told there are none (Recap retry idiom, Issue 361 sweep).
  if (clipsError) {
    return (
      <ToolShell>
        <ToolMain>
          <QueryErrorState
            title="Couldn’t load clips for this video."
            onRetry={() => void refetchClips()}
          />
        </ToolMain>
      </ToolShell>
    )
  }

  // Open a long-form candidate as a short-form clip: point the URL at it + switch.
  // A const arrow, not a function declaration: declarations hoist above the
  // `if (!videoId) return` guard, so TypeScript cannot narrow videoId inside one.
  const openClipInShortEditor = (id: string) => {
    setParams({ video_id: videoId, clip_id: id })
    setEditorMode('short')
  }

  const TAB_BASE =
    'rounded-sm px-3 py-1.5 text-xs font-medium transition-colors duration-fast ease-standard'
  const TAB_ACTIVE = 'bg-accent-soft text-accent-text'
  const TAB_IDLE = 'bg-transparent text-muted hover:text-fg'

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    // scroll={false}: <main> clips and the workspace regions own their own
    // scrolling, so the timeline can never leave the viewport while you edit
    // against it (Issue 389). <main> is itself the flex column, which gives the
    // workspace a definite height to divide up.
    <ToolShell scroll={false} className="flex flex-col gap-3 px-4 py-3 lg:px-6">
      <>
        {/* Header + mode toggle (Issue 307) */}
        <div className="flex shrink-0 flex-wrap items-end justify-between gap-4">
          <div>
            {/* text-h2, and the sub-line only at xl: the header is chrome above
                the work, so it gives up ~30px of vertical budget to the panels. */}
            <h1 className="font-display text-h2 text-fg">Editor</h1>
            <p className="mt-1 hidden text-small text-muted xl:block">
              Refine a single clip, or work the full source timeline.
            </p>
          </div>
          <div
            role="tablist"
            aria-label="Editor mode"
            className="inline-flex gap-0.5 rounded-md border border-strong bg-bg p-[3px]"
          >
            <button
              role="tab"
              aria-selected={editorMode === 'short'}
              onClick={() => setEditorMode('short')}
              className={cn(TAB_BASE, editorMode === 'short' ? TAB_ACTIVE : TAB_IDLE)}
            >
              <RectangleVertical className={ICON_SIZE.sm} aria-hidden="true" /> Short-form clip
            </button>
            <button
              role="tab"
              aria-selected={editorMode === 'long'}
              onClick={() => setEditorMode('long')}
              className={cn(TAB_BASE, editorMode === 'long' ? TAB_ACTIVE : TAB_IDLE)}
            >
              <RectangleHorizontal className={ICON_SIZE.sm} aria-hidden="true" /> Long-form source
            </button>
          </div>
        </div>

        {editorMode === 'long' ? (
          <LongFormEditor
            className="min-h-0 flex-1"
            clips={clips}
            videoId={videoId}
            video={videoRow}
            onOpenClip={openClipInShortEditor}
          />
        ) : !clip ? (
          <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 text-center">
            <Chip pose="confused" size={64} />
            <p className="max-w-md text-body text-muted">
              No clip selected. Switch to <strong className="text-fg">Long-form source</strong> to
              pick a candidate, or head back to Review.
            </p>
            <Button variant="secondary" onClick={() => navigate(`/review?video_id=${videoId}`)}>
              Go to Review
            </Button>
          </div>
        ) : (
          <ShortFormEditor className="min-h-0 flex-1" clip={clip} videoId={videoId} />
        )}
      </>
    </ToolShell>
  )
}
