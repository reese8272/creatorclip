/**
 * Shared client-side utilities. Loaded as a plain (non-module) script so the
 * exported helpers live on `window` and are available to inline page scripts.
 */
(function () {
  'use strict';

  // Canonical HTML escaper. Escapes the five characters that can break out of
  // HTML text OR a quoted attribute context — the apostrophe is mandatory
  // because several call sites interpolate into single-quoted attributes and
  // inline event handlers. Supersedes the per-page copies (profile.html,
  // editor.js) and the incomplete ones (analysis.html `_esc` was missing the
  // apostrophe; activityPanel.js `safe` was text-node only).
  function escapeHtml(s) {
    return (s == null ? '' : String(s)).replace(/[&<>"']/g, function (c) {
      return {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
      }[c];
    });
  }

  window.escapeHtml = escapeHtml;
})();
