// Pre-auth sign-in (port of static/login.html — the stated design north star).
// Bare public layout (no nav). "Sign in with Google" is a real navigation to the
// backend OAuth route, carrying any ?yt= hint forward so the dashboard auto-fill
// survives the OAuth round-trip (mirrors the pre-auth hero flow).
export function Login() {
  const yt = new URLSearchParams(window.location.search).get('yt')
  const signInHref = yt ? `/auth/login?yt=${encodeURIComponent(yt)}` : '/auth/login'

  return (
    <div className="flex min-h-screen flex-col bg-bg">
      <main className="flex flex-1 items-center justify-center px-6 py-12">
        <div className="w-full max-w-md rounded-xl border border-default bg-surface px-9 py-10 text-center">
          <div className="mb-1.5 text-xl font-semibold tracking-tight text-accent">AutoClip</div>
          <div className="mb-7 text-sm leading-relaxed text-muted">
            The only AI editor that truly knows your channel.
          </div>

          <h1 className="mb-2.5 text-2xl font-semibold tracking-tight text-fg">Sign in to continue</h1>
          <p className="mb-7 text-sm leading-relaxed text-muted">
            AutoClip learns your style from your own analytics and ranks clips against your
            channel's DNA — audience-fit over generic virality.
          </p>

          <a
            href={signInHref}
            className="inline-flex w-full items-center justify-center gap-2.5 rounded-md border border-fg bg-fg px-5 py-3 text-[15px] font-semibold text-bg transition-opacity hover:opacity-90"
          >
            <svg className="h-[18px] w-[18px] shrink-0" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg">
              <path fill="#4285F4" d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844a4.14 4.14 0 0 1-1.796 2.716v2.258h2.908c1.702-1.567 2.684-3.874 2.684-6.615z" />
              <path fill="#34A853" d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18z" />
              <path fill="#FBBC05" d="M3.964 10.71A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.042l3.007-2.332z" />
              <path fill="#EA4335" d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.958L3.964 7.29C4.672 5.163 6.656 3.58 9 3.58z" />
            </svg>
            Sign in with Google
          </a>

          <div className="mt-5 rounded-md border border-accent-border bg-accent-soft px-3.5 py-3 text-left text-xs leading-relaxed text-muted">
            AutoClip predicts fit with your style and audience — it does not promise virality. Every
            recommendation is an estimate grounded in your own data.
          </div>

          <div className="mt-5 text-xs leading-relaxed text-subtle">
            By signing in you agree to our{' '}
            <a href="/static/tos.html" className="text-muted underline hover:text-fg">Terms</a> and{' '}
            <a href="/static/privacy.html" className="text-muted underline hover:text-fg">
              Privacy Policy
            </a>
            . We comply with the YouTube API Services Terms of Service.
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
