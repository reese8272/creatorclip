import { createBrowserRouter, Navigate, Outlet, RouterProvider, useRouteError } from 'react-router-dom'
import { AuthGate } from '@/components/AuthGate'
import { AppChrome } from '@/components/AppChrome'
import { useActivityTelemetry } from '@/hooks/useActivityTelemetry'
import { Dashboard } from '@/pages/Dashboard'
import { Insights } from '@/pages/Insights'
import { Analysis } from '@/pages/Analysis'
import { Review } from '@/pages/Review'
import { Profile } from '@/pages/Profile'
import { Chat } from '@/pages/Chat'
import { Pricing } from '@/pages/Pricing'
import { Login } from '@/pages/Login'
import { Landing } from '@/pages/Landing'
import { Walkthrough } from '@/pages/Walkthrough'
import { Onboarding } from '@/pages/Onboarding'
import { VideoClipsMap } from '@/pages/VideoClipsMap'
import { Recap } from '@/pages/Recap'
import { Editor } from '@/pages/Editor'
import { Settings } from '@/pages/Settings'

// Root layout: owns app-wide concerns that must span every route. Today that's
// UI telemetry (Issue 155 — emits a `navigate` event on each route change and
// installs the delegated click/submit listeners); it renders only an <Outlet/>.
function RootLayout() {
  useActivityTelemetry()
  return <Outlet />
}

// Catches any render throw in any child route so a crash never blanks the whole
// SPA — instead shows a branded recovery UI (Issue 346).
function RootError() {
  const error = useRouteError()
  console.error('[RootError]', error)
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-6 bg-bg p-8 text-center text-fg">
      <h1 className="text-2xl font-semibold">Something went wrong</h1>
      <p className="max-w-md text-sm text-muted">
        An unexpected error occurred. You can try reloading the page or returning to the dashboard.
      </p>
      <div className="flex gap-3">
        <button
          onClick={() => window.location.reload()}
          className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-on-accent hover:bg-accent-hover"
        >
          Reload
        </button>
        <a
          href="/app/dashboard"
          className="rounded-md border border-strong px-4 py-2 text-sm font-medium text-fg hover:bg-elevated"
        >
          Back to dashboard
        </a>
      </div>
    </div>
  )
}

// React Router v7 Data Mode. The SPA mounts under /app (Vite base + FastAPI
// fallback); `basename` keeps client routes relative to that prefix. Four layout
// contexts (Issue 85b, extended 376a): protected+chrome (the app), protected+bare
// (focused first-run flows), public+chrome (pricing — anon sees prices), public+bare
// (the pre-auth sign-in, and the marketing landing) — all nested under RootLayout.
// Add child routes here as more pages port.
//
// `landing` has NO AuthGate (Issue 376a — a public marketing page). FastAPI's
// `/` (main.py:index) serves a server-rendered static/landing.html directly
// rather than routing here, because that response must be real content without
// JS execution (Google OAuth verification + the CI pytest lane, which never
// builds the SPA bundle); this route is the in-app equivalent, reachable once
// the SPA is running.
const router = createBrowserRouter(
  [
    {
      element: <RootLayout />,
      errorElement: <RootError />,
      children: [
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
                { path: 'settings', element: <Settings /> },
                { path: 'chat', element: <Chat /> },
                { path: 'video/:videoId', element: <VideoClipsMap /> },
                { path: 'video/:videoId/recap', element: <Recap /> },
                { path: 'editor', element: <Editor /> },
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
        { path: 'landing', element: <Landing /> },
        { path: '*', element: <Navigate to="/dashboard" replace /> },
      ],
    },
  ],
  { basename: '/app' },
)

export default function App() {
  return <RouterProvider router={router} />
}
