import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  clearCache,
  clearLegacyCuts,
  readCache,
  readLegacyCuts,
  writeCache,
} from './editDocCache'
import type { EditDocument } from '@/types'

const CLIP = 'clip-1'
const legacyKey = `clip:${CLIP}:cuts`
const cacheKey = `clip:${CLIP}:editdoc`

const doc: EditDocument = {
  version: 1,
  cuts: [{ id: 'a', start_s: 1, end_s: 2 }],
  last_applied_at: null,
}

afterEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
})

describe('legacy import', () => {
  it('returns null when there is nothing to migrate', () => {
    expect(readLegacyCuts(CLIP)).toBeNull()
  })

  it('drops the derived word span — indices are recomputed, never persisted', () => {
    localStorage.setItem(
      legacyKey,
      JSON.stringify([{ id: 'a', start_s: 1, end_s: 2, indices: [3, 4] }]),
    )
    expect(readLegacyCuts(CLIP)).toEqual([{ id: 'a', start_s: 1, end_s: 2 }])
  })

  it('backfills an id on pre-390 entries that have none', () => {
    localStorage.setItem(legacyKey, JSON.stringify([{ start_s: 1, end_s: 2 }]))
    const cuts = readLegacyCuts(CLIP)
    expect(cuts).toHaveLength(1)
    expect(typeof cuts?.[0].id).toBe('string')
    expect(cuts?.[0].id).toBeTruthy()
  })

  it('drops only the invalid entries, not the whole import', () => {
    // One corrupt cut must not cost the creator the other two.
    localStorage.setItem(
      legacyKey,
      JSON.stringify([
        { id: 'a', start_s: 1, end_s: 2 },
        { id: 'bad', start_s: 5, end_s: 3 },
        { id: 'c', start_s: 8, end_s: 9 },
      ]),
    )
    expect(readLegacyCuts(CLIP)?.map((c) => c.id)).toEqual(['a', 'c'])
  })

  it('returns null for malformed JSON rather than throwing', () => {
    localStorage.setItem(legacyKey, '{not json')
    expect(readLegacyCuts(CLIP)).toBeNull()
  })

  it('returns null when every entry is invalid', () => {
    localStorage.setItem(legacyKey, JSON.stringify([{ start_s: 'x', end_s: 'y' }]))
    expect(readLegacyCuts(CLIP)).toBeNull()
  })

  it('clearLegacyCuts removes the key', () => {
    localStorage.setItem(legacyKey, JSON.stringify([{ id: 'a', start_s: 1, end_s: 2 }]))
    clearLegacyCuts(CLIP)
    expect(localStorage.getItem(legacyKey)).toBeNull()
  })
})

describe('offline cache', () => {
  it('round-trips a document with its dirty flag and revision', () => {
    expect(writeCache(CLIP, doc, { dirty: true, revision: 4 })).toBe(true)
    expect(readCache(CLIP)).toEqual({ doc, dirty: true, revision: 4 })
  })

  it('reports a failed write instead of swallowing it', () => {
    // The pre-391 code wrapped the ONLY copy of the creator's work in
    // `catch { /* quota — recoverable */ }`, silently discarding a save.
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('QuotaExceededError')
    })
    expect(writeCache(CLIP, doc, { dirty: true, revision: 1 })).toBe(false)
  })

  it('treats a malformed cache as absent', () => {
    localStorage.setItem(cacheKey, '{"doc":{"version":1}}')
    expect(readCache(CLIP)).toBeNull()
  })

  it('survives a localStorage that throws on read', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('SecurityError')
    })
    expect(readCache(CLIP)).toBeNull()
    expect(readLegacyCuts(CLIP)).toBeNull()
  })

  it('clearCache removes the key', () => {
    writeCache(CLIP, doc, { dirty: false, revision: 1 })
    clearCache(CLIP)
    expect(readCache(CLIP)).toBeNull()
  })

  it('keeps the legacy and cache keys separate', () => {
    localStorage.setItem(legacyKey, JSON.stringify([{ id: 'a', start_s: 1, end_s: 2 }]))
    writeCache(CLIP, doc, { dirty: false, revision: 2 })
    clearLegacyCuts(CLIP)
    expect(readCache(CLIP)).not.toBeNull()
  })
})
