import { Navigate, Outlet } from 'react-router-dom'
import { useAuth } from '@/hooks/useAuth'
import { ChipLoadingScreen } from '@/components/chip/ChipStates'

// Auth gate for the protected routes (Issue 85b). While the session probe is in
// flight we show a loader; if it resolves to no user we redirect to the SPA
// login route; otherwise the protected page renders via <Outlet/>. Chrome
// (Nav/Footer) is layered separately by <AppChrome> so focused full-screen flows
// (e.g. the walkthrough) can be gated WITHOUT the nav.
export function AuthGate() {
  const { user, loading } = useAuth()

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <ChipLoadingScreen label="Loading…" />
      </div>
    )
  }
  if (!user) {
    return <Navigate to="/login" replace />
  }
  return <Outlet />
}
