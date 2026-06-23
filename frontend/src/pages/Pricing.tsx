import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, ApiError } from '@/lib/api'
import { useAuth } from '@/hooks/useAuth'
import { DisclaimerBand } from '@/components/DisclaimerBand'

// Port of static/pricing.html. Public-or-authed: anonymous visitors see the
// price grid (rendered under <AppChrome> without the auth gate), authed creators
// see their balance + a working "Buy now". Minutes-pack model (no subscription).
interface Pack {
  id: string
  label: string
  minutes: number
  price_cents: number
  per_min: number
  featured: boolean
}

// Issue 209 — keep in sync with billing/packs.py ALL_PACKS (purchasable only).
// TODO: drive from /billing/packs API to eliminate DRY drift (follow-up issue).
const PACKS: Pack[] = [
  { id: 'starter', label: 'Starter', minutes: 200, price_cents: 1800, per_min: 0.09, featured: false },
  { id: 'regular', label: 'Regular', minutes: 500, price_cents: 4000, per_min: 0.08, featured: false },
  { id: 'creator', label: 'Creator', minutes: 1000, price_cents: 7000, per_min: 0.07, featured: true },
  { id: 'pro', label: 'Pro', minutes: 2000, price_cents: 11000, per_min: 0.055, featured: false },
  { id: 'studio', label: 'Studio', minutes: 5000, price_cents: 22500, per_min: 0.045, featured: false },
  // Issue 209 — Stream pack for long-form/multi-hour VOD creators (4.0 ¢/min < Studio 4.5 ¢/min)
  { id: 'stream', label: 'Stream', minutes: 10000, price_cents: 40000, per_min: 0.04, featured: false },
]

const formatPrice = (cents: number) => `$${(cents / 100).toFixed(2)}`

// Per-page-load idempotency UUID for Stripe Checkout (Issue 106): a double-click
// dedupes within Stripe's 24h window; a fresh page load is a fresh intent.
function checkoutIntentId(): string {
  const key = 'creatorclip_checkout_intent_id'
  let id = sessionStorage.getItem(key)
  if (!id) {
    id = crypto.randomUUID()
    sessionStorage.setItem(key, id)
  }
  return id
}

export function Pricing() {
  const { user, balance } = useAuth()
  const authed = user != null
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  // Initialise the toast from the post-checkout redirect param (read once at
  // mount) rather than setting state inside an effect.
  const [toast, setToast] = useState<string | null>(() =>
    params.get('success') === '1' ? 'Purchase complete — minutes added to your balance!' : null,
  )

  // One-time: strip the success/cancelled query params from the URL.
  useEffect(() => {
    if (params.get('success') || params.get('cancelled')) setParams({}, { replace: true })
  }, [params, setParams])

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 3000)
    return () => clearTimeout(t)
  }, [toast])

  async function buyPack(packId: string) {
    if (!authed) {
      navigate('/login')
      return
    }
    const origin = window.location.origin
    try {
      const { checkout_url } = await api<{ checkout_url: string }>('/billing/checkout', {
        method: 'POST',
        redirectOn401: false,
        body: {
          pack_id: packId,
          success_url: `${origin}/app/pricing?success=1`,
          cancel_url: `${origin}/app/pricing?cancelled=1`,
          intent_id: checkoutIntentId(),
        },
      })
      window.location.assign(checkout_url)
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) navigate('/login')
      else setToast('Could not start checkout. Try again shortly.')
    }
  }

  return (
    <>
      <DisclaimerBand>
        AutoClip predicts fit with your style and audience — it does not promise virality.
        Recommendations are estimates grounded in your own data, not guarantees.
      </DisclaimerBand>

      <main className="px-4 pb-6 pt-12 text-center">
        <h1 className="mb-2 text-h1 text-fg">Buy the minutes you need.</h1>
        <p className="mx-auto mb-8 max-w-xl text-sm text-muted">
          No subscription. No expiry. Pay once, use whenever — stash extras for next season.
        </p>
        {authed && balance && (
          <div className="mb-6 text-sm text-muted">
            Your balance:{' '}
            <strong className="font-mono font-semibold text-fg">
              {balance.minutes_balance.toLocaleString()}
            </strong>{' '}
            minutes remaining
          </div>
        )}
      </main>

      <section className="mx-auto mb-8 grid max-w-[1100px] grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-4 px-4">
        {PACKS.map((p) => (
          <div
            key={p.id}
            className={`relative flex flex-col gap-2 rounded-md border bg-surface p-5 shadow-inset transition-[background-color,border-color,box-shadow,transform] duration-base ease-standard hover:-translate-y-0.5 hover:shadow-md ${
              p.featured
                ? 'border-accent shadow-accent-glow'
                : 'border-default shadow-sm hover:border-strong'
            }`}
          >
            {p.featured && (
              <span className="absolute -top-px right-4 -translate-y-1/2 rounded-sm border border-accent bg-bg px-2 py-0.5 text-xs font-semibold uppercase tracking-wide text-accent-text">
                Most picked
              </span>
            )}
            <div className="text-sm font-semibold uppercase tracking-wide text-muted">{p.label}</div>
            <div className="font-mono text-xl font-semibold text-fg">
              {p.minutes.toLocaleString()}
              <span className="ml-1 font-sans text-xs font-normal text-subtle">min</span>
            </div>
            <div className="font-mono text-base font-medium text-fg">{formatPrice(p.price_cents)}</div>
            <div className="font-mono text-xs text-subtle">${p.per_min.toFixed(3)}/min</div>
            <div className="mt-auto pt-3 text-xs leading-relaxed text-subtle">
              Never expires. One source minute = one minute used.
            </div>
            <button
              onClick={() => buyPack(p.id)}
              className={
                authed
                  ? 'mt-4 w-full rounded-sm bg-accent py-3 text-sm font-medium text-on-accent shadow-inset transition-colors duration-fast hover:bg-accent-hover'
                  : 'mt-4 w-full rounded-sm border border-strong py-3 text-sm font-medium text-fg transition-colors duration-fast hover:border-accent hover:text-accent-text'
              }
            >
              {authed ? 'Buy now' : 'Sign in to buy'}
            </button>
          </div>
        ))}
      </section>

      <p className="mx-auto mb-4 max-w-2xl px-4 text-center text-xs leading-relaxed text-subtle">
        Minutes cover source video processing time (transcription + AI analysis + render). One
        minute of source video = one minute deducted.
      </p>

      <p className="mx-auto mb-10 max-w-2xl px-4 text-center text-xs leading-relaxed text-subtle">
        {/* Issue 208 — refund policy copy */}
        Refund policy: if you are unsatisfied with your purchase, contact us and we will review
        your request on a case-by-case basis. Minutes consumed before a refund request are not
        eligible for refund.
      </p>

      {toast && (
        <div className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2 animate-slide-up rounded-lg border border-strong bg-elevated px-5 py-3 text-sm text-fg shadow-lg">
          {toast}
        </div>
      )}
    </>
  )
}
