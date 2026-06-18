// SSE consumer for long-running task streams (DNA rebuild, and later the Pro
// chatbot). Ported from static/progressStream.js — same wire format (named
// JSON events: step / cache / thinking / token / error / done) so it works
// against the existing /tasks/{id}/events endpoint unchanged.

interface StreamEvent {
  label?: string
  detail?: string
  cache_read?: number
  input_tokens?: number
  chunk?: string
  message?: string
  version?: number
}

interface StreamHandlers {
  onRender?: (buffer: string) => void
  onDone?: (data: StreamEvent) => void
  onError?: (message: string) => void
}

const RENDERERS: Record<string, (d: StreamEvent) => string> = {
  step: (d) => `→ ${d.label || 'step'}${d.detail ? `: ${d.detail}` : ''}`,
  cache: (d) =>
    `· cache ${(d.cache_read ?? 0) > 0 ? 'HIT' : 'miss'} (${d.input_tokens || 0} input tok)`,
  thinking: (d) => d.chunk || '',
  token: (d) => d.chunk || '',
  error: (d) => `! ${d.message || 'unknown error'}`,
  done: (d) => `✓ done${d.version != null ? ` v${d.version}` : ''}`,
}

const INLINE = new Set(['thinking', 'token'])

export interface StreamSubscription {
  close: () => void
}

interface ChatStreamHandlers {
  /** Called with each assistant text chunk as it streams. */
  onToken: (chunk: string) => void
  /** Called when the model starts a creator-scoped tool lookup (label e.g. "tool:get_recent_videos"). */
  onStep?: (label: string) => void
  onDone?: () => void
  onError?: (message: string) => void
}

// Chat-specific consumer for the Pro chatbot (Issue 152). Unlike
// subscribeToTaskStream (which flattens steps + tokens into one buffer for the
// DNA-rebuild log), this keeps assistant `token` text separate from tool `step`
// status so the chat bubble shows only the reply, with tool lookups surfaced as
// a transient indicator.
export function subscribeToChatStream(
  url: string,
  handlers: ChatStreamHandlers,
): StreamSubscription {
  const es = new EventSource(url, { withCredentials: true })

  const parse = (evt: MessageEvent): StreamEvent => {
    try {
      return JSON.parse(evt.data)
    } catch {
      return { message: String(evt.data) }
    }
  }

  es.addEventListener('token', (e) => handlers.onToken(parse(e as MessageEvent).chunk || ''))
  es.addEventListener('step', (e) => {
    const label = parse(e as MessageEvent).label
    if (label) handlers.onStep?.(label)
  })
  es.addEventListener('done', () => {
    handlers.onDone?.()
    es.close()
  })
  es.addEventListener('error', (e) => {
    // Native EventSource emits an `error` Event (no data) on network drop; the
    // server emits a named `error` SSE with a message. Distinguish by payload.
    const msg = (e as MessageEvent).data
      ? parse(e as MessageEvent).message || 'unknown error'
      : 'Connection lost.'
    handlers.onError?.(msg)
    es.close()
  })

  return { close: () => es.close() }
}

export function subscribeToTaskStream(url: string, handlers: StreamHandlers): StreamSubscription {
  const es = new EventSource(url, { withCredentials: true })
  let buffer = ''

  const dispatch = (type: string, evt: MessageEvent) => {
    let data: StreamEvent
    try {
      data = JSON.parse(evt.data)
    } catch {
      data = { message: String(evt.data) }
    }
    const render = RENDERERS[type]
    if (render) {
      const line = render(data)
      buffer = INLINE.has(type) ? buffer + line : buffer ? `${buffer}\n${line}` : line
      handlers.onRender?.(buffer)
    }
    if (type === 'done') handlers.onDone?.(data)
    if (type === 'error') handlers.onError?.(data.message || 'unknown error')
    if (type === 'done' || type === 'error') es.close()
  }

  Object.keys(RENDERERS).forEach((t) => {
    es.addEventListener(t, (e) => dispatch(t, e as MessageEvent))
  })

  return { close: () => es.close() }
}
