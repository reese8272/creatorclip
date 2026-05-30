// Live progress stream consumer for long-running tasks (Issue 86).
//
// Subscribes to /tasks/{task_id}/events via EventSource and renders events
// into a terminal-style block. The wire format is plain JSON SSE with named
// event types (step / cache / thinking / token / error / done) — see
// docs/DECISIONS.md (Issue 86) for the format rationale.
//
// Usage:
//   const sub = subscribeToTaskStream('/tasks/abc/events', {
//     onRender: (text) => { document.getElementById('out').textContent = text; },
//     onDone:   (data) => { ... },
//     onError:  (msg)  => { ... },
//   });
//   // later: sub.close();
//
// EventSource auto-reconnects on disconnect (~3s) and sends Last-Event-ID for
// resume — the server replays missed events from that cursor.

(function (global) {
  'use strict';

  // How each event type renders into the terminal-style buffer. Step / cache /
  // error / done are line-oriented (one event = one line). Thinking / token
  // events are inline (each chunk appends to the current line).
  const RENDERERS = {
    step: function (d) {
      return '→ ' + (d.label || 'step') + (d.detail ? ': ' + d.detail : '');
    },
    cache: function (d) {
      const hit = d.cache_read > 0;
      return '· cache ' + (hit ? 'HIT' : 'miss') +
        ' (' + (d.input_tokens || 0) + ' input tok)';
    },
    thinking: function (d) {
      return d.chunk || '';
    },
    token: function (d) {
      return d.chunk || '';
    },
    error: function (d) {
      return '! ' + (d.message || 'unknown error');
    },
    done: function (d) {
      const ver = (d.version != null) ? ' v' + d.version : '';
      return '✓ done' + ver;
    },
  };

  // Inline events append to the current line (no newline prefix).
  const INLINE = { thinking: true, token: true };

  function subscribeToTaskStream(url, handlers) {
    handlers = handlers || {};
    const es = new EventSource(url, { withCredentials: true });
    let buffer = '';

    function dispatch(type, evt) {
      let data;
      try {
        data = JSON.parse(evt.data);
      } catch (e) {
        data = { _raw: evt.data };
      }

      if (handlers.onEvent) handlers.onEvent(type, data);

      const render = RENDERERS[type];
      if (render) {
        const line = render(data);
        if (INLINE[type]) {
          buffer += line;
        } else {
          buffer = buffer ? buffer + '\n' + line : line;
        }
        if (handlers.onRender) handlers.onRender(buffer);
      }

      if (type === 'done' && handlers.onDone) handlers.onDone(data);
      if (type === 'error' && handlers.onError) {
        handlers.onError(data.message || 'unknown error');
      }
      if (type === 'done' || type === 'error') {
        // Server already closed the stream after a terminal event; EventSource
        // would otherwise auto-reconnect indefinitely with no further data.
        es.close();
      }
    }

    Object.keys(RENDERERS).forEach(function (t) {
      es.addEventListener(t, function (e) { dispatch(t, e); });
    });

    // Fallback for any event type we don't know about (forward-compatibility
    // with new event types added server-side later).
    es.onmessage = function (e) { dispatch('message', e); };

    // Browser-level connection errors (network drop, server unreachable).
    // EventSource will retry on its own; surface the state to the caller in
    // case they want to update the UI.
    es.onerror = function () {
      if (handlers.onConnectionError) handlers.onConnectionError();
    };

    return {
      close: function () { es.close(); },
    };
  }

  global.subscribeToTaskStream = subscribeToTaskStream;
})(typeof window !== 'undefined' ? window : globalThis);
