/**
 * static/editor.js — Transcript-based video editor (Issue 135).
 *
 * Selection model: native `window.getSelection()` snapped to the
 * enclosing word `<span data-start data-end data-index>` on `mouseup`.
 * This gives Shift+Arrow keyboard selection for free (WAI-ARIA-aligned).
 *
 * State model:
 *   - Cut queue: array of {start_s, end_s, indices: [int, int]} in
 *     localStorage["clip:{id}:cuts"]. Survives page reload.
 *   - One-level undo of the last cut add or remove.
 *
 * Render flow: batch-on-confirm (per Issue 135 spec — live re-render
 * would burn ~20s of ffmpeg per word delete). On confirm POSTs to
 * `/clips/{id}/cuts` and polls for `cleaned_render_uri` to surface the
 * edited video; final swap goes through the existing
 * `/clips/{id}/clean/confirm` endpoint shared with Issue 134.
 *
 * The editor instance is owned by review.html — call
 * `window.transcriptEditor.mount(clip)` when a clip loads, and
 * `window.transcriptEditor.unmount()` when the user advances.
 */

(function () {
  'use strict';

  // Soft warning band at 40% removed; hard caps live server-side.
  const WARNING_REMOVED_PCT = 40.0;
  // Max kept-region merge tolerance for adjacent click-cuts.
  const ADJACENT_MERGE_S = 0.05;

  let _state = null;
  let _undo = null; // {prevCuts: [...]} | null

  function _storageKey(clipId) {
    return `clip:${clipId}:cuts`;
  }

  function _loadCuts(clipId) {
    try {
      const raw = localStorage.getItem(_storageKey(clipId));
      const parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }

  function _saveCuts(clipId, cuts) {
    try {
      localStorage.setItem(_storageKey(clipId), JSON.stringify(cuts));
    } catch {
      // Storage quota exceeded — silently drop; the worst case is the user
      // loses cuts on refresh, which is recoverable.
    }
  }

  function _clearCuts(clipId) {
    try {
      localStorage.removeItem(_storageKey(clipId));
    } catch {}
  }

  /**
   * Render the transcript word array into `_state.wordsEl`. Each word becomes
   * a `<span class="ed-word" data-start data-end data-index>word</span>` with
   * a literal text-node space between spans (NOT inside them — that breaks
   * `getSelection()` boundary snapping).
   */
  function _renderWords() {
    const wordsEl = _state.wordsEl;
    wordsEl.textContent = '';
    _state.words.forEach((w, i) => {
      if (i > 0) wordsEl.appendChild(document.createTextNode(' '));
      const span = document.createElement('span');
      span.className = 'ed-word';
      span.dataset.start = w.start_s;
      span.dataset.end = w.end_s;
      span.dataset.index = String(i);
      span.textContent = w.word;
      wordsEl.appendChild(span);
    });
    _applyCutStyling();
  }

  /**
   * Walk the current cut queue and apply strikethrough + faded opacity to
   * every word inside any cut range. Also re-renders the cut queue panel.
   */
  function _applyCutStyling() {
    document.querySelectorAll('#ed-words .ed-word').forEach(el => {
      el.classList.remove('ed-cut');
    });
    _state.cuts.forEach((c, cutIdx) => {
      for (let i = c.indices[0]; i <= c.indices[1]; i++) {
        const span = _state.wordsEl.querySelector(`.ed-word[data-index="${i}"]`);
        if (span) {
          span.classList.add('ed-cut');
          span.dataset.cutIdx = String(cutIdx);
        }
      }
    });
    _renderCutQueue();
    _renderSummary();
  }

  function _renderCutQueue() {
    const queueEl = _state.queueEl;
    queueEl.textContent = '';
    if (!_state.cuts.length) {
      queueEl.textContent = 'No pending cuts. Drag-select words to mark them for removal.';
      queueEl.style.fontStyle = 'italic';
      queueEl.style.color = 'var(--color-text-subtle)';
      return;
    }
    queueEl.style.fontStyle = 'normal';
    queueEl.style.color = '';
    _state.cuts.forEach((c, idx) => {
      const row = document.createElement('div');
      row.className = 'ed-cut-row';
      const dur = (c.end_s - c.start_s).toFixed(2);
      const preview = _state.words
        .slice(c.indices[0], c.indices[1] + 1)
        .map(w => w.word)
        .join(' ')
        .slice(0, 60);
      row.innerHTML = `<span style="text-decoration:line-through;color:var(--color-text-subtle)">${_escape(preview)}</span> <small>· ${dur}s</small>`;
      const btn = document.createElement('button');
      btn.className = 'ed-remove-btn';
      btn.setAttribute('aria-label', 'Remove cut');
      btn.textContent = '×';
      btn.onclick = () => _removeCut(idx);
      row.appendChild(btn);
      queueEl.appendChild(row);
    });
  }

  function _renderSummary() {
    const removed = _state.cuts.reduce((acc, c) => acc + (c.end_s - c.start_s), 0);
    const pct = _state.clipDurationS > 0 ? (100 * removed / _state.clipDurationS) : 0;
    const summary = `${_state.cuts.length} cut(s) · would remove ${removed.toFixed(2)}s (${pct.toFixed(0)}%)`;
    _state.summaryEl.textContent = summary;
    if (pct >= WARNING_REMOVED_PCT) {
      _state.warnEl.textContent = `⚠ This removes ${pct.toFixed(0)}% of your clip.`;
      _state.warnEl.style.display = 'block';
    } else {
      _state.warnEl.style.display = 'none';
    }
  }

  function _escape(s) {
    return s.replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  /**
   * Snap a Selection range to whole-word boundaries by walking up from the
   * range's startContainer + endContainer to the nearest `.ed-word` ancestor.
   * Returns `[startIdx, endIdx]` inclusive, or null when the selection isn't
   * inside the words container.
   */
  function _selectionToWordIndices(sel) {
    if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null;
    const range = sel.getRangeAt(0);
    const startSpan = _ancestorWord(range.startContainer);
    let endSpan = _ancestorWord(range.endContainer);
    if (!startSpan && !endSpan) return null;
    // If the selection ends in a whitespace text node between spans, walk
    // back to the preceding word.
    if (!endSpan && range.endContainer.previousSibling) {
      endSpan = _ancestorWord(range.endContainer.previousSibling);
    }
    if (!startSpan || !endSpan) return null;
    let i = parseInt(startSpan.dataset.index, 10);
    let j = parseInt(endSpan.dataset.index, 10);
    if (i > j) [i, j] = [j, i];
    return [i, j];
  }

  function _ancestorWord(node) {
    while (node && node !== document.body) {
      if (node.nodeType === 1 && node.classList && node.classList.contains('ed-word')) {
        return node;
      }
      node = node.parentNode;
    }
    return null;
  }

  function _onMouseUp() {
    const sel = window.getSelection();
    const range = _selectionToWordIndices(sel);
    if (!range) return;
    const [i, j] = range;
    // Drop selection that's wholly inside an existing cut — re-selecting an
    // already-cut span shouldn't queue a duplicate.
    if (_state.cuts.some(c => c.indices[0] <= i && c.indices[1] >= j)) {
      sel.removeAllRanges();
      return;
    }
    _addCut(i, j);
    sel.removeAllRanges();
  }

  function _addCut(i, j) {
    const start = _state.words[i].start_s;
    const end = _state.words[j].end_s;
    _undo = { prevCuts: JSON.parse(JSON.stringify(_state.cuts)) };
    _state.cuts.push({ start_s: start, end_s: end, indices: [i, j] });
    _state.cuts.sort((a, b) => a.start_s - b.start_s);
    _state.cuts = _mergeAdjacent(_state.cuts);
    _saveCuts(_state.clipId, _state.cuts);
    _applyCutStyling();
  }

  function _removeCut(idx) {
    _undo = { prevCuts: JSON.parse(JSON.stringify(_state.cuts)) };
    _state.cuts.splice(idx, 1);
    _saveCuts(_state.clipId, _state.cuts);
    _applyCutStyling();
  }

  function _undoLast() {
    if (!_undo) return;
    _state.cuts = _undo.prevCuts;
    _undo = null;
    _saveCuts(_state.clipId, _state.cuts);
    _applyCutStyling();
  }

  function _mergeAdjacent(cuts) {
    if (!cuts.length) return cuts;
    const out = [cuts[0]];
    for (let k = 1; k < cuts.length; k++) {
      const last = out[out.length - 1];
      const cur = cuts[k];
      if (cur.start_s <= last.end_s + ADJACENT_MERGE_S) {
        last.end_s = Math.max(last.end_s, cur.end_s);
        last.indices[1] = Math.max(last.indices[1], cur.indices[1]);
      } else {
        out.push(cur);
      }
    }
    return out;
  }

  async function _confirm() {
    if (!_state.cuts.length) {
      _state.statusEl.textContent = 'No cuts to apply.';
      return;
    }
    _state.statusEl.textContent = 'Submitting cuts…';
    const resp = await fetch(`/clips/${_state.clipId}/cuts`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        segments: _state.cuts.map(c => ({ start_s: c.start_s, end_s: c.end_s })),
      }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      const detail = err.detail;
      const msg = (detail && typeof detail === 'object' && detail.message) ? detail.message
                : (typeof detail === 'string') ? detail
                : 'Submit failed — try again.';
      _state.statusEl.textContent = msg;
      return;
    }
    const data = await resp.json();
    _state.statusEl.textContent = 'Editing your clip — come back in ~20s.';
    if (data.stream_url && window.activeTasks) {
      window.activeTasks.registerTask({
        taskId: data.task_id, streamUrl: data.stream_url,
        label: `Editing clip ${_state.clipId.slice(0, 8)}`,
      });
    }
    // Poll for cleaned_render_uri to appear. The clean and edit features share
    // the same cleaned_render_uri slot (Issue 135 D1) so the existing
    // /clean/confirm swap path applies on confirm.
    const start = Date.now();
    const poll = setInterval(async () => {
      if (Date.now() - start > 180000) {
        clearInterval(poll);
        _state.statusEl.textContent = 'Edit is taking longer than expected — refresh later.';
        return;
      }
      const r = await fetch(`/videos/${_state.videoId}/clips`, { credentials: 'include' });
      if (!r.ok) return;
      const d = await r.json();
      const fresh = (d.clips || []).find(c => c.id === _state.clipId);
      if (fresh && fresh.cleaned_render_uri) {
        clearInterval(poll);
        _state.cleanedUri = fresh.cleaned_render_uri;
        _state.statusEl.textContent = 'Edited version ready — preview below.';
        if (_state.previewEl) {
          _state.previewEl.src = fresh.cleaned_render_uri;
          _state.previewContainer.style.display = 'block';
        }
      }
    }, 3000);
  }

  async function _confirmFinal() {
    const resp = await fetch(`/clips/${_state.clipId}/clean/confirm`, {
      method: 'POST', credentials: 'include',
    });
    if (!resp.ok) {
      _state.statusEl.textContent = 'Swap failed — try again.';
      return;
    }
    const data = await resp.json();
    const player = document.getElementById('clip-player');
    if (player && data.render_uri) {
      player.src = data.render_uri;
      player.load();
    }
    _clearCuts(_state.clipId);
    _state.cuts = [];
    _state.cleanedUri = null;
    _applyCutStyling();
    if (_state.previewContainer) _state.previewContainer.style.display = 'none';
    _state.statusEl.textContent = data.status === 'swapped'
      ? 'Edited version is now the main render.'
      : 'No edited version to swap.';
  }

  function _discardEdit() {
    if (_state.previewContainer) _state.previewContainer.style.display = 'none';
    _state.cleanedUri = null;
    _state.statusEl.textContent = 'Keeping original render.';
  }

  function _discardCuts() {
    _undo = { prevCuts: JSON.parse(JSON.stringify(_state.cuts)) };
    _state.cuts = [];
    _clearCuts(_state.clipId);
    _applyCutStyling();
    _state.statusEl.textContent = 'Cleared all pending cuts.';
  }

  async function mount(clip) {
    if (!clip || !clip.id) return;
    const wordsEl = document.getElementById('ed-words');
    const queueEl = document.getElementById('ed-queue');
    const summaryEl = document.getElementById('ed-summary');
    const warnEl = document.getElementById('ed-warning');
    const statusEl = document.getElementById('ed-status');
    const previewContainer = document.getElementById('ed-preview-container');
    const previewEl = document.getElementById('ed-preview');
    if (!wordsEl) return; // panel not in DOM (older template)

    statusEl.textContent = 'Loading transcript…';
    const resp = await fetch(`/clips/${clip.id}/transcript`, { credentials: 'include' });
    if (!resp.ok) {
      statusEl.textContent = 'Failed to load transcript — try refreshing.';
      return;
    }
    const data = await resp.json();
    _state = {
      clipId: clip.id,
      videoId: clip.video_id,
      clipDurationS: data.clip_duration_s,
      words: data.words,
      cuts: _loadCuts(clip.id),
      cleanedUri: clip.cleaned_render_uri || null,
      wordsEl, queueEl, summaryEl, warnEl, statusEl,
      previewContainer, previewEl,
    };
    _renderWords();
    statusEl.textContent = '';
    wordsEl.addEventListener('mouseup', _onMouseUp);
    if (_state.cleanedUri && previewContainer && previewEl) {
      previewEl.src = _state.cleanedUri;
      previewContainer.style.display = 'block';
    }
  }

  function unmount() {
    if (_state && _state.wordsEl) {
      _state.wordsEl.removeEventListener('mouseup', _onMouseUp);
    }
    _state = null;
    _undo = null;
  }

  window.transcriptEditor = {
    mount, unmount,
    confirm: _confirm,
    confirmFinal: _confirmFinal,
    discardEdit: _discardEdit,
    discardCuts: _discardCuts,
    undo: _undoLast,
  };
})();
