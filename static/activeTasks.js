// Cross-page task lifecycle manager (Wave 5 — Issue: cross-tab task persistence).
//
// Single source of truth for in-progress background tasks. Solves the
// problem: "user clicks DNA build, navigates to /static/insights.html,
// loses the SSE connection, has no idea anything is happening."
//
// Architecture:
//   - localStorage key `creatorclip:active_tasks` stores an array of
//     active task entries.
//   - On every page mount, this library reads the array, garbage-collects
//     stale entries (>1h old — beyond the server-side SSE stream TTL), and
//     opens a fresh EventSource per remaining entry. EventSources resume
//     mid-stream via the standard `Last-Event-ID` header that the
//     server-side primitive (Issue 86 / routers/tasks.py) already honors.
//   - On terminal events (`done` / `error`), the entry is removed from
//     localStorage and the EventSource closed.
//   - Pub-sub: callers (page-specific UI + the global activity panel)
//     subscribe to state changes via `subscribe(fn)` and get notified
//     whenever the active set changes.
//
// Entry shape:
//   {
//     task_id: string,         // SSE stream key (video_id / clip_id /
//                              //   Celery task id depending on surface)
//     kind: string,            // "dna_build" | "catalog_sync" |
//                              //   "improvement_brief" | "upload_pipeline" |
//                              //   "render"
//     label: string,           // human-readable, e.g. "DNA build"
//     stream_url: string,      // /tasks/{task_id}/events
//     started_at: number,      // Date.now() at registration
//     last_event_id: string,   // updated on every received event so a
//                              //   page-navigation mid-stream resumes
//                              //   from the right cursor
//     last_text: string        // buffered terminal-style display text
//   }
//
// Why localStorage (not sessionStorage / SharedWorker / BroadcastChannel):
//   - sessionStorage clears on tab close → loses the resume.
//   - SharedWorker would let multiple browser-tabs share one EventSource
//     but adds complexity for a need we don't have yet (≤3 concurrent
//     streams per creator per aacquire_slot cap).
//   - BroadcastChannel could coordinate UI across tabs — additive future
//     work, not the user's stated need ("page-to-page in one tab").

(function (global) {
  'use strict';

  var STORAGE_KEY = 'creatorclip:active_tasks';
  var STALE_AFTER_MS = 60 * 60 * 1000; // 1 hour — matches server-side stream TTL
  var connections = {}; // task_id → EventSource
  var subscribers = []; // callbacks invoked on state changes

  function readStorage() {
    try {
      var raw = global.localStorage.getItem(STORAGE_KEY);
      if (!raw) return [];
      var parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch (e) {
      // localStorage disabled (private mode) or corrupt JSON → treat as empty.
      return [];
    }
  }

  function writeStorage(tasks) {
    try {
      global.localStorage.setItem(STORAGE_KEY, JSON.stringify(tasks));
    } catch (e) {
      // Quota exceeded or disabled — non-fatal. The library still works for
      // this page; just won't survive navigation.
    }
  }

  function pruneStale(tasks) {
    var now = Date.now();
    return tasks.filter(function (t) {
      return (now - (t.started_at || 0)) < STALE_AFTER_MS;
    });
  }

  function notify() {
    var snapshot = getActiveTasks();
    subscribers.forEach(function (fn) {
      try { fn(snapshot); } catch (e) { /* subscriber bug shouldn't break others */ }
    });
  }

  function getActiveTasks() {
    return readStorage().slice(); // defensive copy
  }

  function findTask(task_id) {
    return readStorage().filter(function (t) { return t.task_id === task_id; })[0] || null;
  }

  function updateTaskField(task_id, patch) {
    var tasks = readStorage();
    var idx = tasks.findIndex(function (t) { return t.task_id === task_id; });
    if (idx < 0) return;
    tasks[idx] = Object.assign({}, tasks[idx], patch);
    writeStorage(tasks);
  }

  function removeTask(task_id) {
    var tasks = readStorage().filter(function (t) { return t.task_id !== task_id; });
    writeStorage(tasks);
    var es = connections[task_id];
    if (es) {
      try { es.close(); } catch (e) { /* ignore */ }
      delete connections[task_id];
    }
    notify();
  }

  // Renderer reused from progressStream.js's event types. Kept inline so
  // activeTasks.js has no script-order dependency on progressStream.js.
  function renderLine(type, data) {
    if (type === 'step') {
      return '→ ' + (data.label || 'step') + (data.detail ? ': ' + data.detail : '');
    }
    if (type === 'cache') {
      var hit = data.cache_read > 0;
      return '· cache ' + (hit ? 'HIT' : 'miss') + ' (' + (data.input_tokens || 0) + ' input tok)';
    }
    if (type === 'thinking' || type === 'token') {
      return data.chunk || '';
    }
    if (type === 'error') {
      return '! ' + (data.message || 'unknown error');
    }
    if (type === 'done') {
      var ver = (data.version != null) ? ' v' + data.version : '';
      return '✓ done' + ver;
    }
    return '';
  }

  var INLINE = { thinking: true, token: true };

  function openConnection(task) {
    if (connections[task.task_id]) return; // already connected
    var es;
    try {
      es = new global.EventSource(task.stream_url, { withCredentials: true });
    } catch (e) {
      // EventSource not supported or URL malformed → silently skip.
      return;
    }
    connections[task.task_id] = es;

    var buffer = task.last_text || '';

    function dispatch(type, evt) {
      var data;
      try { data = JSON.parse(evt.data); } catch (e) { data = { _raw: evt.data }; }

      var line = renderLine(type, data);
      if (INLINE[type]) {
        buffer += line;
      } else {
        buffer = buffer ? buffer + '\n' + line : line;
      }

      // Persist progress so a page navigation resumes from here.
      var patch = { last_text: buffer };
      if (evt.lastEventId) patch.last_event_id = evt.lastEventId;
      updateTaskField(task.task_id, patch);
      notify();

      if (type === 'done' || type === 'error') {
        // Terminal — server closed the stream; remove from active set.
        removeTask(task.task_id);
      }
    }

    ['step', 'cache', 'thinking', 'token', 'error', 'done'].forEach(function (t) {
      es.addEventListener(t, function (e) { dispatch(t, e); });
    });
    es.onmessage = function (e) { dispatch('message', e); };
    es.onerror = function () { /* EventSource auto-reconnects; no action */ };
  }

  function registerTask(task) {
    if (!task || !task.task_id || !task.stream_url) return;
    var tasks = pruneStale(readStorage());
    if (tasks.find(function (t) { return t.task_id === task.task_id; })) {
      // Already registered (e.g. debounce returning the same task) — re-open
      // the connection if it's not live, but don't duplicate the entry.
      openConnection(task);
      return;
    }
    var entry = Object.assign(
      { started_at: Date.now(), last_event_id: '', last_text: '' },
      task
    );
    tasks.push(entry);
    writeStorage(tasks);
    openConnection(entry);
    notify();
  }

  function subscribe(callback) {
    if (typeof callback !== 'function') return function () {};
    subscribers.push(callback);
    // Fire once immediately with current state so the subscriber renders
    // before any new events arrive.
    try { callback(getActiveTasks()); } catch (e) { /* ignore */ }
    return function unsubscribe() {
      var idx = subscribers.indexOf(callback);
      if (idx >= 0) subscribers.splice(idx, 1);
    };
  }

  // On script load (every page mount), prune stale entries and resume
  // connections for everything still in localStorage.
  function bootstrap() {
    var tasks = pruneStale(readStorage());
    writeStorage(tasks);
    tasks.forEach(function (t) { openConnection(t); });
    notify();
  }

  // Public API.
  global.activeTasks = {
    registerTask: registerTask,
    getActiveTasks: getActiveTasks,
    findTask: findTask,
    subscribe: subscribe,
    removeTask: removeTask,        // exposed for manual cleanup (e.g. user dismisses)
    _bootstrap: bootstrap,         // exposed for tests; bootstrap() runs automatically below
  };

  // Auto-bootstrap on script load (deferred attribute on the <script> tag
  // ensures this runs after DOM parsed; no DOMContentLoaded needed).
  bootstrap();
})(typeof window !== 'undefined' ? window : globalThis);
