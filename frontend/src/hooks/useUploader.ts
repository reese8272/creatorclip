// React glue over the app-wide Uppy singleton (Issue 395).
//
// Subscribes to the shared instance and mirrors its file table into React
// state. Unmount unsubscribes but never destroys the instance — an in-flight
// multi-GB upload must survive route changes and panel closes.

import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { AppUppy } from '@/lib/uploader'
import { getUploader, onSessionExpired } from '@/lib/uploader'

export interface UploadQueueItem {
  id: string
  name: string
  sizeBytes: number
  /** 0-100; multipart mode counts R2-acked bytes — honest end-to-end progress. */
  progressPct: number
  status: 'queued' | 'uploading' | 'paused' | 'done' | 'error'
  error: string | null
  videoId: string | null
}

function toItems(uppy: AppUppy): UploadQueueItem[] {
  return uppy.getFiles().map((f) => {
    const status: UploadQueueItem['status'] = f.error
      ? 'error'
      : f.progress.uploadComplete
        ? 'done'
        : f.isPaused
          ? 'paused'
          : f.progress.uploadStarted
            ? 'uploading'
            : 'queued'
    return {
      id: f.id,
      name: f.name ?? 'video',
      sizeBytes: f.size ?? 0,
      progressPct: f.progress.percentage ?? 0,
      status,
      error: typeof f.error === 'string' ? f.error : null,
      videoId: f.response?.body?.video_id ?? null,
    }
  })
}

export function useUploader(onUploaded?: (videoId: string) => void) {
  const queryClient = useQueryClient()
  const [uppy, setUppy] = useState<AppUppy | null>(null)
  const [items, setItems] = useState<UploadQueueItem[]>([])
  const [sessionExpired, setSessionExpired] = useState(false)
  // Ref so the stable event handlers always see the latest callback.
  const onUploadedRef = useRef(onUploaded)
  useEffect(() => {
    onUploadedRef.current = onUploaded
  }, [onUploaded])

  useEffect(() => {
    let disposed = false
    const offs: (() => void)[] = []

    void getUploader().then((instance) => {
      if (disposed) return
      setUppy(instance)
      setItems(toItems(instance))

      const refresh = () => setItems(toItems(instance))
      const events = [
        'file-added',
        'file-removed',
        'upload',
        'upload-progress',
        'upload-error',
        'pause-all',
        'resume-all',
        'cancel-all',
      ] as const
      for (const evt of events) {
        instance.on(evt as 'upload', refresh)
        offs.push(() => instance.off(evt as 'upload', refresh))
      }

      const onSuccess: Parameters<typeof instance.on<'upload-success'>>[1] = (_file, response) => {
        refresh()
        const videoId = response?.body?.video_id
        if (videoId) {
          void queryClient.invalidateQueries({ queryKey: ['videos'] })
          onUploadedRef.current?.(videoId)
        }
      }
      instance.on('upload-success', onSuccess)
      offs.push(() => instance.off('upload-success', onSuccess))
    })

    const offExpired = onSessionExpired(() => setSessionExpired(true))
    return () => {
      disposed = true
      offs.forEach((off) => off())
      offExpired()
    }
  }, [queryClient])

  const addFiles = useCallback(
    (files: File[]): string | null => {
      if (!uppy) return 'Uploader is still starting — try again in a second.'
      let firstError: string | null = null
      for (const file of files) {
        try {
          uppy.addFile({ name: file.name, type: file.type, data: file })
        } catch (err) {
          // Restriction errors (type/size) — surface the first, keep adding the rest.
          firstError ??= err instanceof Error ? err.message : 'File was rejected.'
        }
      }
      return firstError
    },
    [uppy],
  )

  const start = useCallback(
    (youtubeVideoId?: string) => {
      if (!uppy) return
      // The association only applies to a single-file upload — with a batch
      // there is no way to know which file it belongs to.
      const pending = uppy.getFiles().filter((f) => !f.progress.uploadComplete)
      if (youtubeVideoId && pending.length === 1) {
        uppy.setFileMeta(pending[0].id, { youtube_video_id: youtubeVideoId })
      }
      setSessionExpired(false)
      void uppy.upload()
    },
    [uppy],
  )

  const retryFile = useCallback(
    (id: string) => {
      setSessionExpired(false)
      void uppy?.retryUpload(id)
    },
    [uppy],
  )

  const removeFile = useCallback((id: string) => uppy?.removeFile(id), [uppy])

  const clearFinished = useCallback(() => {
    if (!uppy) return
    for (const f of uppy.getFiles()) {
      if (f.progress.uploadComplete) uppy.removeFile(f.id)
    }
  }, [uppy])

  return {
    ready: uppy !== null,
    items,
    sessionExpired,
    addFiles,
    start,
    retryFile,
    removeFile,
    clearFinished,
  }
}
