import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'
import { DisclaimerBand } from '@/components/DisclaimerBand'
import { Chip } from '@/components/Chip'
import { ChipThinking } from '@/components/chip/ChipStates'
import { Button } from '@/components/ui/button'
import { subscribeToChatStream, type StreamSubscription } from '@/lib/taskStream'

interface ChatMsg {
  role: 'user' | 'assistant'
  content: string
}

// Empty-state suggestion pills — click to send (Issue 309 fidelity).
const SUGGESTIONS = [
  'What were my best videos this month?',
  'When should I post?',
  'Which hooks work best for me?',
  'What should I try next?',
]

interface QueuedReply {
  task_id: string
  stream_url: string | null
  conversation_id: string
}

// Turn an internal tool label ("tool:get_recent_videos") into friendly status.
function toolLabel(label: string): string {
  const name = label.replace(/^tool:/, '')
  const map: Record<string, string> = {
    get_channel_dna: 'reading your channel DNA',
    get_recent_videos: 'pulling your recent videos',
    get_video_performance: 'checking that video',
    get_channel_averages: 'computing your channel averages',
    get_upload_timing: 'finding your best upload windows',
  }
  return map[name] || 'looking at your data'
}

export function Chat() {
  const [messages, setMessages] = useState<ChatMsg[]>([])
  const [streamingText, setStreamingText] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [toolStatus, setToolStatus] = useState<string | null>(null)
  const [input, setInput] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [retryable, setRetryable] = useState(false)
  const [reconnecting, setReconnecting] = useState(false)
  const [gated, setGated] = useState(false)
  const conversationId = useRef<string | null>(null)
  const lastUserText = useRef<string | null>(null)
  const sub = useRef<StreamSubscription | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  // Close any live stream on unmount.
  useEffect(() => () => sub.current?.close(), [])

  // Keep the latest turn in view.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [messages, streamingText, toolStatus])

  function startStream(reply: QueuedReply) {
    if (!reply.stream_url) {
      setError('Could not open the response stream — please retry.')
      setStreaming(false)
      return
    }
    setStreamingText('')
    setToolStatus(null)
    setReconnecting(false)
    setStreaming(true)
    sub.current = subscribeToChatStream(reply.stream_url, {
      onToken: (chunk) => {
        setToolStatus(null)
        setReconnecting(false)
        setStreamingText((prev) => prev + chunk)
      },
      onStep: (label) => setToolStatus(toolLabel(label)),
      onReconnecting: () => setReconnecting(true),
      onReconnected: () => setReconnecting(false),
      onDone: () => {
        setStreaming(false)
        setReconnecting(false)
        setStreamingText((finalText) => {
          if (finalText) setMessages((m) => [...m, { role: 'assistant', content: finalText }])
          return ''
        })
        setToolStatus(null)
      },
      onError: (msg) => {
        setStreaming(false)
        setReconnecting(false)
        setToolStatus(null)
        // Preserve whatever streamed before the drop so the partial answer isn't
        // lost; the user can read it and hit Retry for a fresh, complete reply.
        setStreamingText((partial) => {
          if (partial) setMessages((m) => [...m, { role: 'assistant', content: partial }])
          return ''
        })
        setError(msg)
        setRetryable(true)
      },
    })
  }

  // Re-run the last turn after a connection drop. Prefer regenerate (drops any
  // partial assistant bubble server- and client-side); fall back to resending the
  // last user message if there's no conversation yet.
  function retry() {
    setError(null)
    setRetryable(false)
    if (conversationId.current) {
      regenerate()
    } else if (lastUserText.current) {
      send(lastUserText.current)
    }
  }

  async function send(override?: string) {
    const text = (override ?? input).trim()
    if (!text || streaming) return
    setError(null)
    setRetryable(false)
    lastUserText.current = text
    setInput('')
    setMessages((m) => [...m, { role: 'user', content: text }])
    try {
      const reply = await api<QueuedReply>('/api/chat/messages', {
        method: 'POST',
        body: { conversation_id: conversationId.current, message: text },
      })
      conversationId.current = reply.conversation_id
      startStream(reply)
    } catch (e) {
      handleSendError(e)
    }
  }

  async function regenerate() {
    if (streaming || !conversationId.current) return
    setError(null)
    setRetryable(false)
    // Drop the last assistant bubble locally; the server drops it too.
    setMessages((m) => (m.length && m[m.length - 1].role === 'assistant' ? m.slice(0, -1) : m))
    try {
      const reply = await api<QueuedReply>(
        `/api/chat/conversations/${conversationId.current}/regenerate`,
        { method: 'POST' },
      )
      startStream(reply)
    } catch (e) {
      handleSendError(e)
    }
  }

  function handleSendError(e: unknown) {
    setStreaming(false)
    if (e instanceof ApiError && e.status === 402) {
      setGated(true)
      setError(e.message)
    } else if (e instanceof ApiError && e.status === 429) {
      setError("You've reached today's message limit. It resets tomorrow.")
    } else {
      setError(e instanceof ApiError ? e.message : 'Something went wrong — please retry.')
    }
  }

  function stop() {
    sub.current?.close()
    setStreaming(false)
    setStreamingText((finalText) => {
      if (finalText) setMessages((m) => [...m, { role: 'assistant', content: finalText }])
      return ''
    })
    setToolStatus(null)
  }

  const empty = messages.length === 0 && !streaming
  const canRegenerate =
    !streaming && messages.length > 0 && messages[messages.length - 1].role === 'assistant'

  return (
    <>
      <DisclaimerBand>
        The assistant answers from your own channel data. It estimates fit with your style and
        audience — it does not promise virality.
      </DisclaimerBand>

      <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col px-4 py-6">
        <div
          ref={scrollRef}
          className={cn(
            'flex-1 overflow-y-auto pb-4',
            // Center the intro in the available space until the first message,
            // then fall back to top-aligned scrolling with inter-bubble spacing.
            empty ? 'flex items-center justify-center' : 'space-y-4',
          )}
        >
          {empty && (
            <div className="flex flex-col items-center text-center text-sm text-muted">
              <Chip pose="wave" size={72} />
              <h2 className="mb-2 mt-3 text-h2 text-fg">Ask about your channel</h2>
              <p className="max-w-md">
                I pull your own analytics to answer — best videos, upload timing, hooks, and what to
                try next.
              </p>
              <div className="mt-5 flex flex-wrap justify-center gap-2">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => send(s)}
                    className="rounded-full border border-strong bg-surface px-3.5 py-1.5 text-small text-fg shadow-inset transition-colors hover:bg-elevated focus:border-accent focus:outline-none"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m, i) => (
            <Bubble key={i} role={m.role} content={m.content} />
          ))}

          {/* While streaming: Chip "thinking" until the first token arrives, then
              the live bubble with a blink caret (Issue 309). */}
          {streaming &&
            (streamingText ? (
              <Bubble role="assistant" content={streamingText} streaming />
            ) : (
              <div className="flex justify-start">
                <ChipThinking size={64} />
              </div>
            ))}
          {toolStatus && (
            <p className="px-1 text-xs italic text-muted">…{toolStatus}</p>
          )}
          {reconnecting && (
            <p className="px-1 text-xs italic text-muted">Reconnecting…</p>
          )}
        </div>

        {error && (
          <div className="mb-3 flex items-center justify-between gap-3 rounded-md border border-[color:var(--color-danger-border)] bg-[color:var(--color-danger-soft)] px-3 py-2 text-sm text-danger">
            <span>
              {error}
              {gated && (
                <a href="/app/pricing" className="ml-2 font-medium underline">
                  View plans →
                </a>
              )}
            </span>
            {retryable && !gated && (
              <Button variant="ghost" size="sm" onClick={retry} className="shrink-0">
                ↻ Retry
              </Button>
            )}
          </div>
        )}

        {canRegenerate && (
          <div className="mb-2">
            <Button variant="ghost" size="sm" onClick={regenerate}>
              ↻ Regenerate
            </Button>
          </div>
        )}

        <form
          onSubmit={(e) => {
            e.preventDefault()
            send()
          }}
          className="flex items-end gap-2 border-t border-default pt-3"
        >
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                send()
              }
            }}
            rows={1}
            placeholder="Ask about your channel…"
            disabled={gated}
            className="max-h-40 min-h-[40px] flex-1 resize-y rounded-md border border-strong bg-surface px-3 py-2 text-sm text-fg placeholder:text-muted focus:border-accent focus:outline-none disabled:opacity-50"
          />
          {streaming ? (
            <Button type="button" variant="secondary" onClick={stop}>
              Stop
            </Button>
          ) : (
            <Button type="submit" disabled={!input.trim() || gated}>
              Send
            </Button>
          )}
        </form>
      </main>
    </>
  )
}

function Bubble({
  role,
  content,
  streaming,
}: {
  role: 'user' | 'assistant'
  content: string
  streaming?: boolean
}) {
  const isUser = role === 'user'
  return (
    <div className={isUser ? 'flex justify-end' : 'flex items-start justify-start gap-2'}>
      {!isUser && <Chip pose="think" size={28} className="mt-1 flex-shrink-0" />}
      <div
        className={
          isUser
            ? 'max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-accent px-4 py-2 text-sm text-on-accent'
            : 'max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-bl-sm border border-default bg-surface px-4 py-2 text-sm text-fg'
        }
      >
        {content}
        {streaming && (
          <span
            className="ml-px inline-block h-3.5 w-[7px] translate-y-0.5 bg-accent-text"
            style={{ animation: 'chip-blink 1s steps(1) infinite' }}
          />
        )}
      </div>
    </div>
  )
}
