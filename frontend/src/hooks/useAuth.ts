import { useQuery } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api'
import type { Balance, CurrentUser } from '@/types'

interface AuthState {
  user: CurrentUser | null
  balance: Balance | null
  loading: boolean
}

// Auth + balance bootstrap, backed by TanStack Query (one cached /auth/me +
// /billing/balance shared by the chrome and every page). The probe does NOT
// redirect on 401 — it resolves to `user: null` so public pages (pricing) can
// render for logged-out visitors. The redirect decision lives in <AuthGate>,
// which protects the authenticated routes (Issue 85b).
export function useAuth(): AuthState {
  const userQuery = useQuery({
    queryKey: ['auth', 'me'],
    queryFn: async () => {
      try {
        return await api<CurrentUser>('/auth/me', { redirectOn401: false })
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null
        throw e
      }
    },
  })
  const balanceQuery = useQuery({
    queryKey: ['billing', 'balance'],
    queryFn: () => api<Balance>('/billing/balance', { redirectOn401: false }),
    enabled: userQuery.data != null,
  })

  return {
    user: userQuery.data ?? null,
    balance: balanceQuery.data ?? null,
    loading: userQuery.isPending,
  }
}
