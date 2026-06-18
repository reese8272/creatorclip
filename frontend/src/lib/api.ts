// Typed fetch client for the existing cookie-authed FastAPI endpoints.
//
// Every call is same-origin with `credentials: 'include'` so the session
// cookie rides along (dev: Vite proxies these prefixes to the API — see
// vite.config.ts). A 401 means the session lapsed; we bounce to the existing
// login page, matching the vanilla auth.js guard.

const LOGIN_URL = '/app/login'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

interface RequestOptions {
  method?: string
  body?: unknown
  /** When false, a 401 throws instead of redirecting (used by the auth probe). */
  redirectOn401?: boolean
}

export async function api<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, redirectOn401 = true } = opts
  const headers: Record<string, string> = {}
  let payload: BodyInit | undefined
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }

  const resp = await fetch(path, {
    method,
    credentials: 'include',
    headers,
    body: payload,
  })

  if (resp.status === 401 && redirectOn401) {
    window.location.href = LOGIN_URL
    // Never resolves — the navigation is in flight.
    return new Promise<T>(() => {})
  }

  if (!resp.ok) {
    const detail = await resp
      .json()
      .then((d: { detail?: string }) => d.detail)
      .catch(() => undefined)
    throw new ApiError(resp.status, detail || `Request failed (${resp.status})`)
  }

  if (resp.status === 204) return undefined as T
  return (await resp.json()) as T
}
