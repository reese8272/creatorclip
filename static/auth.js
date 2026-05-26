// Shared auth guard — included in every CreatorClip page.
// Fetches /auth/me; redirects to /auth/login on 401.
// Sets window.__USER__ and dispatches 'auth:ready' on document.
(async function () {
  const resp = await fetch('/auth/me', { credentials: 'include' });
  if (!resp.ok) {
    window.location = '/auth/login';
    return;
  }
  window.__USER__ = await resp.json();
  document.dispatchEvent(new CustomEvent('auth:ready', { detail: window.__USER__ }));
})();

function logout() {
  fetch('/auth/logout', { method: 'POST', credentials: 'include' })
    .then(() => { window.location = '/auth/login'; });
}
