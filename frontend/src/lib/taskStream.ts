// SSE consumer for long-running task streams (DNA rebuild, and later the Pro
// chatbot). Ported from static/progressStream.js — same wire format (named
// JSON events: step / cache / thinking / token / error / done) so it works
// against the existing /tasks/{id}/events endpoint unchanged.

interface StreamEvent {
  label?: string
  stage?: string
  detail?: string
  cache_read?: number
  input_tokens?: number
  chunk?: string
  message?: string
  version?: number
}

interface StreamHandlers {
  onRender?: (buffer: string) => void
  /** Each assistant/prose `token` chunk as it streams (analysis narrative). */
  onToken?: (chunk: string) => void
  /** Latest `step` label — drives a status chip without the flattened buffer. */
  onStep?: (label: string) => void
  /** Coarse pipeline stage from a `step` event (ingest/transcribe/signals/render/clean). */
  onStage?: (stage: string) => void
  /** Final `done` event payload — carries structured results (suggestions,
   *  concepts, report, chapters, …) for the analysis features (Issue 85e). */
  onDone?: (data: Record<string, unknown>) => void
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
  /** Transient transport drop — a reconnect is in flight (attempt is 1-based). */
  onReconnecting?: (attempt: number) => void
  /** A reconnect succeeded and the stream is live again. */
  onReconnected?: () => void
}

// How many transient reconnect attempts before we surface a terminal
// "Connection lost." The server resumes from the Last-Event-ID cursor on
// reconnect (routers/tasks.py), so a recovered drop replays missed events.
const MAX_CHAT_RECONNECTS = 5
const CHAT_RECONNECT_BASE_MS = 1000

// Chat-specific consumer for the Pro chatbot (Issue 152). Unlike
// subscribeToTaskStream (which flattens steps + tokens into one buffer for the
// DNA-rebuild log), this keeps assistant `token` text separate from tool `step`
// status so the chat bubble shows only the reply, with tool lookups surfaced as
// a transient indicator.
//
// Resilience (2026-06-29): the previous version called es.close() on the FIRST
// transport `error`, which kills EventSource's built-in auto-reconnect — so any
// transient blip (a 5G→Wi-Fi handoff, a proxy idle-drop) became a permanent
// "Connection lost." dead-end. Now a transport drop is treated as transient: the
// browser's native auto-reconnect (readyState CONNECTING) is allowed to proceed,
// a CLOSED socket is manually reopened with backoff, and only a server-sent named
// `error` event or exhausting MAX_CHAT_RECONNECTS surfaces a terminal error.
export function subscribeToChatStream(
  url: string,
  handlers: ChatStreamHandlers,
): StreamSubscription {
  let es: EventSource | null = null
  let attempts = 0
  let done = false
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null

  const parse = (evt: MessageEvent): StreamEvent => {
    try {
      return JSON.parse(evt.data)
    } catch {
      return { message: String(evt.data) }
    }
  }

  const giveUp = (msg: string) => {
    done = true
    if (reconnectTimer) clearTimeout(reconnectTimer)
    es?.close()
    handlers.onError?.(msg)
  }

  const open = () => {
    es = new EventSource(url, { withCredentials: true })

    es.onopen = () => {
      // A successful (re)connection clears the transient-failure budget so a
      // single early blip in a long, healthy stream never counts toward the cap.
      if (attempts > 0) handlers.onReconnected?.()
      attempts = 0
    }

    es.addEventListener('token', (e) => handlers.onToken(parse(e as MessageEvent).chunk || ''))
    es.addEventListener('step', (e) => {
      const label = parse(e as MessageEvent).label
      if (label) handlers.onStep?.(label)
    })
    es.addEventListener('done', () => {
      done = true
      es?.close()
      handlers.onDone?.()
    })
    es.addEventListener('error', (e) => {
      if (done) return
      const me = e as MessageEvent
      // A server-sent NAMED error event carries data → a real, terminal failure.
      if (me.data) {
        giveUp(parse(me).message || 'unknown error')
        return
      }
      // Otherwise it's a transport drop. Bound the attempts so a turn that
      // finished (or was abandoned) while disconnected can't hang forever.
      attempts += 1
      if (attempts > MAX_CHAT_RECONNECTS) {
        giveUp('Connection lost.')
        return
      }
      handlers.onReconnecting?.(attempts)
      // readyState CONNECTING → the browser is already auto-reconnecting; let it.
      // readyState CLOSED → it won't, so reopen manually with linear backoff.
      if (es?.readyState === EventSource.CLOSED) {
        es.close()
        reconnectTimer = setTimeout(() => {
          if (!done) open()
        }, CHAT_RECONNECT_BASE_MS * attempts)
      }
    })
  }

  open()
  return {
    close: () => {
      done = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      es?.close()
    },
  }
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
    if (type === 'token') handlers.onToken?.(data.chunk || '')
    if (type === 'step') {
      handlers.onStep?.(data.label || '')
      if (data.stage) handlers.onStage?.(data.stage)
    }
    if (type === 'done') handlers.onDone?.(data as unknown as Record<string, unknown>)
    if (type === 'error') handlers.onError?.(data.message || 'unknown error')
    if (type === 'done' || type === 'error') es.close()
  }

  Object.keys(RENDERERS).forEach((t) => {
    es.addEventListener(t, (e) => dispatch(t, e as MessageEvent))
  })

  return { close: () => es.close() }
}
