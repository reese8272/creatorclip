// Global activity panel (Issue 211 — supersedes Issue 160).
//
// Fixed floating widget (bottom-right) that reads from the activeTasks store via
// useSyncExternalStore (React 18+ canonical pattern for external stores).
// Persists across all SPA routes because it is mounted in AppChrome outside
// the Outlet.
//
// SSE cap compliance: before opening a new EventSource for a task entry the
// panel checks isCapExhausted() — if true that slot is shown as "waiting" and
// no 4th EventSource is opened.
//
// Accessibility: respects prefers-reduced-motion via matchMedia — enter/exit
// transitions are gated on that query.

import { useEffect, useSyncExternalStore } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  subscribe,
  getSnapshot,
  upsert,
  isCapExhausted,
  type TaskEntry,
} from '@/stores/activeTasks'
import { subscribeToTaskStream } from '@/lib/taskStream'
import { sendActivity } from '@/lib/activity'
import { api } from '@/lib/api'
import type { NotificationItem, NotificationList } from '@/types'

// ── Motion guard ──────────────────────────────────────────────────────────────

function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

// ── Stage label map (mirrors StageStepper) ────────────────────────────────────

const STAGE_LABELS: Record<string, string> = {
  ingest: 'Ingesting',
  transcribe: 'Transcribing',
  signals: 'Analysing signals',
  render: 'Rendering',
  clean: 'Cleaning',
}

function phaseLabel(entry: TaskEntry): string {
  if (entry.phase === 'done') return 'Done'
  if (entry.phase === 'error') return 'Failed'
  if (entry.phase === 'pending') return 'Queued'
  // running — show stage or step label
  if (entry.stage) return STAGE_LABELS[entry.stage] ?? entry.stage
  if (entry.label) return entry.label
  return 'Running'
}

function deepLinkHref(entry: TaskEntry): string {
  // Clip-render tasks navigate to the review page; everything else to the video page.
  return `/app/video/${entry.videoId}`
}

// ── SSE subscription manager ──────────────────────────────────────────────────
// For each task entry that has a streamUrl and is not yet subscribed, open an
// EventSource (if the cap allows). Cleanup closes all open connections.

function useTaskSubscriptions(tasks: Map<string, TaskEntry>): void {
  useEffect(() => {
    const closers: Array<() => void> = []

    for (const entry of tasks.values()) {
      if (!entry.streamUrl) continue
      if (entry.subscribed) continue
      if (entry.phase === 'done' || entry.phase === 'error') continue
      if (isCapExhausted()) continue

      // Mark subscribed before opening so isCapExhausted() is accurate for
      // subsequent iterations of this loop.
      upsert(entry.taskId, { subscribed: true })

      const sub = subscribeToTaskStream(entry.streamUrl, {
        onStep: (label) => upsert(entry.taskId, { label, phase: 'running' }),
        onStage: (stage) => upsert(entry.taskId, { stage, phase: 'running' }),
        onDone: () => {
          upsert(entry.taskId, { phase: 'done', subscribed: false })
        },
        onError: () => {
          upsert(entry.taskId, { phase: 'error', subscribed: false })
        },
      })

      closers.push(() => sub.close())
    }

    return () => {
      closers.forEach((fn) => fn())
    }
    // Re-run whenever the task map reference changes (store immutably replaces).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tasks])
}

// ── Task row ─────────────────────────────────────────────────────────────────

interface TaskRowProps {
  entry: TaskEntry
  reducedMotion: boolean
}

function TaskRow({ entry, reducedMotion }: TaskRowProps) {
  const href = deepLinkHref(entry)
  const label = phaseLabel(entry)
  const isCapBlocked = !entry.subscribed && entry.phase === 'running' && isCapExhausted()

  return (
    <div
      className={[
        'flex items-center justify-between gap-2 rounded-md px-3 py-2',
        'bg-surface-raised text-sm',
        reducedMotion ? '' : 'transition-opacity duration-200',
        entry.phase === 'done' || entry.phase === 'error' ? 'opacity-70' : '',
      ]
        .filter(Boolean)
        .join(' ')}
    >
      <span className="truncate text-subtle" style={{ maxWidth: '14rem' }}>
        {label}
        {isCapBlocked && (
          <span className="ml-1 text-xs text-muted">(waiting — cap reached)</span>
        )}
      </span>
      <Link
        to={href}
        className="shrink-0 text-xs text-accent underline-offset-2 hover:underline"
        onClick={() =>
          sendActivity('click', 'activity-panel-deeplink', {
            source: 'ui',
            target: 'activity-panel-deeplink',
            taskId: entry.taskId,
            videoId: entry.videoId,
          })
        }
      >
        view
      </Link>
    </div>
  )
}

// ── Notification row ───────────────────────────────────────────────────────────
// Durable in-app notifications (Issue 245) — distinct from the transient task
// rows above. Each is dismissible (POST /api/notifications/{id}/dismiss).

function NotificationRow({
  item,
  onDismiss,
}: {
  item: NotificationItem
  onDismiss: (id: string) => void
}) {
  return (
    <div className="flex items-start justify-between gap-2 rounded-md bg-surface-raised px-3 py-2 text-sm">
      <span className="min-w-0">
        <span className="block truncate font-medium text-fg">{item.title}</span>
        <span className="block text-xs text-subtle">{item.body}</span>
        {item.link_url && (
          <Link
            to={item.link_url}
            className="mt-0.5 inline-block text-xs text-accent underline-offset-2 hover:underline"
          >
            view
          </Link>
        )}
      </span>
      <button
        type="button"
        aria-label="Dismiss notification"
        onClick={() => onDismiss(item.id)}
        className="shrink-0 text-xs text-muted hover:text-fg"
      >
        ✕
      </button>
    </div>
  )
}

// ── Panel ─────────────────────────────────────────────────────────────────────

export function ActivityPanel() {
  const tasks = useSyncExternalStore(subscribe, getSnapshot)
  const reducedMotion = prefersReducedMotion()
  const queryClient = useQueryClient()

  // Drive SSE subscriptions from the store state.
  useTaskSubscriptions(tasks)

  // Durable in-app notifications. Fail-open: a fetch error (e.g. logged-out)
  // leaves notifications empty so the panel still works for live tasks.
  const notificationsQuery = useQuery({
    queryKey: ['notifications'],
    queryFn: () => api<NotificationList>('/api/notifications'),
    retry: false,
  })
  const dismiss = useMutation({
    mutationFn: (id: string) =>
      api(`/api/notifications/${id}/dismiss`, { method: 'POST' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['notifications'] })
    },
  })

  const notifications = notificationsQuery.data?.items ?? []
  const entries = Array.from(tasks.values())

  if (tasks.size === 0 && notifications.length === 0) return null

  return (
    <div
      role="region"
      aria-label="Active tasks"
      style={{
        position: 'fixed',
        bottom: '1.5rem',
        right: '1.5rem',
        zIndex: 50,
        width: 'min(22rem, 90vw)',
      }}
      className={[
        'flex flex-col gap-1.5 rounded-lg border border-border bg-surface shadow-lg p-3',
        reducedMotion ? '' : 'transition-all duration-200',
      ]
        .filter(Boolean)
        .join(' ')}
    >
      {entries.length > 0 && (
        <>
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-subtle select-none">
            In progress
          </p>
          {entries.map((entry) => (
            <TaskRow key={entry.taskId} entry={entry} reducedMotion={reducedMotion} />
          ))}
        </>
      )}
      {notifications.length > 0 && (
        <>
          <p className="mb-1 mt-1 text-xs font-semibold uppercase tracking-wide text-subtle select-none">
            Notifications
          </p>
          {notifications.map((item) => (
            <NotificationRow key={item.id} item={item} onDismiss={(id) => dismiss.mutate(id)} />
          ))}
        </>
      )}
    </div>
  )
}
