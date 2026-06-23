import { Outlet } from 'react-router-dom'
import { Nav } from '@/components/Nav'
import { Footer } from '@/components/Footer'
import { ActivityPanel } from '@/components/ActivityPanel'
import { useAuth } from '@/hooks/useAuth'

// Shared chrome (persistent Nav + Footer around <Outlet/>) — auth-agnostic on
// purpose: under <AuthGate> it wraps the protected pages (user is guaranteed),
// and standalone it wraps public-or-authed pages like pricing, where useAuth
// resolves to null for anonymous visitors and the Nav renders its logged-out
// state. (Issue 85b — split out of the old AppLayout.)
//
// ActivityPanel (Issue 211) is mounted here alongside the Outlet so it persists
// across all SPA routes. It self-hides when the activeTasks store is empty.
export function AppChrome() {
  const { user, balance } = useAuth()
  return (
    <div className="flex min-h-screen flex-col">
      <Nav user={user} balance={balance} />
      <Outlet />
      <Footer />
      <ActivityPanel />
    </div>
  )
}
