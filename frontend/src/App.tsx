import { createBrowserRouter, Navigate, RouterProvider } from 'react-router-dom'
import { AuthGate } from '@/components/AuthGate'
import { AppChrome } from '@/components/AppChrome'
import { Dashboard } from '@/pages/Dashboard'
import { Insights } from '@/pages/Insights'
import { Analysis } from '@/pages/Analysis'
import { Review } from '@/pages/Review'
import { Profile } from '@/pages/Profile'
import { Chat } from '@/pages/Chat'
import { Pricing } from '@/pages/Pricing'
import { Login } from '@/pages/Login'
import { Walkthrough } from '@/pages/Walkthrough'
import { Onboarding } from '@/pages/Onboarding'

// React Router v7 Data Mode. The SPA mounts under /app (Vite base + FastAPI
// fallback); `basename` keeps client routes relative to that prefix. Four layout
// contexts (Issue 85b): protected+chrome (the app), protected+bare (focused
// first-run flows), public+chrome (pricing — anon sees prices), public+bare
// (the pre-auth sign-in). Add child routes here as more pages port.
const router = createBrowserRouter(
  [
    {
      element: <AuthGate />,
      children: [
        {
          element: <AppChrome />,
          children: [
            { path: 'dashboard', element: <Dashboard /> },
            { path: 'insights', element: <Insights /> },
            { path: 'analysis', element: <Analysis /> },
            { path: 'review', element: <Review /> },
            { path: 'profile', element: <Profile /> },
            { path: 'chat', element: <Chat /> },
          ],
        },
        { path: 'walkthrough', element: <Walkthrough /> },
        { path: 'onboarding', element: <Onboarding /> },
      ],
    },
    {
      element: <AppChrome />,
      children: [{ path: 'pricing', element: <Pricing /> }],
    },
    { path: 'login', element: <Login /> },
    { path: '*', element: <Navigate to="/dashboard" replace /> },
  ],
  { basename: '/app' },
)

export default function App() {
  return <RouterProvider router={router} />
}
