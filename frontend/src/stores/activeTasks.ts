// Global active-tasks singleton store (Issue 211).
//
// Implements the React 18+ useSyncExternalStore contract — subscribe/getSnapshot
// — so consumers can wire it via useSyncExternalStore without adding any external
// state-management dependency (Zustand uses this same primitive internally).
//
// SSE cap posture (corrected 2026-07-02, Issue 352 Batch K): the SERVER is the
// sole enforcer of the 3-concurrent-streams-per-creator cap
// (MAX_CONCURRENT_SSE_PER_CREATOR = 3 in routers/tasks.py) — a 4th stream is
// rejected with a named `error` event ("too many open streams") that every
// consumer surfaces as an error state. This store's cap accounting
// (isCapExhausted) is a client-side courtesy pre-empt honored by the
// ActivityPanel only; other consumers (useStageStream per dashboard row,
// useTaskStream in Onboarding, the chat stream, DnaCard resync) open
// EventSources directly and rely on the server cap.
//
// Lifecycle rule: terminal entries (done / error) auto-remove after
// TERMINAL_TTL_MS so the panel empties and hides without a manual dismiss step.

export type TaskPhase = 'pending' | 'running' | 'done' | 'error'

export interface TaskEntry {
  /** Opaque internal ID returned by the task queue. */
  taskId: string
  /** The video_id this task belongs to — used for deep-link routing. */
  videoId: string
  /** Human-readable label of the current pipeline stage. */
  label: string | null
  /** Coarse stage string (ingest / transcribe / signals / render / clean). */
  stage: string | null
  phase: TaskPhase
  /** SSE stream URL to subscribe to (null until the backend returns one). */
  streamUrl: string | null
  /** Whether this slot has an open EventSource (managed externally, stored here
   *  so the panel knows whether it is actively subscribed). */
  subscribed: boolean
}

/** Maximum concurrent SSE slots per creator (mirrors routers/tasks.py). */
export const MAX_CONCURRENT_SSE = 3

/** How long a terminal (done/error) entry survives before auto-removal (ms). */
const TERMINAL_TTL_MS = 3_000

type Subscriber = () => void

// ── Internal state ────────────────────────────────────────────────────────────

let _tasks: Map<string, TaskEntry> = new Map()
const _subscribers: Set<Subscriber> = new Set()
const _removalTimers: Map<string, ReturnType<typeof setTimeout>> = new Map()

function _notify(): void {
  _subscribers.forEach((fn) => fn())
}

function _scheduleRemoval(taskId: string): void {
  // Clear any prior timer (re-entering terminal state resets the clock).
  const prior = _removalTimers.get(taskId)
  if (prior !== undefined) clearTimeout(prior)

  _removalTimers.set(
    taskId,
    setTimeout(() => {
      _removalTimers.delete(taskId)
      _tasks = new Map(_tasks)
      _tasks.delete(taskId)
      _notify()
    }, TERMINAL_TTL_MS),
  )
}

// ── Public API ────────────────────────────────────────────────────────────────

/** useSyncExternalStore `subscribe` param. */
export function subscribe(cb: Subscriber): () => void {
  _subscribers.add(cb)
  return () => _subscribers.delete(cb)
}

/** useSyncExternalStore `getSnapshot` param.  Returns a stable reference when
 *  the map hasn't changed — React will bail out of re-renders correctly. */
export function getSnapshot(): Map<string, TaskEntry> {
  return _tasks
}

/** Add or update a task entry.  Callers pass a partial overlay; fields not
 *  provided are preserved from the existing entry (if any). */
export function upsert(taskId: string, patch: Partial<Omit<TaskEntry, 'taskId'>>): void {
  const existing = _tasks.get(taskId)
  const next: TaskEntry = {
    taskId,
    videoId: patch.videoId ?? existing?.videoId ?? '',
    label: patch.label !== undefined ? patch.label : (existing?.label ?? null),
    stage: patch.stage !== undefined ? patch.stage : (existing?.stage ?? null),
    phase: patch.phase ?? existing?.phase ?? 'pending',
    streamUrl: patch.streamUrl !== undefined ? patch.streamUrl : (existing?.streamUrl ?? null),
    subscribed: patch.subscribed !== undefined ? patch.subscribed : (existing?.subscribed ?? false),
  }

  _tasks = new Map(_tasks)
  _tasks.set(taskId, next)
  _notify()

  // Schedule auto-removal when the task reaches a terminal phase.
  if (next.phase === 'done' || next.phase === 'error') {
    _scheduleRemoval(taskId)
  } else {
    // Cancel any pending removal if the task re-activates (edge case: retry).
    const prior = _removalTimers.get(taskId)
    if (prior !== undefined) {
      clearTimeout(prior)
      _removalTimers.delete(taskId)
    }
  }
}

/** Remove a task immediately (e.g. on explicit dismiss or page teardown). */
export function remove(taskId: string): void {
  const prior = _removalTimers.get(taskId)
  if (prior !== undefined) {
    clearTimeout(prior)
    _removalTimers.delete(taskId)
  }
  if (!_tasks.has(taskId)) return
  _tasks = new Map(_tasks)
  _tasks.delete(taskId)
  _notify()
}

/** How many task slots currently have an open EventSource subscription. */
export function openSubscriptionCount(): number {
  let count = 0
  for (const entry of _tasks.values()) {
    if (entry.subscribed) count++
  }
  return count
}

/** True when opening another SSE connection would exceed the server cap. */
export function isCapExhausted(): boolean {
  return openSubscriptionCount() >= MAX_CONCURRENT_SSE
}

/** Reset the entire store — used only in tests. */
export function _reset(): void {
  _removalTimers.forEach((t) => clearTimeout(t))
  _removalTimers.clear()
  _tasks = new Map()
  _notify()
}
