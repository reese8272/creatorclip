// Typed fetch client for the existing cookie-authed FastAPI endpoints.
//
// Every call is same-origin with `credentials: 'include'` so the session
// cookie rides along (dev: Vite proxies these prefixes to the API — see
// vite.config.ts). A 401 means the session lapsed; we bounce to the existing
// login page, matching the vanilla auth.js guard.

const LOGIN_URL = '/app/login'

// FastAPI's HTTPException detail is either a plain string or the structured
// {code, message} shape (e.g. 409 source_expired / pending_clean_or_edit).
// The raw detail is kept so callers can branch on `.code`; `.message` stays a
// human-readable string either way (previously an object detail rendered as
// "[object Object]").
function deriveMessage(status: number, detail: unknown): string {
  if (typeof detail === 'string' && detail) return detail
  if (detail && typeof detail === 'object') {
    const message = (detail as { message?: unknown }).message
    if (typeof message === 'string' && message) return message
  }
  return `Request failed (${status})`
}

function deriveCode(detail: unknown): string | null {
  if (detail && typeof detail === 'object') {
    const code = (detail as { code?: unknown }).code
    if (typeof code === 'string') return code
  }
  return null
}

export class ApiError extends Error {
  status: number
  /** Raw parsed `detail` from the error body — string, object, or undefined. */
  detail: unknown
  /** Machine-readable code when detail is the structured {code, message} shape. */
  code: string | null

  constructor(status: number, detail: unknown) {
    super(deriveMessage(status, detail))
    this.status = status
    this.detail = detail
    this.code = deriveCode(detail)
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
    const detail: unknown = await resp
      .json()
      .then((d: { detail?: unknown }) => d.detail)
      .catch(() => undefined)
    throw new ApiError(resp.status, detail)
  }

  if (resp.status === 204) return undefined as T
  return (await resp.json()) as T
}
