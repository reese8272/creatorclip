// Shared TanStack mutation keys — lives outside component files so fast
// refresh keeps working (react-refresh/only-export-components).

// Issue 431: Review suppresses its "all reviewed → dashboard" auto-navigate
// via useIsMutating on this key while a generate-more request is in flight.
export function generateMoreMutationKey(videoId: string) {
  return ['generate-more', videoId]
}
