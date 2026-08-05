// Direct-to-R2 upload singleton (Issue 395).
//
// One module-scope Uppy instance survives SPA route changes, so an in-flight
// multi-GB upload is not killed by navigation — components subscribe via
// useUploader and never destroy it. The transport is decided once per session
// from GET /videos/uploads/config:
//
//   multipart — @uppy/aws-s3: the browser PUTs 25 MiB parts straight to R2 with
//     presigned URLs; our API only signs and completes. Progress is R2-acked
//     bytes (honest), failed parts retry individually, and listParts resumes
//     after a reload without re-sending uploaded parts.
//   proxy — @uppy/xhr-upload to the legacy /videos/upload endpoint (local-disk
//     dev, where R2 does not exist).
//
// Presigned part PUTs carry no cookies — only the sign/complete calls are
// session-authed, which is why they use api() with redirectOn401: false: a
// 60-minute JWT dies inside any long upload (observed in prod 2026-08-05), and
// the redirecting wrapper would navigate away mid-upload and lose the queue.
// Instead we pause everything and let the UI offer sign-in + retry — uploaded
// parts are kept by R2 for 7 days.

import Uppy from '@uppy/core'
import type { Body, Meta, UppyFile } from '@uppy/core'
import AwsS3 from '@uppy/aws-s3'
import XHRUpload from '@uppy/xhr-upload'
import GoldenRetriever from '@uppy/golden-retriever'
import { api, ApiError } from './api'

export interface UploadConfig {
  mode: 'multipart' | 'proxy'
  part_size_bytes: number
  max_file_bytes: number
}

/** Per-file meta the form sets before upload; snake_case so the proxy mode's
 * FormData field name matches the legacy endpoint's Form(...) parameter. */
export type UploadMeta = Meta & {
  youtube_video_id?: string
  duration_s?: number
}

/** Both transports resolve to the same response shape (VideoLinkedOut). */
export type UploadBody = Body & {
  video_id: string
  status: string
  stream_url: string | null
}

export type AppUppy = Uppy<UploadMeta, UploadBody>

// ── session-expiry pub/sub ────────────────────────────────────────────────────

const sessionExpiredListeners = new Set<() => void>()

export function onSessionExpired(cb: () => void): () => void {
  sessionExpiredListeners.add(cb)
  return () => sessionExpiredListeners.delete(cb)
}

let activeUppy: AppUppy | null = null

/** api() for the upload endpoints: never redirects on 401 — pauses the queue
 * and notifies instead, so the in-flight upload state survives re-login. */
async function mpApi<T>(path: string, opts?: { method?: string; body?: unknown }): Promise<T> {
  try {
    return await api<T>(path, { ...opts, redirectOn401: false })
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      activeUppy?.pauseAll()
      sessionExpiredListeners.forEach((cb) => cb())
    }
    throw err
  }
}

// ── multipart transport callbacks ─────────────────────────────────────────────

interface UploadCreateOut {
  upload_id: string
  key: string
  part_size_bytes: number
}

interface UploadPartsOut {
  parts: { part_number: number; size: number; etag: string }[]
}

// Uppy's use() checks plugin options against the DEFAULT <Meta, Body> generics
// (the instance generics don't flow through typeof BasePlugin), so the callback
// signatures use the generic file type and cast meta at the read site.
type AppFile = UppyFile<Meta, Body>

/** The plugin types uploadId as optional on list/complete/abort (single-PUT
 * uploads have none) — we are always multipart, so absence is a bug. */
function requireUploadId(uploadId: string | undefined): string {
  if (!uploadId) throw new Error('multipart upload has no uploadId')
  return uploadId
}

/** Exported for tests — thin adapters between Uppy's S3 callback contract and
 * the /videos/uploads API. */
export function multipartCallbacks() {
  return {
    createMultipartUpload: async (file: AppFile) => {
      const meta = file.meta as UploadMeta
      const out = await mpApi<UploadCreateOut>('/videos/uploads', {
        method: 'POST',
        body: {
          filename: file.name ?? 'video.mp4',
          size_bytes: file.size ?? 0,
          content_type: file.type || null,
          youtube_video_id: meta.youtube_video_id || null,
          duration_s: meta.duration_s || null,
        },
      })
      return { key: out.key, uploadId: out.upload_id }
    },

    signPart: async (
      _file: AppFile,
      { uploadId, key, partNumber }: { uploadId: string; key: string; partNumber: number },
    ) => {
      const out = await mpApi<{ url: string; expires_in: number }>(
        `/videos/uploads/${encodeURIComponent(uploadId)}/parts/${partNumber}/presign`,
        { method: 'POST', body: { key } },
      )
      // url only — no headers: nothing beyond the part body is signed.
      return { url: out.url, method: 'PUT' as const }
    },

    listParts: async (_file: AppFile, { key, uploadId }: { key: string; uploadId?: string }) => {
      const id = requireUploadId(uploadId)
      const out = await mpApi<UploadPartsOut>(
        `/videos/uploads/${encodeURIComponent(id)}/parts?key=${encodeURIComponent(key)}`,
      )
      return out.parts.map((p) => ({ PartNumber: p.part_number, Size: p.size, ETag: p.etag }))
    },

    completeMultipartUpload: async (
      file: AppFile,
      {
        key,
        uploadId,
        parts,
      }: { key: string; uploadId?: string; parts: { PartNumber?: number; ETag?: string }[] },
    ) => {
      const id = requireUploadId(uploadId)
      const meta = file.meta as UploadMeta
      const out = await mpApi<UploadBody>(`/videos/uploads/${encodeURIComponent(id)}/complete`, {
        method: 'POST',
        body: {
          key,
          parts: parts.map((p) => ({ part_number: p.PartNumber, etag: p.ETag })),
          youtube_video_id: meta.youtube_video_id || null,
          duration_s: meta.duration_s || null,
          // Issue 435: the flow is stateless server-side, so the title seed
          // (filename stem) travels with the completing call.
          filename: file.name ?? null,
        },
      })
      // The returned object becomes upload-success's response.body; the plugin
      // types it as {location?} but our instance reads it as UploadBody.
      return out as UploadBody & { location?: string }
    },

    abortMultipartUpload: async (
      _file: AppFile,
      { key, uploadId }: { key: string; uploadId?: string },
    ) => {
      await mpApi(`/videos/uploads/${encodeURIComponent(requireUploadId(uploadId))}/abort`, {
        method: 'POST',
        body: { key },
      })
    },
  }
}

// ── duration probe (advisory) ─────────────────────────────────────────────────

/** Best-effort duration via HTMLVideoElement metadata — feeds the server's
 * advisory balance pre-check; the worker's ffprobe at ingest is authoritative. */
export function readVideoDuration(blob: Blob): Promise<number | null> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(blob)
    const el = document.createElement('video')
    el.preload = 'metadata'
    el.onloadedmetadata = () => {
      const d = el.duration
      URL.revokeObjectURL(url)
      resolve(Number.isFinite(d) && d > 0 ? d : null)
    }
    el.onerror = () => {
      URL.revokeObjectURL(url)
      resolve(null)
    }
    el.src = url
  })
}

// ── singleton assembly ────────────────────────────────────────────────────────

const ACCEPTED_TYPES = ['.mp4', '.mov', '.mkv', '.webm', '.m4v']

/** Exported for tests: assemble a fresh Uppy for a given config. */
export function buildUppy(config: UploadConfig): AppUppy {
  const uppy = new Uppy<UploadMeta, UploadBody>({
    autoProceed: false,
    restrictions: {
      allowedFileTypes: ACCEPTED_TYPES,
      maxFileSize: config.max_file_bytes,
    },
  })

  uppy.on('file-added', (file) => {
    void readVideoDuration(file.data as Blob)
      .then((d) => {
        if (d) uppy.setFileMeta(file.id, { duration_s: d })
      })
      .catch(() => {}) // advisory only — never block the upload on a probe failure
  })

  if (config.mode === 'multipart') {
    uppy.use(AwsS3, {
      shouldUseMultipart: true,
      getChunkSize: () => config.part_size_bytes,
      ...multipartCallbacks(),
    })
  } else {
    uppy.use(XHRUpload, {
      endpoint: '/videos/upload',
      fieldName: 'file',
      withCredentials: true,
      // Only the association rides along as a form field; duration_s is a
      // multipart-only advisory (the legacy endpoint ffprobes server-side).
      allowedMetaFields: ['youtube_video_id'],
    })
  }

  // Cross-reload resume: localStorage state + (when available) the service
  // worker blob cache. Without the SW a reloaded file returns as a "ghost" —
  // re-selecting the same file resumes the same uploadId via listParts.
  // Guarded: golden-retriever hard-requires localStorage + indexedDB, which
  // some private-browsing modes (and the test env) don't provide — uploads must
  // still work there, just without cross-reload resume.
  if (typeof localStorage !== 'undefined' && typeof indexedDB !== 'undefined') {
    uppy.use(GoldenRetriever, { serviceWorker: 'serviceWorker' in navigator })
    if ('serviceWorker' in navigator) {
      void navigator.serviceWorker
        .register('/app/uppy-sw.js', { scope: '/app/' })
        .catch(() => {}) // resume degrades to ghost-mode; never block uploads
    }
  }

  return uppy
}

let uploaderPromise: Promise<AppUppy> | null = null

/** The app-wide uploader. First call fetches the transport config; later calls
 * reuse the same instance (and its in-flight uploads). */
export function getUploader(): Promise<AppUppy> {
  uploaderPromise ??= api<UploadConfig>('/videos/uploads/config').then((config) => {
    const uppy = buildUppy(config)
    activeUppy = uppy
    return uppy
  })
  return uploaderPromise
}
