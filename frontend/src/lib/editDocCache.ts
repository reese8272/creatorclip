import { normalizeCut, toDocumentCuts } from './editorCuts'
import type { EditDocument, EditDocumentCut } from '@/types'

// localStorage's demotion (Issue 391).
//
// Before this issue localStorage WAS the source of truth for a creator's cuts.
// Now it has two much smaller jobs:
//
//   1. A one-time migration of the legacy `clip:{id}:cuts` key into the server
//      document, so nobody's in-progress work is lost by the upgrade.
//   2. A paint-fast cache, shown immediately on open and then ALWAYS overwritten
//      by the server once the GET resolves.
//
// Neither job is allowed to make it authoritative again.

/** The pre-391 key. Read once per clip, then removed. */
const legacyKey = (clipId: string) => `clip:${clipId}:cuts`

/** The post-391 offline cache. */
const cacheKey = (clipId: string) => `clip:${clipId}:editdoc`

export interface CachedDocument {
  doc: EditDocument
  /** True when this copy has edits that were never confirmed saved. */
  dirty: boolean
  revision: number
}

/** localStorage.getItem, but a disabled/blocked store reads as "nothing there". */
function readRaw(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

/**
 * Read the legacy cut list, or null when there is nothing to migrate.
 *
 * Invalid entries are DROPPED rather than failing the whole import: a single
 * corrupt cut must not cost the creator the other nine. `normalizeCut` already
 * backfills a missing id — every pre-390 entry lacks one.
 */
export function readLegacyCuts(clipId: string): EditDocumentCut[] | null {
  const raw = readRaw(legacyKey(clipId))
  if (!raw) return null
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return null
    const cuts = toDocumentCuts(
      parsed
        .map(normalizeCut)
        .filter((c): c is NonNullable<ReturnType<typeof normalizeCut>> => c !== null),
    )
    return cuts.length ? cuts : null
  } catch {
    return null
  }
}

/**
 * Remove the legacy key.
 *
 * MUST be called only after the server has accepted the import — on a 200 OR a
 * 409, because a 409 means the server already holds a newer document and won
 * either way. Removing it before the round trip would lose the cuts if the
 * request failed; removing it on neither would re-import them forever. Doing it
 * exactly on "the server has spoken" makes a double import structurally
 * impossible and is self-healing if the removal itself throws.
 */
export function clearLegacyCuts(clipId: string): void {
  try {
    localStorage.removeItem(legacyKey(clipId))
  } catch {
    // Nothing to recover: the import already succeeded server-side, and the
    // revision-0 guard means a surviving key cannot be re-imported.
  }
}

export function readCache(clipId: string): CachedDocument | null {
  const raw = readRaw(cacheKey(clipId))
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as Partial<CachedDocument>
    const doc = parsed.doc
    if (!doc || !Array.isArray(doc.cuts)) return null
    return {
      doc,
      dirty: parsed.dirty === true,
      revision: typeof parsed.revision === 'number' ? parsed.revision : 0,
    }
  } catch {
    return null
  }
}

/**
 * Write the cache. Returns false when the write failed (quota, private mode).
 *
 * The caller SURFACES that false rather than dropping it. The pre-391 code did
 * `catch { /* quota — recoverable *\/ }` around the only copy of the creator's
 * work, which silently discarded a save; here the server already has the
 * document, so a failed cache write is genuinely low-severity — but it is still
 * reported instead of swallowed.
 */
export function writeCache(
  clipId: string,
  doc: EditDocument,
  { dirty, revision }: { dirty: boolean; revision: number },
): boolean {
  try {
    localStorage.setItem(cacheKey(clipId), JSON.stringify({ doc, dirty, revision }))
    return true
  } catch {
    return false
  }
}

export function clearCache(clipId: string): void {
  try {
    localStorage.removeItem(cacheKey(clipId))
  } catch {
    // A stale cache is always overwritten by the server on next load.
  }
}
