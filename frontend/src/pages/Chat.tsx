import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '@/lib/api'
import { useAuth } from '@/hooks/useAuth'
import { Nav } from '@/components/Nav'
import { Footer } from '@/components/Footer'
import { Button } from '@/components/ui/button'
import { subscribeToChatStream, type StreamSubscription } from '@/lib/taskStream'

interface ChatMsg {
  role: 'user' | 'assistant'
  content: string
}

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
  const { user, balance, loading } = useAuth()
  const [messages, setMessages] = useState<ChatMsg[]>([])
  const [streamingText, setStreamingText] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [toolStatus, setToolStatus] = useState<string | null>(null)
  const [input, setInput] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [gated, setGated] = useState(false)
  const conversationId = useRef<string | null>(null)
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
    setStreaming(true)
    sub.current = subscribeToChatStream(reply.stream_url, {
      onToken: (chunk) => {
        setToolStatus(null)
        setStreamingText((prev) => prev + chunk)
      },
      onStep: (label) => setToolStatus(toolLabel(label)),
      onDone: () => {
        setStreaming(false)
        setStreamingText((finalText) => {
          if (finalText) setMessages((m) => [...m, { role: 'assistant', content: finalText }])
          return ''
        })
        setToolStatus(null)
      },
      onError: (msg) => {
        setStreaming(false)
        setToolStatus(null)
        setError(msg)
      },
    })
  }

  async function send() {
    const text = input.trim()
    if (!text || streaming) return
    setError(null)
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

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-muted">Loading…</div>
    )
  }

  const empty = messages.length === 0 && !streaming
  const canRegenerate =
    !streaming && messages.length > 0 && messages[messages.length - 1].role === 'assistant'

  return (
    <div className="flex min-h-screen flex-col">
      <Nav user={user} balance={balance} />

      <div className="border-b border-default bg-surface px-6 py-2 text-center text-xs text-muted">
        The assistant answers from your own channel data. It estimates fit with your style and
        audience — it does not promise virality.
      </div>

      <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col px-4 py-6">
        <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto pb-4">
          {empty && (
            <div className="mt-12 text-center text-sm text-muted">
              <p className="mb-2 text-base font-medium text-fg">Ask about your channel</p>
              <p>
                Try “What were my best videos this month?” or “When should I post?” — I’ll pull your
                own analytics to answer.
              </p>
            </div>
          )}

          {messages.map((m, i) => (
            <Bubble key={i} role={m.role} content={m.content} />
          ))}

          {streaming && <Bubble role="assistant" content={streamingText} streaming />}
          {toolStatus && (
            <p className="px-1 text-xs italic text-muted">…{toolStatus}</p>
          )}
        </div>

        {error && (
          <div className="mb-3 rounded-md border border-[color:var(--color-danger-border)] bg-[color:var(--color-danger-soft)] px-3 py-2 text-sm text-danger">
            {error}
            {gated && (
              <a href="/static/pricing.html" className="ml-2 font-medium underline">
                View plans →
              </a>
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

      <Footer />
    </div>
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
    <div className={isUser ? 'flex justify-end' : 'flex justify-start'}>
      <div
        className={
          isUser
            ? 'max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-accent px-4 py-2 text-sm text-on-accent'
            : 'max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-bl-sm border border-default bg-surface px-4 py-2 text-sm text-fg'
        }
      >
        {content}
        {streaming && <span className="ml-0.5 inline-block animate-pulse">▍</span>}
      </div>
    </div>
  )
}
