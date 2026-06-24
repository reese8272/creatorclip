// Chip mascot pose registry — Issue 304 (design handoff).
// Kept in its own module so the Chip component file exports only a component
// (react-refresh/only-export-components), matching the repo convention.
//
// Concept -> sprite map (handoff README §8):
//   magnify -> analyzing a video      idea    -> insight / generating clips
//   present -> explaining a brief      book    -> Creator DNA / learning
//   think   -> "why this clip" / chat  papers  -> editor transcript
//   laptop  -> processing / editor     meditate-> still-learning band
//   wave    -> Assistant welcome       confused-> empty / no-clips states
export const CHIP_POSES = {
  think: 'analyzing/thinking',
  book: 'learning/DNA',
  idea: 'insight',
  present: 'explaining',
  magnify: 'analyzing a video',
  laptop: 'processing/editor',
  papers: 'transcript',
  confused: 'empty/no-clips',
  wave: 'welcome',
  meditate: 'still-learning',
} as const

export type ChipPose = keyof typeof CHIP_POSES
