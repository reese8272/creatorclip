import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { Footer } from '@/components/Footer'

// Port of static/walkthrough.html — the first-run explainer (5 panels). Local
// only: no API calls until "Set up my AutoClip", which marks it seen and routes
// to onboarding (still static until Issue 85d). Gated to authed users via the
// protected (bare) layout route. Keyboard nav (arrows / Enter) is the canonical
// first-run UX.
interface Panel {
  step: string
  title: ReactNode
  body: ReactNode
}

const PANELS: Panel[] = [
  {
    step: '01 / what this is',
    title: (
      <>
        An AI editor that knows <em>your</em> channel.
      </>
    ),
    body: (
      <>
        <p className="mb-4 text-sm leading-relaxed text-muted">
          AutoClip looks at the videos you've already made. It studies what worked for{' '}
          <strong className="text-fg">your</strong> audience — not what's trending, not what the
          algorithm rewards, your own data.
        </p>
        <p className="text-sm leading-relaxed text-muted">
          Then it watches your future videos for moments that fit. It clips them, scores them
          against your DNA, and queues them for your review. You keep what you like and drop what
          you don't — the more you review, the sharper it gets.
        </p>
      </>
    ),
  },
  {
    step: '02 / your DNA',
    title: "Your DNA is your editor's brief.",
    body: (
      <>
        <p className="mb-4 text-sm leading-relaxed text-muted">
          We analyse your top &amp; bottom performers, your retention curves, and your
          audience-activity windows. We synthesise that into a short plain-language brief — your
          Creator DNA — that captures how your channel actually works.
        </p>
        <ul className="my-4 space-y-2">
          {[
            'The hooks your audience actually stays for',
            "Your optimal clip length (not the platform's, yours)",
            'The region of a long-form video your Shorts cuts hit hardest',
            'The upload windows your audience is online',
          ].map((t) => (
            <li key={t} className="relative pl-6 text-sm leading-relaxed text-fg">
              <span className="absolute left-0 font-mono text-accent">→</span>
              {t}
            </li>
          ))}
        </ul>
        <p className="text-sm leading-relaxed text-muted">
          You confirm it. You can edit it. It evolves as you do.
        </p>
      </>
    ),
  },
  {
    step: '03 / what a clip is',
    title: "A clip isn't just a moment. It's the setup AND the payoff.",
    body: (
      <>
        <p className="mb-4 text-sm leading-relaxed text-muted">
          Most AI clippers find a punchline and cut around it. We don't. We look BACKWARDS from the
          peak — 60 to 90 seconds — to find the <strong className="text-fg">setup</strong>. The
          viewer has to land in context, not in the middle of a punchline that means nothing without
          it.
        </p>
        <p className="text-sm leading-relaxed text-muted">
          Every clip we suggest comes with the principle behind why we picked it. You'll see it in
          the review queue. If we got it wrong, tell us — that's the loop that makes your DNA
          sharper.
        </p>
      </>
    ),
  },
  {
    step: '04 / what those badges mean',
    title: 'The dashboard speaks plainly.',
    body: (
      <>
        <p className="mb-4 text-sm leading-relaxed text-muted">
          You'll see your videos go through a few stages. Here's what they actually mean — no
          jargon:
        </p>
        <ul className="my-4 space-y-2">
          {[
            ['pending', "waiting in line; we'll start any second"],
            ['running', 'ingesting + transcribing + finding signals (~2–5 min on a 20-min video)'],
            ['done', 'clips are scored; the "Generate clips" button is your next move'],
            ['failed', 'something broke; your minutes are automatically refunded'],
          ].map(([k, v]) => (
            <li key={k} className="relative pl-6 text-sm leading-relaxed text-fg">
              <span className="absolute left-0 font-mono text-accent">→</span>
              <strong className="text-fg">{k}</strong> — {v}
            </li>
          ))}
        </ul>
        <p className="text-sm leading-relaxed text-muted">
          When something's in flight, your <strong className="text-fg">dashboard</strong> shows its
          status and refreshes on its own — you don't have to sit and watch. Come back any time and
          the latest state is waiting for you.
        </p>
      </>
    ),
  },
  {
    step: '05 / tell us about you',
    title: 'One last step. Then we get to work.',
    body: (
      <>
        <p className="mb-4 text-sm leading-relaxed text-muted">
          Your DNA tells us what your videos have done. It can't tell us what you're{' '}
          <strong className="text-fg">trying</strong> to build.
        </p>
        <p className="text-sm leading-relaxed text-muted">
          Tell us your niche, who your audience is, what you'll never do. It's 45 seconds. It's the
          difference between "this clip got views" and "this clip is on-brand for what you're
          building."
        </p>
        <p className="mt-6 text-xs leading-relaxed text-subtle">
          We'll also fuse your stated identity with the inferred DNA at clip-scoring time — so we
          honour your hard-nos even when an old video accidentally performed well doing something
          you've moved past.
        </p>
      </>
    ),
  },
]

function markWalkthroughSeen() {
  try {
    localStorage.setItem('creatorclip:walkthrough_seen', '1')
  } catch {
    /* private-mode browsers can still proceed */
  }
}

export function Walkthrough() {
  const navigate = useNavigate()
  const [current, setCurrent] = useState(1)
  const isLast = current === PANELS.length

  // Mark seen, then hand off WITHIN the SPA to the ported onboarding route
  // (Issue 154 — previously did a full-page exit to the dead /static/onboarding.html).
  const finish = useCallback(() => {
    markWalkthroughSeen()
    navigate('/onboarding')
  }, [navigate])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight') setCurrent((c) => Math.min(c + 1, PANELS.length))
      else if (e.key === 'ArrowLeft') setCurrent((c) => Math.max(c - 1, 1))
      else if (e.key === 'Enter' && current === PANELS.length) finish()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [current, finish])

  const panel = PANELS[current - 1]

  return (
    <div className="flex min-h-screen flex-col items-center bg-bg px-4 py-12">
      <div className="w-full max-w-xl">
        <div className="mb-10 flex justify-center gap-2">
          {PANELS.map((_, i) => (
            <span
              key={i}
              className={`h-0.5 w-6 transition-colors ${
                i + 1 < current ? 'bg-muted' : i + 1 === current ? 'bg-accent' : 'bg-strong'
              }`}
            />
          ))}
        </div>

        <div className="rounded-lg border border-default bg-surface px-8 py-10">
          <div className="mb-3 font-mono text-xs uppercase tracking-wide text-subtle">
            {panel.step}
          </div>
          <h1 className="mb-4 text-xl font-semibold tracking-tight text-fg">{panel.title}</h1>
          {panel.body}

          <div className="mt-8 flex items-center justify-between">
            {current > 1 ? (
              <button
                onClick={() => setCurrent((c) => c - 1)}
                className="text-xs text-subtle hover:text-muted"
              >
                ← Back
              </button>
            ) : (
              <span />
            )}
            <span className="font-mono text-xs text-subtle">
              {current} of {PANELS.length}
            </span>
            {isLast ? (
              <button
                onClick={finish}
                className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-on-accent hover:bg-accent-hover"
              >
                Set up my AutoClip →
              </button>
            ) : (
              <button
                onClick={() => setCurrent((c) => c + 1)}
                className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-on-accent hover:bg-accent-hover"
              >
                Continue →
              </button>
            )}
          </div>
        </div>

        <p className="mx-auto mt-8 max-w-md text-center text-xs leading-relaxed text-subtle">
          AutoClip predicts fit with your style and audience — it does not promise virality.
          Recommendations are estimates grounded in your own data, not guarantees.
        </p>
      </div>

      {/* ToS/Privacy footer — bare first-run flow still carries the OAuth-verification
          links (Issue 153); this route sits outside AppChrome, so render it here. */}
      <div className="mt-auto w-full">
        <Footer />
      </div>
    </div>
  )
}
