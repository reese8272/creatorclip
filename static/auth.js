// Shared auth guard — included in every AutoClip page.
// Fetches /auth/me; redirects to /auth/login on 401.
// Sets window.__USER__ and dispatches 'auth:ready' on document.
//
// Issue 100 — first-run gate: brand-new creators (onboarding_state =
// 'connected' AND walkthrough not seen) get redirected to the
// /static/walkthrough.html explainer before the dashboard. Subsequent
// visits skip it. The walkthrough is also skipped explicitly when the
// user is already on walkthrough.html or onboarding.html (to avoid a
// redirect loop from within those flows).
//
// Issue 136 — pre-auth hero gate: a page that opts in via
// `<body data-allow-anonymous>` shows its hero block on 401 instead of
// redirecting. The hero CTA forwards the YouTube URL via ?yt= query
// hint; post-login this helper picks it up and auto-fills the link form.
(async function () {
  const resp = await fetch('/auth/me', { credentials: 'include' });
  if (!resp.ok) {
    if (document.body && document.body.hasAttribute('data-allow-anonymous')) {
      // Issue 136 — show the hero rather than bouncing to login.
      document.body.classList.add('is-hero-mode');
      document.dispatchEvent(new CustomEvent('auth:anonymous'));
      return;
    }
    window.location = '/static/login.html';
    return;
  }
  const user = await resp.json();
  window.__USER__ = user;
  // 2026-06-08 — server-resolved next-step CTA so every page can render
  // guidance without re-deriving it from /data-gate + /dna + /videos.
  // Pages listen for `setup:ready` (or read window.__SETUP__ inside
  // auth:ready) and decide whether to show a step hint.
  if (user.setup) {
    window.__SETUP__ = user.setup;
    document.dispatchEvent(new CustomEvent('setup:ready', { detail: user.setup }));
  }
  // Issue 136 — if the user landed here with a ?yt= hint from the hero,
  // auto-fill the link-video input so the next click finishes the flow.
  try {
    const params = new URLSearchParams(window.location.search);
    const ytHint = params.get('yt');
    if (ytHint) {
      const tryFill = () => {
        const input = document.getElementById('yt-id-input');
        if (input) {
          input.value = ytHint;
          const details = input.closest('details');
          if (details) details.open = true;
        }
      };
      // Run after DOMContentLoaded since auth.js is loaded in <head>.
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', tryFill, { once: true });
      } else {
        tryFill();
      }
    }
  } catch (_) { /* harmless — yt hint is best-effort */ }

  // First-run gate (Issue 100). Only redirects when:
  //   - the creator's onboarding hasn't started (state = 'connected')
  //   - we haven't already shown them the walkthrough
  //   - they're not already on the walkthrough OR onboarding page
  // The localStorage flag is set in walkthrough.html on completion.
  const path = window.location.pathname || '';
  const onSetupSurface =
    path.includes('/static/walkthrough.html') ||
    path.includes('/static/onboarding.html');
  let walkthroughSeen = false;
  try { walkthroughSeen = localStorage.getItem('creatorclip:walkthrough_seen') === '1'; }
  catch (_) { /* private mode — treat as unseen, fall through */ }

  if (user.onboarding_state === 'connected' && !walkthroughSeen && !onSetupSurface) {
    window.location = '/static/walkthrough.html';
    return;
  }

  // Populate shared nav elements present on every authenticated page.
  // Each page can still override these in its own auth:ready handler.
  const navUser = document.getElementById('nav-user');
  if (navUser && !navUser.textContent) {
    navUser.textContent = user.channel_title || user.email || '';
  }
  // Issue 126 — balance fetch now ALSO carries trial + low-balance state.
  // Cache the full payload on window.__BALANCE__ so every page can read it
  // (dashboard banner, analysis pre-action warning, profile billing card)
  // without re-fetching. Dispatch `billing:ready` so listeners can render
  // without a polling loop.
  const navBalance = document.getElementById('nav-balance');
  fetch('/billing/balance', { credentials: 'include' })
    .then(r => r.ok ? r.json() : null)
    .then(d => {
      if (!d) return;
      window.__BALANCE__ = d;
      if (navBalance) {
        navBalance.textContent = `${d.minutes_balance} min`;
        // Light up the chip when the creator is below the threshold so the
        // amber state is visible on every authenticated page (Issue 126).
        navBalance.classList.toggle('is-low', !!d.low_balance);
      }
      document.dispatchEvent(new CustomEvent('billing:ready', { detail: d }));
    })
    .catch(() => {});

  document.dispatchEvent(new CustomEvent('auth:ready', { detail: user }));
})();

function logout() {
  fetch('/auth/logout', { method: 'POST', credentials: 'include' })
    .then(() => { window.location = '/static/login.html'; });
}
