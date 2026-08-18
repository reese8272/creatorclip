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

// Issue 522 — a Redis outage makes LLM routes return 503 with can't-verify copy
// instead of the 429 "your budget has been reached". No component change was
// needed for that: `deriveMessage` already surfaces a server-authored string
// detail verbatim. These pin that contract, because the honesty of the new copy
// depends entirely on it — server-side wording is worthless if the client
// substitutes its own generic message.
describe('spend-guard outage copy (Issue 522)', () => {
  it('surfaces the 503 can’t-verify detail verbatim rather than a generic message', async () => {
    const serverCopy =
      "We can't check your AI budget right now, so AI features are paused for a few " +
      'minutes. Nothing was charged. Please try again shortly.'
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        status: 503,
        ok: false,
        json: async () => ({ detail: serverCopy }),
      })),
    )
    const err = (await api('/clips/generate', { method: 'POST' }).catch(
      (e: unknown) => e,
    )) as ApiError

    expect(err).toBeInstanceOf(ApiError)
    expect(err.status).toBe(503)
    expect(err.message).toBe(serverCopy)
    // The honesty assertion, mirroring tests/test_spend_guard.py: whatever the
    // creator is shown must not claim a budget was reached that was never read.
    expect(err.message.toLowerCase()).not.toContain('has been reached')
  })

  it('still surfaces the real 429 cap copy, so the two stay distinguishable', async () => {
    const capCopy = "Your account's daily AI budget has been reached. It resets automatically."
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ status: 429, ok: false, json: async () => ({ detail: capCopy }) })),
    )
    const err = (await api('/clips/generate', { method: 'POST' }).catch(
      (e: unknown) => e,
    )) as ApiError

    expect(err.status).toBe(429)
    expect(err.message).toBe(capCopy)
  })
})
