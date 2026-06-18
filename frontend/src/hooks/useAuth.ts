import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import type { Balance, CurrentUser } from '@/types'

interface AuthState {
  user: CurrentUser | null
  balance: Balance | null
  loading: boolean
}

// Mirrors the vanilla auth.js bootstrap: probe /auth/me (a 401 redirects to the
// login page inside api()), then fetch the balance chip. The first-run
// walkthrough gate is intentionally not re-implemented here — it lives in the
// shared bootstrap and only fires for brand-new creators who never reach the
// profile page; it moves into the shared SPA layout when more pages are ported.
export function useAuth(): AuthState {
  const [state, setState] = useState<AuthState>({ user: null, balance: null, loading: true })

  useEffect(() => {
    let active = true
    ;(async () => {
      const user = await api<CurrentUser>('/auth/me')
      const balance = await api<Balance>('/billing/balance').catch(() => null)
      if (active) setState({ user, balance, loading: false })
    })()
    return () => {
      active = false
    }
  }, [])

  return state
}
