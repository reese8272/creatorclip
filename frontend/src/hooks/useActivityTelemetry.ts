import { useEffect, useRef } from 'react'
import { useLocation } from 'react-router-dom'
import { installActivityListeners, sendActivity } from '@/lib/activity'

// SPA UI telemetry wiring (Issue 155). Installs the delegated click/submit
// listeners once for the app's lifetime, and emits a `navigate` event on the
// initial load and every subsequent route change. Mount ONCE, high in the tree,
// inside the Router (it needs useLocation).
export function useActivityTelemetry(): void {
  const location = useLocation()
  const lastPath = useRef<string | null>(null)

  useEffect(() => installActivityListeners(), [])

  useEffect(() => {
    const path = location.pathname + location.search
    if (lastPath.current === path) return
    lastPath.current = path
    sendActivity('navigate', path, {})
  }, [location.pathname, location.search])
}
