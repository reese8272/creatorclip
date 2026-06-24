// Pre-auth sign-in (port of static/login.html — the stated design north star).
// Bare public layout (no nav). "Sign in with Google" is a real navigation to the
// backend OAuth route, carrying any ?yt= hint forward so the dashboard auto-fill
// survives the OAuth round-trip (mirrors the pre-auth hero flow).
//
// Issue 299: The OAuth CTA is disabled until the creator clicks the affirmative
// "I agree to the Terms and Privacy Policy" checkbox (unchecked by default).
// This replaces the passive "By signing in you agree…" sign-in wrap that the
// 2025 9th Circuit (Chabolla v. ClassPass) held NOT binding.  The checkbox
// being affirmative and gating the CTA is the minimal defensible clickwrap
// pattern per FTC/CFPB 2025 guidance and the Ninth Circuit standard.
//
// Issue 300: A second "I confirm I am 13 or older" checkbox is composed with the
// above.  Both must be checked before the CTA is active.  Age-neutral phrasing
// ("13 or older") is the FTC COPPA Rule (16 CFR Part 312, effective 2025-06-23)
// recommended pattern: a neutral affirmation, not a yes/no that nudges the answer.
import { useState } from 'react'

export function Login() {
  const params = new URLSearchParams(window.location.search)
  const yt = params.get('yt')
  // Set by the backend OAuth callback when token exchange or persistence fails,
  // instead of leaking a 500 mid-OAuth (see routers/auth.py callback hardening).
  const oauthFailed = params.get('error') === 'oauth_failed'
  const signInHref = yt ? `/auth/login?yt=${encodeURIComponent(yt)}` : '/auth/login'

  const [agreed, setAgreed] = useState(false)
  const [ageConfirmed, setAgeConfirmed] = useState(false)

  const canSignIn = agreed && ageConfirmed

  return (
    <div className="flex min-h-screen flex-col bg-bg">
      <main className="flex flex-1 items-center justify-center px-6 py-12">
        <div className="w-full max-w-md rounded-lg border border-default bg-surface px-9 py-10 text-center shadow-lg">
          <div className="mb-1.5 text-xl font-semibold tracking-tight text-accent-text">AutoClip</div>
          <div className="mb-7 text-sm leading-relaxed text-muted">
            The only AI editor that truly knows your channel.
          </div>

          {oauthFailed && (
            <div
              role="alert"
              className="mb-5 rounded-md border border-danger-border bg-danger-soft px-3.5 py-3 text-left text-xs leading-relaxed text-danger"
            >
              We couldn't complete sign-in with Google. This is usually temporary — please try
              again. If it keeps happening, contact support.
            </div>
          )}

          <h1 className="mb-2.5 text-h1 text-fg">Sign in to continue</h1>
          <p className="mb-7 text-sm leading-relaxed text-muted">
            AutoClip learns your style from your own analytics and ranks clips against your
            channel's DNA — audience-fit over generic virality.
          </p>

          {/* Issue 299 — Affirmative clickwrap checkbox (unchecked by default).
              Must be checked before the OAuth CTA becomes active.  This is the
              defensible consent artifact per Chabolla v. ClassPass (9th Cir. 2025)
              and GDPR Art. 7 recorded-consent requirement. */}
          <label className="mb-3 flex cursor-pointer items-start gap-3 rounded-md border border-default bg-surface px-3.5 py-3 text-left text-xs leading-relaxed text-muted">
            <input
              type="checkbox"
              checked={agreed}
              onChange={(e) => setAgreed(e.target.checked)}
              aria-label="I agree to the Terms of Service and Privacy Policy"
              className="mt-0.5 h-4 w-4 shrink-0 cursor-pointer accent-current"
            />
            <span>
              I agree to the{' '}
              <a
                href="/static/tos.html"
                target="_blank"
                rel="noopener noreferrer"
                className="text-fg underline hover:opacity-80"
                onClick={(e) => e.stopPropagation()}
              >
                Terms of Service
              </a>{' '}
              and{' '}
              <a
                href="/static/privacy.html"
                target="_blank"
                rel="noopener noreferrer"
                className="text-fg underline hover:opacity-80"
                onClick={(e) => e.stopPropagation()}
              >
                Privacy Policy
              </a>
              . We comply with the YouTube API Services Terms of Service.
            </span>
          </label>

          {/* Issue 300 — COPPA 13+ minimum-age attestation (unchecked by default).
              Age-neutral phrasing per FTC COPPA Rule (16 CFR Part 312, 2025-06-23):
              "I confirm I am 13 or older" avoids a leading yes/no question.
              Both this and the consent checkbox above must be checked before sign-in. */}
          <label className="mb-5 flex cursor-pointer items-start gap-3 rounded-md border border-default bg-surface px-3.5 py-3 text-left text-xs leading-relaxed text-muted">
            <input
              type="checkbox"
              checked={ageConfirmed}
              onChange={(e) => setAgeConfirmed(e.target.checked)}
              aria-label="I confirm I am 13 or older"
              className="mt-0.5 h-4 w-4 shrink-0 cursor-pointer accent-current"
            />
            <span>I confirm I am 13 or older.</span>
          </label>

          {canSignIn ? (
            <a
              href={signInHref}
              className="inline-flex w-full items-center justify-center gap-2.5 rounded-md border border-fg bg-fg px-5 py-3 text-[15px] font-semibold text-bg transition-opacity hover:opacity-90"
              aria-label="Sign in with Google"
            >
              <svg className="h-[18px] w-[18px] shrink-0" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                <path fill="#4285F4" d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844a4.14 4.14 0 0 1-1.796 2.716v2.258h2.908c1.702-1.567 2.684-3.874 2.684-6.615z" />
                <path fill="#34A853" d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18z" />
                <path fill="#FBBC05" d="M3.964 10.71A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.042l3.007-2.332z" />
                <path fill="#EA4335" d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.958L3.964 7.29C4.672 5.163 6.656 3.58 9 3.58z" />
              </svg>
              Sign in with Google
            </a>
          ) : (
            <button
              type="button"
              disabled
              aria-disabled="true"
              className="inline-flex w-full cursor-not-allowed items-center justify-center gap-2.5 rounded-md border border-fg bg-fg px-5 py-3 text-[15px] font-semibold text-bg opacity-40"
              aria-label="Sign in with Google (please agree to Terms and confirm your age first)"
            >
              <svg className="h-[18px] w-[18px] shrink-0" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                <path fill="#4285F4" d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844a4.14 4.14 0 0 1-1.796 2.716v2.258h2.908c1.702-1.567 2.684-3.874 2.684-6.615z" />
                <path fill="#34A853" d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18z" />
                <path fill="#FBBC05" d="M3.964 10.71A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.042l3.007-2.332z" />
                <path fill="#EA4335" d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.958L3.964 7.29C4.672 5.163 6.656 3.58 9 3.58z" />
              </svg>
              Sign in with Google
            </button>
          )}

          <div className="mt-5 rounded-md border border-accent-border bg-accent-soft px-3.5 py-3 text-left text-xs leading-relaxed text-muted">
            AutoClip predicts fit with your style and audience — it does not promise virality. Every
            recommendation is an estimate grounded in your own data.
          </div>
        </div>
      </main>

      <footer className="flex items-center justify-center gap-5 px-6 py-6 text-xs text-subtle">
        <a href="/static/tos.html" className="hover:text-fg">Terms</a>
        <a href="/static/privacy.html" className="hover:text-fg">Privacy</a>
        <span>© AutoClip 2026</span>
      </footer>
    </div>
  )
}
