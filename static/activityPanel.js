// Global activity panel (Wave 5 — Issue: frontend visibility for backend work).
//
// Floating bottom-right widget that surfaces every active background task
// regardless of which page the user is on. Reacts to activeTasks.subscribe().
//
// Collapsed:  ⚡ N running   (click to expand)
// Expanded:   per-task terminal-style stream + close button to collapse
// Hidden:     when no active tasks
//
// Pattern: Linear / Vercel / Notion-style activity tray. One panel instance
// across all pages (the script idempotently attaches to <body>; reloading
// the same page never duplicates the panel).
//
// Depends on /static/activeTasks.js being loaded first.

(function (global) {
  'use strict';

  if (!global.activeTasks) {
    // activeTasks.js wasn't loaded — log and exit. Don't break the page.
    if (global.console && global.console.warn) {
      global.console.warn('activityPanel.js: window.activeTasks missing; panel disabled.');
    }
    return;
  }

  var PANEL_ID = 'cc-activity-panel';

  // If the panel is already attached (e.g. script loaded twice via duplicate
  // <script> tags), don't duplicate.
  if (global.document && global.document.getElementById(PANEL_ID)) return;

  function injectStyles() {
    if (global.document.getElementById('cc-activity-panel-styles')) return;
    var style = global.document.createElement('style');
    style.id = 'cc-activity-panel-styles';
    style.textContent = [
      '#cc-activity-panel {',
      '  position: fixed; bottom: 18px; right: 18px; z-index: 9999;',
      '  font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, sans-serif;',
      '  font-size: 0.8rem; color: #e0e0e0;',
      '}',
      '#cc-activity-panel.cc-hidden { display: none; }',
      '#cc-activity-panel .cc-badge {',
      '  background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 999px;',
      '  padding: 7px 14px; cursor: pointer; user-select: none;',
      '  box-shadow: 0 2px 12px rgba(0,0,0,0.4); display: flex; align-items: center; gap: 8px;',
      '}',
      '#cc-activity-panel .cc-badge:hover { background: #232323; }',
      '#cc-activity-panel .cc-pulse {',
      '  display: inline-block; width: 8px; height: 8px; border-radius: 50%;',
      '  background: #6c63ff; animation: cc-pulse 1.4s ease-in-out infinite;',
      '}',
      '@keyframes cc-pulse {',
      '  0%, 100% { opacity: 0.4; transform: scale(0.9); }',
      '  50% { opacity: 1; transform: scale(1.15); }',
      '}',
      '#cc-activity-panel .cc-tray {',
      '  display: none; background: #141414; border: 1px solid #2a2a2a; border-radius: 8px;',
      '  width: 340px; max-height: 60vh; overflow-y: auto;',
      '  box-shadow: 0 4px 18px rgba(0,0,0,0.5);',
      '}',
      '#cc-activity-panel.cc-open .cc-tray { display: block; }',
      '#cc-activity-panel.cc-open .cc-badge { border-bottom-left-radius: 0; border-bottom-right-radius: 0; }',
      '#cc-activity-panel .cc-tray-header {',
      '  display: flex; justify-content: space-between; align-items: center;',
      '  padding: 10px 12px; border-bottom: 1px solid #232323;',
      '  font-weight: 600; font-size: 0.75rem; color: #aaa;',
      '  letter-spacing: 0.06em; text-transform: uppercase;',
      '}',
      '#cc-activity-panel .cc-tray-header button {',
      '  background: none; border: none; color: #888; cursor: pointer; font-size: 1rem;',
      '}',
      '#cc-activity-panel .cc-task {',
      '  padding: 10px 12px; border-bottom: 1px solid #1f1f1f;',
      '}',
      '#cc-activity-panel .cc-task:last-child { border-bottom: none; }',
      '#cc-activity-panel .cc-task-label {',
      '  font-weight: 600; color: #e0e0e0; margin-bottom: 6px; font-size: 0.8rem;',
      '}',
      '#cc-activity-panel .cc-task-stream {',
      '  background: #0a0a0a; border-radius: 4px; padding: 8px 10px;',
      '  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;',
      '  font-size: 0.7rem; color: #c8c8c8; line-height: 1.5;',
      '  white-space: pre-wrap; word-break: break-word;',
      '  max-height: 140px; overflow-y: auto;',
      '}',
    ].join('\n');
    global.document.head.appendChild(style);
  }

  function buildPanel() {
    injectStyles();
    var panel = global.document.createElement('div');
    panel.id = PANEL_ID;
    panel.className = 'cc-hidden';
    panel.innerHTML = [
      '<div class="cc-badge" data-toggle="1">',
      '  <span class="cc-pulse"></span>',
      '  <span class="cc-count">0 running</span>',
      '</div>',
      '<div class="cc-tray">',
      '  <div class="cc-tray-header">',
      '    <span>Active work</span>',
      '    <button data-close="1" title="Collapse">−</button>',
      '  </div>',
      '  <div class="cc-tasks"></div>',
      '</div>',
    ].join('');
    global.document.body.appendChild(panel);

    // Toggle expanded state on badge click.
    panel.querySelector('[data-toggle]').addEventListener('click', function () {
      panel.classList.toggle('cc-open');
    });
    panel.querySelector('[data-close]').addEventListener('click', function () {
      panel.classList.remove('cc-open');
    });

    return panel;
  }

  function labelFor(task) {
    if (task.label) return task.label;
    var kindLabels = {
      dna_build: 'Building your Creator DNA',
      catalog_sync: 'Syncing channel catalog',
      improvement_brief: 'Generating improvement brief',
      upload_pipeline: 'Processing upload',
      render: 'Rendering clip',
    };
    return kindLabels[task.kind] || (task.kind || 'Background task');
  }

  function render(panel, tasks) {
    var count = tasks.length;
    if (count === 0) {
      panel.classList.add('cc-hidden');
      panel.classList.remove('cc-open');
      return;
    }
    panel.classList.remove('cc-hidden');
    panel.querySelector('.cc-count').textContent = count + ' running';

    var tasksEl = panel.querySelector('.cc-tasks');
    tasksEl.innerHTML = tasks.map(function (t) {
      var safe = function (s) {
        return (s == null ? '' : String(s))
          .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      };
      return [
        '<div class="cc-task" data-task-id="', safe(t.task_id), '">',
        '  <div class="cc-task-label">', safe(labelFor(t)), '</div>',
        '  <pre class="cc-task-stream">', safe(t.last_text || '…'), '</pre>',
        '</div>',
      ].join('');
    }).join('');

    // Auto-scroll each stream to bottom so the latest events are visible.
    Array.prototype.forEach.call(panel.querySelectorAll('.cc-task-stream'), function (el) {
      el.scrollTop = el.scrollHeight;
    });
  }

  function mount() {
    var panel = buildPanel();
    global.activeTasks.subscribe(function (tasks) {
      render(panel, tasks);
    });
  }

  if (global.document.readyState === 'loading') {
    global.document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})(typeof window !== 'undefined' ? window : globalThis);
