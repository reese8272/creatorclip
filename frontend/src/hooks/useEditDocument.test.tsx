import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useEditDocument } from './useEditDocument'
import { SAVE_DEBOUNCE_MS } from '@/lib/saveScheduler'
import type { EditDocument } from '@/types'

const CLIP = 'clip-1'
const legacyKey = `clip:${CLIP}:cuts`

const emptyDoc: EditDocument = { version: 1, cuts: [], last_applied_at: null }

const addCut = (id: string) => (d: EditDocument) => ({
  ...d,
  cuts: [...d.cuts, { id, start_s: 1, end_s: 2 }],
})

/** Mock `fetch` with a queue of responses keyed by method. */
function mockApi(handlers: {
  get?: () => { status?: number; body: unknown }
  put?: (call: number, body: unknown) => { status?: number; body: unknown }
}) {
  let putCalls = 0
  const putBodies: unknown[] = []
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    const result =
      method === 'PUT'
        ? (putBodies.push(JSON.parse(String(init?.body))),
          handlers.put?.(putCalls++, putBodies[putBodies.length - 1]))
        : handlers.get?.()
    const { status = 200, body } = result ?? { body: {} }
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    } as Response
  })
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, putBodies, putCount: () => putCalls }
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

const okGet = (revision: number, doc: EditDocument = emptyDoc) => ({
  body: { clip_id: CLIP, revision, doc, updated_at: null, clip_duration_s: 60 },
})

const okPut = (revision: number) => ({
  body: { clip_id: CLIP, revision, doc: emptyDoc, updated_at: '2026-08-04T00:00:00Z', clip_duration_s: 60 },
})

beforeEach(() => {
  localStorage.clear()
  vi.useFakeTimers({ shouldAdvanceTime: true })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

async function renderReady(api: Parameters<typeof mockApi>[0]) {
  const mocks = mockApi(api)
  const view = renderHook(() => useEditDocument(CLIP), { wrapper })
  await waitFor(() => expect(view.result.current.isLoading).toBe(false))
  return { ...mocks, ...view }
}

describe('useEditDocument', () => {
  it('hydrates from the server document', async () => {
    const doc: EditDocument = {
      version: 1,
      cuts: [{ id: 'server', start_s: 5, end_s: 6 }],
      last_applied_at: null,
    }
    const { result } = await renderReady({ get: () => okGet(3, doc) })
    expect(result.current.doc.cuts.map((c) => c.id)).toEqual(['server'])
    expect(result.current.saveState).toBe('idle')
  })

  it('autosaves after the debounce, sending the seen revision as base', async () => {
    const { result, putBodies } = await renderReady({
      get: () => okGet(3),
      put: () => okPut(4),
    })

    act(() => result.current.commit(addCut('a')))
    expect(result.current.saveState).toBe('dirty')

    await act(async () => {
      vi.advanceTimersByTime(SAVE_DEBOUNCE_MS)
    })
    await waitFor(() => expect(result.current.saveState).toBe('saved'))
    expect(putBodies).toHaveLength(1)
    expect((putBodies[0] as { base_revision: number }).base_revision).toBe(3)
  })

  it('advances base_revision so the second save is not a self-conflict', async () => {
    const { result, putBodies } = await renderReady({
      get: () => okGet(3),
      put: (call) => okPut(4 + call),
    })

    act(() => result.current.commit(addCut('a')))
    await act(async () => void vi.advanceTimersByTime(SAVE_DEBOUNCE_MS))
    await waitFor(() => expect(result.current.saveState).toBe('saved'))

    act(() => result.current.commit(addCut('b')))
    await act(async () => void vi.advanceTimersByTime(SAVE_DEBOUNCE_MS))
    await waitFor(() => expect(putBodies).toHaveLength(2))

    expect((putBodies[1] as { base_revision: number }).base_revision).toBe(4)
  })

  it('undo and redo move through the stack more than one step', async () => {
    const { result } = await renderReady({ get: () => okGet(1), put: () => okPut(2) })

    act(() => result.current.commit(addCut('a')))
    act(() => result.current.commit(addCut('b')))
    expect(result.current.doc.cuts).toHaveLength(2)

    act(() => result.current.undo())
    act(() => result.current.undo())
    expect(result.current.doc.cuts).toHaveLength(0)
    expect(result.current.canUndo).toBe(false)

    act(() => result.current.redo())
    expect(result.current.doc.cuts).toHaveLength(1)
  })

  it('surfaces a 409 as an explicit choice and never auto-merges', async () => {
    const serverDoc: EditDocument = {
      version: 1,
      cuts: [{ id: 'theirs', start_s: 9, end_s: 10 }],
      last_applied_at: null,
    }
    const { result } = await renderReady({
      get: () => okGet(3),
      put: () => ({
        status: 409,
        body: { detail: { code: 'stale_revision', revision: 8, doc: serverDoc } },
      }),
    })

    act(() => result.current.commit(addCut('mine')))
    await act(async () => void vi.advanceTimersByTime(SAVE_DEBOUNCE_MS))
    await waitFor(() => expect(result.current.saveState).toBe('conflict'))

    // The local document is untouched — nothing was merged into it.
    expect(result.current.doc.cuts.map((c) => c.id)).toEqual(['mine'])
    expect(result.current.conflict?.serverRevision).toBe(8)
    expect(result.current.conflict?.serverDoc.cuts.map((c) => c.id)).toEqual(['theirs'])
  })

  it('takeTheirs replaces the document with the server copy', async () => {
    const serverDoc: EditDocument = {
      version: 1,
      cuts: [{ id: 'theirs', start_s: 9, end_s: 10 }],
      last_applied_at: null,
    }
    const { result } = await renderReady({
      get: () => okGet(3),
      put: () => ({
        status: 409,
        body: { detail: { code: 'stale_revision', revision: 8, doc: serverDoc } },
      }),
    })
    act(() => result.current.commit(addCut('mine')))
    await act(async () => void vi.advanceTimersByTime(SAVE_DEBOUNCE_MS))
    await waitFor(() => expect(result.current.saveState).toBe('conflict'))

    act(() => result.current.takeTheirs())
    expect(result.current.doc.cuts.map((c) => c.id)).toEqual(['theirs'])
    expect(result.current.conflict).toBeNull()
  })

  it('keepMine re-saves against the server revision', async () => {
    const { result, putBodies } = await renderReady({
      get: () => okGet(3),
      put: (call) =>
        call === 0
          ? { status: 409, body: { detail: { revision: 8, doc: emptyDoc } } }
          : okPut(9),
    })
    act(() => result.current.commit(addCut('mine')))
    await act(async () => void vi.advanceTimersByTime(SAVE_DEBOUNCE_MS))
    await waitFor(() => expect(result.current.saveState).toBe('conflict'))

    act(() => result.current.keepMine())
    await act(async () => void vi.advanceTimersByTime(SAVE_DEBOUNCE_MS))
    await waitFor(() => expect(result.current.saveState).toBe('saved'))

    expect((putBodies[1] as { base_revision: number }).base_revision).toBe(8)
    expect(result.current.doc.cuts.map((c) => c.id)).toEqual(['mine'])
  })

  it('treats a 422 as terminal rather than retrying forever', async () => {
    const { result, putCount } = await renderReady({
      get: () => okGet(1),
      put: () => ({ status: 422, body: { detail: { code: 'overlap', message: 'overlap' } } }),
    })

    act(() => result.current.commit(addCut('a')))
    await act(async () => void vi.advanceTimersByTime(SAVE_DEBOUNCE_MS))
    await waitFor(() => expect(result.current.saveState).toBe('error'))

    await act(async () => void vi.advanceTimersByTime(60_000))
    expect(putCount()).toBe(1)
    expect(result.current.saveError).toMatch(/could not be saved/i)
  })

  it('imports legacy localStorage cuts only at revision 0', async () => {
    localStorage.setItem(
      legacyKey,
      JSON.stringify([{ id: 'legacy', start_s: 1, end_s: 2, indices: [0, 1] }]),
    )
    const { result } = await renderReady({ get: () => okGet(0), put: () => okPut(1) })

    expect(result.current.doc.cuts.map((c) => c.id)).toEqual(['legacy'])
    await act(async () => void vi.advanceTimersByTime(SAVE_DEBOUNCE_MS))
    await waitFor(() => expect(result.current.saveState).toBe('saved'))
    // Removed only once the server accepted it — a double import is impossible.
    expect(localStorage.getItem(legacyKey)).toBeNull()
  })

  it('DISCARDS a legacy blob when the server already holds a decision', async () => {
    // The anti-resurrection rule: the creator cleared their cuts on device B, so
    // the server holds `cuts: []` at revision >= 1. Device A's stale localStorage
    // must not bring them back.
    localStorage.setItem(legacyKey, JSON.stringify([{ id: 'zombie', start_s: 1, end_s: 2 }]))
    const { result } = await renderReady({ get: () => okGet(5) })

    expect(result.current.doc.cuts).toEqual([])
    expect(result.current.saveState).toBe('idle')
  })

  it('never issues two concurrent PUTs', async () => {
    let resolveFirst: (() => void) | null = null
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if ((init?.method ?? 'GET') === 'GET') {
        return { ok: true, status: 200, json: async () => okGet(1).body } as Response
      }
      if (!resolveFirst) {
        await new Promise<void>((r) => {
          resolveFirst = r
        })
      }
      return { ok: true, status: 200, json: async () => okPut(2).body } as Response
    })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useEditDocument(CLIP), { wrapper })
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    act(() => result.current.commit(addCut('a')))
    await act(async () => void vi.advanceTimersByTime(SAVE_DEBOUNCE_MS))
    await waitFor(() => expect(result.current.saveState).toBe('saving'))

    // A second edit while the first save is still in flight.
    act(() => result.current.commit(addCut('b')))
    await act(async () => void vi.advanceTimersByTime(SAVE_DEBOUNCE_MS))

    const putsWhileInFlight = fetchMock.mock.calls.filter(
      ([, init]) => (init as RequestInit | undefined)?.method === 'PUT',
    ).length
    expect(putsWhileInFlight).toBe(1)
  })
})
