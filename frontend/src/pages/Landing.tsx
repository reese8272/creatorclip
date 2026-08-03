import { DisclaimerBand } from '@/components/DisclaimerBand'
import { ArrowRight } from '@/components/ui/icon'
import { ICON_INLINE, ICON_SIZE } from '@/components/ui/iconSizes'

// Public marketing landing (Issue 376a). Mirrors static/landing.html — the
// page FastAPI actually serves at `/` (main.py:index), which works whether or
// not the SPA bundle is built, so both the CI pytest lane and prod render
// identical real content instead of an empty SPA shell awaiting hydration.
// This route (`/app/landing`, no AuthGate) is the in-app equivalent, reachable
// once the SPA is running — same copy, app design language.
//
// Google's OAuth verification requirements (support.google.com/cloud/answer/
// 13464321) require the homepage to accurately describe the app's
// functionality and link the privacy policy — both satisfied here.
//
// 376(b) (a no-signup live demo) was DESCOPED by owner decision 2026-07-30 —
// see docs/DECISIONS.md. No demo affordance is offered on this page.
export function Landing() {
  return (
    <div className="flex min-h-screen flex-col bg-bg">
      <nav className="mx-auto flex w-full max-w-3xl items-center justify-between px-6 py-5">
        <span className="text-base font-semibold text-fg">AutoClip</span>
        <div className="flex items-center gap-5 text-sm text-muted">
          <a href="/app/pricing" className="hover:text-fg">
            Pricing
          </a>
          <a href="/static/tos.html" className="hover:text-fg">
            Terms
          </a>
          <a href="/static/privacy.html" className="hover:text-fg">
            Privacy
          </a>
          <a href="/auth/login" className="hover:text-fg">
            Sign in
          </a>
        </div>
      </nav>

      <main className="flex-1">
        <section className="mx-auto max-w-2xl px-6 pb-8 pt-10 text-center">
          <h1 className="mb-4 text-h1 text-fg">
            The only AI editor that truly knows your channel.
          </h1>
          <p className="mx-auto mb-6 max-w-xl text-sm leading-relaxed text-muted">
            AutoClip learns your style from your own YouTube analytics, adapts as you evolve, and
            keeps you ahead of the algorithm — clips scored against your channel&apos;s own DNA,
            not a one-size-fits-all template.
          </p>
          <div className="flex flex-wrap justify-center gap-3">
            <a
              href="/auth/login"
              className="inline-flex items-center justify-center rounded-md border border-fg bg-fg px-5 py-3 text-body font-semibold text-bg transition-opacity hover:opacity-90"
            >
              Sign in with Google
            </a>
            <a
              href="/app/pricing"
              className="inline-flex items-center justify-center rounded-md border border-default px-5 py-3 text-body font-semibold text-fg hover:border-strong"
            >
              See pricing
            </a>
          </div>
        </section>

        <div className="mx-auto max-w-2xl px-6">
          <DisclaimerBand>
            AutoClip predicts fit with your style and audience — it does not promise virality.
            Every recommendation is an estimate grounded in your own data, not a guarantee.
          </DisclaimerBand>
        </div>

        <section className="mx-auto max-w-2xl border-t border-default px-6 py-8">
          <h2 className="mb-3 text-lg font-semibold text-fg">What AutoClip does</h2>
          <p className="mb-3 text-sm leading-relaxed text-muted">
            Connect your YouTube channel and AutoClip analyzes your own video and analytics
            history to build a working model of what your audience actually responds to — your
            channel&apos;s &quot;DNA.&quot; Every clip candidate it finds is scored against that
            model, not a one-size-fits-all score borrowed from someone else&apos;s channel.
          </p>
          <ul className="ml-5 list-disc space-y-2 text-sm leading-relaxed text-muted">
            <li>
              <strong className="text-fg">Setup, not aftermath.</strong> Clips are cut to start at
              the setup of a moment, not the punchline, so the context survives the cut.
            </li>
            <li>
              <strong className="text-fg">Named reasoning.</strong> Every score cites a specific,
              named principle, so the ranking is never a black box.
            </li>
            <li>
              <strong className="text-fg">Your channel&apos;s own signal.</strong> Ranking is
              grounded in your DNA and, once you&apos;ve given enough feedback, a preference model
              trained on your own upvotes and downvotes — never a generic benchmark.
            </li>
          </ul>
        </section>

        <section className="mx-auto max-w-2xl border-t border-default px-6 py-8">
          <h2 className="mb-3 text-lg font-semibold text-fg">
            Proof of lift: we measure what actually happened
          </h2>
          <p className="mb-3 text-sm leading-relaxed text-muted">
            Most AI clipping tools stop at generating the clip. AutoClip closes the loop: once you
            publish a clip through AutoClip, it tracks how that clip actually performed — views at
            the 48-hour and 7-day checkpoint, compared against the median of your own comparable
            published Shorts, not an industry benchmark. That outcome feeds back into how future
            clips are ranked for your channel specifically.
          </p>
          <p className="text-sm leading-relaxed text-muted">
            This is reported honestly: a clip needs enough comparable history before AutoClip will
            call a verdict, and a rate is only shown once there is enough judged data to state one
            with a confidence interval. Raw counts are shown before that threshold — never an
            invented trend.
          </p>
          {/* Issue 355: this is the page's headline differentiator and it used
              to point nowhere. The panel now has a stable anchor. */}
          <a
            href="/app/insights#proof-of-lift"
            className="mt-3 inline-block text-sm text-accent-text hover:underline"
          >
            See yours <ArrowRight className={`${ICON_SIZE.md} ${ICON_INLINE}`} aria-hidden="true" />
          </a>
        </section>

        <section className="mx-auto max-w-2xl border-t border-default px-6 py-8">
          <h2 className="mb-1 text-lg font-semibold text-fg">Pricing</h2>
          <p className="mb-4 text-sm text-muted">
            No subscription, no expiry. Buy minutes once, use them whenever.
          </p>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            {[
              { label: 'Starter', minutes: '200 min', price: '$18.00' },
              { label: 'Regular', minutes: '500 min', price: '$40.00' },
              { label: 'Creator', minutes: '1,000 min', price: '$70.00' },
              { label: 'Pro', minutes: '2,000 min', price: '$110.00' },
              { label: 'Studio', minutes: '5,000 min', price: '$225.00' },
              { label: 'Stream', minutes: '10,000 min', price: '$400.00' },
            ].map((p) => (
              <div key={p.label} className="rounded-md border border-default p-4 text-center">
                <div className="mb-1 text-xs uppercase tracking-wide text-muted">{p.label}</div>
                <div className="font-mono text-base text-fg">{p.minutes}</div>
                <div className="mt-1 font-mono text-sm text-muted">{p.price}</div>
              </div>
            ))}
          </div>
          <p className="mt-4 text-xs text-subtle">
            Minutes cover source-video processing time (transcription + AI analysis + render); one
            minute of source video is one minute deducted. Full details and a live balance at{' '}
            <a href="/app/pricing" className="underline hover:text-fg">
              /app/pricing
            </a>
            .
          </p>
        </section>
      </main>

      <footer className="flex items-center justify-center gap-5 px-6 py-6 text-xs text-subtle">
        <a href="/static/tos.html" className="hover:text-fg">
          Terms
        </a>
        <a href="/static/privacy.html" className="hover:text-fg">
          Privacy
        </a>
        <a href="/static/accessibility.html" className="hover:text-fg">
          Accessibility
        </a>
        <span>© AutoClip 2026</span>
      </footer>
    </div>
  )
}
