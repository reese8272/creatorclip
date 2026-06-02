/**
 * Beta-testing activity tracker (Issue 122).
 * Fires a fire-and-forget POST to /api/activity for clicks, form submits,
 * and page navigations so tester sessions are captured in the persistent log.
 */
(function () {
  const PAGE = document.title || location.pathname;

  function send(event_type, target, extra) {
    fetch("/api/activity", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ page: PAGE, event_type, target, extra: extra || {} }),
      keepalive: true,
    }).catch(function () {});
  }

  // Capture clicks: buttons, links, and labelled inputs.
  document.addEventListener("click", function (e) {
    var el = e.target.closest("button, a, [data-log], input[type=submit], input[type=button]");
    if (!el) return;
    var label =
      el.dataset.log ||
      el.innerText?.trim().slice(0, 80) ||
      el.getAttribute("aria-label") ||
      el.id ||
      el.className ||
      el.tagName;
    send("click", label, { href: el.href || undefined });
  }, true);

  // Capture form submits.
  document.addEventListener("submit", function (e) {
    var form = e.target;
    send("submit", form.id || form.action || "form", {});
  }, true);

  // Log initial page load.
  send("navigate", PAGE, { referrer: document.referrer || undefined });

  // SPA navigation via History API.
  var _pushState = history.pushState.bind(history);
  history.pushState = function (state, title, url) {
    _pushState(state, title, url);
    send("navigate", String(url || location.href), {});
  };
  window.addEventListener("popstate", function () {
    send("navigate", location.href, {});
  });
})();
