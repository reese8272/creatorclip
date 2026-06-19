// Client UI telemetry (Issue 155). Restores the behavior of the old
// static/activity.js that the React cutover (Issue 85) dropped: fire-and-forget
// POSTs to /api/activity for clicks, form submits, and route changes. The
// backend (routers/activity.py) resolves the creator from the session cookie,
// redacts, and persists to the event_logs sink (Issue 151).
//
// Hard rule: telemetry must NEVER throw into the UI or affect UX. Every send is
// best-effort and swallowed. Targets/pages are sliced to the server's Pydantic
// limits (page<=100, target<=200) so a long title can't 422 the request.

export type ActivityType = 'click' | 'submit' | 'navigate'

function currentPage(): string {
  return (document.title || window.location.pathname).slice(0, 100)
}

export function sendActivity(
  eventType: ActivityType,
  target: string,
  extra: Record<string, unknown> = {},
): void {
  try {
    void fetch('/api/activity', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        page: currentPage(),
        event_type: eventType,
        target: String(target).slice(0, 200),
        extra,
      }),
      credentials: 'include',
      keepalive: true,
    }).catch(() => {})
  } catch {
    /* never let telemetry break the app */
  }
}

function onClick(e: MouseEvent): void {
  const el = (e.target as Element | null)?.closest<HTMLElement>(
    'button, a, [data-log], input[type=submit], input[type=button]',
  )
  if (!el) return
  // textContent (not innerText) so labels resolve in both browsers and jsdom.
  const label =
    el.dataset.log ||
    el.textContent?.trim().slice(0, 80) ||
    el.getAttribute('aria-label') ||
    el.id ||
    el.className ||
    el.tagName
  const href = el instanceof HTMLAnchorElement ? el.href : undefined
  sendActivity('click', label, href ? { href } : {})
}

function onSubmit(e: SubmitEvent): void {
  const form = e.target as HTMLFormElement
  sendActivity('submit', form.id || form.getAttribute('action') || 'form', {})
}

// Delegated capture-phase listeners matching the old activity.js footprint.
// Returns a cleanup that removes them.
export function installActivityListeners(): () => void {
  document.addEventListener('click', onClick, true)
  document.addEventListener('submit', onSubmit, true)
  return () => {
    document.removeEventListener('click', onClick, true)
    document.removeEventListener('submit', onSubmit, true)
  }
}
