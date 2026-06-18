import { Outlet } from 'react-router-dom'
import { Nav } from '@/components/Nav'
import { Footer } from '@/components/Footer'
import { useAuth } from '@/hooks/useAuth'

// Shared chrome (persistent Nav + Footer around <Outlet/>) — auth-agnostic on
// purpose: under <AuthGate> it wraps the protected pages (user is guaranteed),
// and standalone it wraps public-or-authed pages like pricing, where useAuth
// resolves to null for anonymous visitors and the Nav renders its logged-out
// state. (Issue 85b — split out of the old AppLayout.)
export function AppChrome() {
  const { user, balance } = useAuth()
  return (
    <div className="flex min-h-screen flex-col">
      <Nav user={user} balance={balance} />
      <Outlet />
      <Footer />
    </div>
  )
}
