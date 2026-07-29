/**
 * ApiError structured-detail contract (Wave-1 review-ui lane).
 *
 * FastAPI sends `detail` as a plain string OR the structured {code, message}
 * shape (409 source_expired / pending_clean_or_edit). Both must yield a
 * readable `.message`, and the structured form must expose `.code` so the UI
 * can branch. Regression: an object detail used to render "[object Object]".
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError } from './api'

afterEach(() => vi.unstubAllGlobals())

describe('ApiError', () => {
  it('keeps a string detail as the message (backward compatible)', () => {
    const e = new ApiError(409, 'Render already running')
    expect(e.message).toBe('Render already running')
    expect(e.code).toBeNull()
    expect(e.status).toBe(409)
  })

  it('exposes code and message from a structured {code, message} detail', () => {
    const detail = { code: 'source_expired', message: 'Source media expired — re-upload.' }
    const e = new ApiError(409, detail)
    expect(e.message).toBe('Source media expired — re-upload.')
    expect(e.code).toBe('source_expired')
    expect(e.detail).toBe(detail)
  })

  it('falls back to a status message when the detail is absent or unusable', () => {
    expect(new ApiError(500, undefined).message).toBe('Request failed (500)')
    expect(new ApiError(422, { code: 'trim_noop' }).message).toBe('Request failed (422)')
    expect(new ApiError(422, { code: 'trim_noop' }).code).toBe('trim_noop')
  })
})

describe('api() error parsing', () => {
  it('throws an ApiError carrying the raw structured detail from the body', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        status: 409,
        ok: false,
        json: async () => ({ detail: { code: 'pending_clean_or_edit', message: 'Confirm first.' } }),
      })),
    )
    const err = await api('/clips/c1/trim-render', { method: 'POST' }).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    const apiErr = err as ApiError
    expect(apiErr.code).toBe('pending_clean_or_edit')
    expect(apiErr.message).toBe('Confirm first.')
  })
})
