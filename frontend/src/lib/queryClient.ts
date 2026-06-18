import { QueryClient } from '@tanstack/react-query'

// App-wide TanStack Query client (adopted 2026-06-18 — see docs/DECISIONS.md).
// One client means every component that reads the same queryKey shares a single
// cached request instead of each re-fetching on mount — the concrete win over
// the hand-rolled useEffect+fetch the SPA started with. Defaults are tuned for
// a cookie-authed creator tool: short staleness so navigation feels live, a
// single retry, and no refetch-on-focus (creators tab away constantly).
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})
