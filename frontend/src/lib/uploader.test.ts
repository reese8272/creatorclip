// Issue 395 — the transport adapters between Uppy's S3 contract and our API.
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { UploadMeta } from './uploader'
import { buildUppy, multipartCallbacks, onSessionExpired } from './uploader'

type FetchCall = { url: string; init: RequestInit }

function stubFetch(handler: (url: string) => { status: number; body: unknown }) {
  const calls: FetchCall[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = String(input)
      calls.push({ url, init })
      const { status, body } = handler(url)
      return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => body,
      }
    }),
  )
  return calls
}

function fileWithMeta(meta: UploadMeta = {}) {
  return {
    name: 'clip.mp4',
    size: 1000,
    type: 'video/mp4',
    meta,
  } as unknown as Parameters<ReturnType<typeof multipartCallbacks>['createMultipartUpload']>[0]
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('multipartCallbacks', () => {
  it('createMultipartUpload posts file facts and maps upload_id → uploadId', async () => {
    const calls = stubFetch(() => ({
      status: 201,
      body: { upload_id: 'upl-1', key: 'source/c/k.mp4', part_size_bytes: 5 },
    }))
    const out = await multipartCallbacks().createMultipartUpload(
      fileWithMeta({ youtube_video_id: 'abc12345678', duration_s: 42 }),
    )
    expect(out).toEqual({ uploadId: 'upl-1', key: 'source/c/k.mp4' })
    expect(calls[0].url).toBe('/videos/uploads')
    expect(JSON.parse(calls[0].init.body as string)).toEqual({
      filename: 'clip.mp4',
      size_bytes: 1000,
      content_type: 'video/mp4',
      youtube_video_id: 'abc12345678',
      duration_s: 42,
    })
  })

  it('signPart hits the part URL and returns url-only PUT parameters', async () => {
    const calls = stubFetch(() => ({ status: 200, body: { url: 'https://r2/part', expires_in: 900 } }))
    const out = await multipartCallbacks().signPart(fileWithMeta(), {
      uploadId: 'upl-1',
      key: 'source/c/k.mp4',
      partNumber: 7,
    })
    expect(out).toEqual({ url: 'https://r2/part', method: 'PUT' })
    expect(calls[0].url).toBe('/videos/uploads/upl-1/parts/7/presign')
    expect(JSON.parse(calls[0].init.body as string)).toEqual({ key: 'source/c/k.mp4' })
  })

  it('listParts maps snake_case parts to the S3 shape (resume primitive)', async () => {
    stubFetch(() => ({
      status: 200,
      body: { parts: [{ part_number: 2, size: 25, etag: 'e2' }] },
    }))
    const out = await multipartCallbacks().listParts(fileWithMeta(), {
      uploadId: 'upl-1',
      key: 'source/c/k.mp4',
    })
    expect(out).toEqual([{ PartNumber: 2, Size: 25, ETag: 'e2' }])
  })

  it('completeMultipartUpload posts the manifest and surfaces VideoLinkedOut', async () => {
    const calls = stubFetch(() => ({
      status: 200,
      body: { video_id: 'v9', status: 'pending', stream_url: '/tasks/v9/events' },
    }))
    const out = await multipartCallbacks().completeMultipartUpload(
      fileWithMeta({ duration_s: 42 }),
      {
        uploadId: 'upl-1',
        key: 'source/c/k.mp4',
        parts: [{ PartNumber: 1, ETag: 'e1' }],
      },
    )
    expect(out.video_id).toBe('v9')
    expect(calls[0].url).toBe('/videos/uploads/upl-1/complete')
    const body = JSON.parse(calls[0].init.body as string)
    expect(body.parts).toEqual([{ part_number: 1, etag: 'e1' }])
    expect(body.duration_s).toBe(42)
  })

  it('a 401 notifies session-expiry listeners instead of redirecting', async () => {
    stubFetch(() => ({ status: 401, body: { detail: 'expired' } }))
    const expired = vi.fn()
    const off = onSessionExpired(expired)
    await expect(
      multipartCallbacks().signPart(fileWithMeta(), {
        uploadId: 'upl-1',
        key: 'source/c/k.mp4',
        partNumber: 1,
      }),
    ).rejects.toThrow()
    expect(expired).toHaveBeenCalledTimes(1)
    off()
  })
})

describe('buildUppy', () => {
  it('multipart mode installs the AwsS3 plugin', () => {
    const uppy = buildUppy({ mode: 'multipart', part_size_bytes: 25, max_file_bytes: 100 })
    expect(uppy.getPlugin('AwsS3Multipart')).toBeTruthy()
    expect(uppy.getPlugin('XHRUpload')).toBeFalsy()
    uppy.destroy()
  })

  it('proxy mode falls back to the legacy XHR endpoint', () => {
    const uppy = buildUppy({ mode: 'proxy', part_size_bytes: 0, max_file_bytes: 100 })
    expect(uppy.getPlugin('XHRUpload')).toBeTruthy()
    expect(uppy.getPlugin('AwsS3Multipart')).toBeFalsy()
    uppy.destroy()
  })
})
