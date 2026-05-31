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
(async function () {
  const resp = await fetch('/auth/me', { credentials: 'include' });
  if (!resp.ok) {
    window.location = '/auth/login';
    return;
  }
  const user = await resp.json();
  window.__USER__ = user;

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

  document.dispatchEvent(new CustomEvent('auth:ready', { detail: user }));
})();

function logout() {
  fetch('/auth/logout', { method: 'POST', credentials: 'include' })
    .then(() => { window.location = '/auth/login'; });
}
