import type { ReviewClip } from '@/types'

// Pure pile logic (Issue 445), kept out of the component file for the same
// reason `lib/cropTrack.ts` is: a module that exports both components and
// helpers breaks Fast Refresh, and this half is worth testing on its own.

export type Pile = 'pending' | 'kept' | 'dropped'

export const PILE_ORDER: Pile[] = ['pending', 'kept', 'dropped']

export const PILE_LABEL: Record<Pile, string> = {
  pending: 'Needs review',
  kept: 'Keep',
  dropped: 'Drop',
}

/** Partition a clip list into the three piles, preserving input order. */
export function partitionByPile(clips: ReviewClip[]): Record<Pile, ReviewClip[]> {
  const piles: Record<Pile, ReviewClip[]> = { pending: [], kept: [], dropped: [] }
  for (const c of clips) piles[c.triage ?? 'pending'].push(c)
  return piles
}

/** Engine top-picks first, then by rank. Issue 377's shortlist as ORDERING.
 *
 * 377 used `shortlisted` as a FILTER, which hid nine of twelve clips behind a
 * "show all candidates" link and let the queue claim it was finished after
 * three verdicts. Ordering keeps its intent — argue the best cases first —
 * without ever asserting something false about what is left.
 */
export function orderForReview(clips: ReviewClip[]): ReviewClip[] {
  return [...clips].sort((a, b) => {
    if (a.shortlisted !== b.shortlisted) return a.shortlisted ? -1 : 1
    return (a.rank ?? Number.MAX_SAFE_INTEGER) - (b.rank ?? Number.MAX_SAFE_INTEGER)
  })
}
