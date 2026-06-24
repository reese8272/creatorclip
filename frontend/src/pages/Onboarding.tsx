import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api'
import { useAuth } from '@/hooks/useAuth'
import { useTaskStream } from '@/hooks/useTaskStream'
import { sendActivity } from '@/lib/activity'
import { Button } from '@/components/ui/button'
import { StepCard } from '@/components/onboarding/StepCard'
import { OnboardingIdentity } from '@/components/onboarding/OnboardingIdentity'
import { TaskStepper } from '@/components/TaskStepper'
import { Footer } from '@/components/Footer'
import type { DataGate, DnaResponse, IdentityResponse, TaskQueued } from '@/types'

async function logout() {
  await api('/auth/logout', { method: 'POST', redirectOn401: false }).catch(() => {})
  window.location.href = '/app/login'
}

function DataGateStatus({ gate }: { gate: DataGate | undefined }) {
  if (!gate) return <p className="mb-4 text-sm text-muted">Checking…</p>
  const mark = (ok: boolean) =>
    ok ? <span className="text-success">✓</span> : <span className="text-warning">•</span>
  const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`
  return (
    <p className="mb-4 text-sm text-muted">
      {mark(gate.long_form_ready)} {plural(gate.long_form_videos, 'long-form video')}
      <br />
      {mark(gate.shorts_ready)} {plural(gate.shorts, 'Short')}
      <br />
      {gate.ready ? (
        <span className="text-success">Ready to build your Creator DNA.</span>
      ) : (
        'Link more of your published videos to unlock DNA.'
      )}
    </p>
  )
}

// sessionStorage keys for re-attach-on-navigation survival (Issue 214). When the
// user navigates away and returns, we re-read the stream URL from sessionStorage
// so useTaskStream re-subscribes automatically via its existing URL-change effect.
const SS_CATALOG_URL = 'onboarding:catalogUrl'
const SS_DNA_URL = 'onboarding:dnaUrl'

export function Onboarding() {
  const { user } = useAuth()
  const queryClient = useQueryClient()

  // Initialise from sessionStorage so a back-navigate re-attaches to in-flight
  // or completed streams without requiring a new API call.
  const [catalogUrl, setCatalogUrl] = useState<string | null>(
    () => sessionStorage.getItem(SS_CATALOG_URL),
  )
  const [dnaUrl, setDnaUrl] = useState<string | null>(
    () => sessionStorage.getItem(SS_DNA_URL),
  )
  const [identitySaved, setIdentitySaved] = useState(false)
  const [buildError, setBuildError] = useState<string | null>(null)

  // Elapsed time tracking: record the moment streaming starts, tick each second.
  const [catalogElapsed, setCatalogElapsed] = useState(0)
  const [dnaElapsed, setDnaElapsed] = useState(0)
  const catalogStartRef = useRef<number | null>(null)
  const dnaStartRef = useRef<number | null>(null)

  const catalog = useTaskStream(catalogUrl)
  const dna = useTaskStream(dnaUrl)

  // Poll the data gate while a catalog sync is actively streaming; stop on
  // settle. The done-effect below does the final read once the worker finishes.
  const gateQuery = useQuery({
    queryKey: ['data-gate'],
    queryFn: () => api<DataGate>('/creators/me/data-gate'),
    refetchInterval: () => (catalog.status === 'streaming' ? 4000 : false),
  })
  const dnaQuery = useQuery({
    queryKey: ['dna'],
    queryFn: () =>
      api<DnaResponse>('/creators/me/dna').catch((e) => {
        if (e instanceof ApiError && e.status === 404) return { profile: null }
        throw e
      }),
  })
  const identityQuery = useQuery({
    queryKey: ['identity'],
    queryFn: () => api<IdentityResponse>('/creators/me/identity'),
  })

  useEffect(() => {
    if (catalog.status === 'done') queryClient.invalidateQueries({ queryKey: ['data-gate'] })
  }, [catalog.status, queryClient])
  useEffect(() => {
    if (dna.status === 'done') queryClient.invalidateQueries({ queryKey: ['dna'] })
  }, [dna.status, queryClient])

  // Elapsed-time tick for catalog stream.
  useEffect(() => {
    if (catalog.status !== 'streaming') return
    if (!catalogStartRef.current) catalogStartRef.current = Date.now()
    const id = setInterval(() => {
      setCatalogElapsed(Date.now() - (catalogStartRef.current ?? Date.now()))
    }, 1000)
    return () => clearInterval(id)
  }, [catalog.status])

  // Elapsed-time tick for DNA stream.
  useEffect(() => {
    if (dna.status !== 'streaming') return
    if (!dnaStartRef.current) dnaStartRef.current = Date.now()
    const id = setInterval(() => {
      setDnaElapsed(Date.now() - (dnaStartRef.current ?? Date.now()))
    }, 1000)
    return () => clearInterval(id)
  }, [dna.status])

  // Emit a step-view activity event each time a new step label arrives (Issue 235
  // funnel foundation). Fires on the count change, not the label itself, so we
  // avoid stale-closure issues — the parent only cares about progression, not
  // which step it was.
  const prevCatalogSteps = useRef(0)
  const prevDnaSteps = useRef(0)
  useEffect(() => {
    if (catalog.steps.length > prevCatalogSteps.current) {
      sendActivity('navigate', 'onboarding:catalog-step', {
        source: 'ui',
        step: catalog.steps.length,
        label: catalog.steps.at(-1),
      })
      prevCatalogSteps.current = catalog.steps.length
    }
  }, [catalog.steps])
  useEffect(() => {
    if (dna.steps.length > prevDnaSteps.current) {
      sendActivity('navigate', 'onboarding:dna-step', {
        source: 'ui',
        step: dna.steps.length,
        label: dna.steps.at(-1),
      })
      prevDnaSteps.current = dna.steps.length
    }
  }, [dna.steps])

  const identityExists = identitySaved || Boolean(identityQuery.data?.identity)
  const briefReady = Boolean(dnaQuery.data?.profile)

  async function syncCatalog() {
    setCatalogUrl(null)
    sessionStorage.removeItem(SS_CATALOG_URL)
    catalogStartRef.current = null
    setCatalogElapsed(0)
    try {
      const { stream_url } = await api<TaskQueued>('/creators/me/catalog/sync', { method: 'POST' })
      if (stream_url) {
        sessionStorage.setItem(SS_CATALOG_URL, stream_url)
        setCatalogUrl(stream_url)
      }
      queryClient.invalidateQueries({ queryKey: ['data-gate'] })
    } catch {
      /* gate poll still reflects the last good read */
    }
  }

  async function buildDna() {
    setBuildError(null)
    setDnaUrl(null)
    sessionStorage.removeItem(SS_DNA_URL)
    dnaStartRef.current = null
    setDnaElapsed(0)
    try {
      const { stream_url } = await api<TaskQueued>('/creators/me/dna/build', { method: 'POST' })
      if (stream_url) {
        sessionStorage.setItem(SS_DNA_URL, stream_url)
        setDnaUrl(stream_url)
      }
    } catch (e) {
      setBuildError(
        e instanceof ApiError
          ? e.message
          : 'Could not start — make sure your channel data is ready (step 2).',
      )
    }
  }

  return (
    <div className="flex min-h-screen flex-col items-center">
      <nav className="flex w-full items-center gap-4 border-b border-default px-6 py-3">
        <a href="/" className="font-semibold tracking-tight text-fg">
          AutoClip
        </a>
        <span className="flex-1" />
        {/* Escape hatch: an already-connected creator can jump straight to the
            dashboard without finishing every setup step. Setup is resumable —
            the dashboard's DNA CTA and the per-step state both persist. */}
        {user && (
          <Link to="/dashboard" className="text-xs text-muted hover:text-fg">
            Skip to dashboard →
          </Link>
        )}
        <button onClick={logout} className="text-xs text-muted hover:text-fg">
          Logout
        </button>
      </nav>

      <main className="flex w-full max-w-lg flex-col gap-3 px-4 pb-6 pt-10">
        <h1 className="mt-6 text-center text-h1 text-fg">Set up AutoClip</h1>
        <p className="mb-2 text-center text-sm text-muted">
          Connect your channel once. We learn the rest from your data.
        </p>
        <p className="mb-2 rounded-md border border-default bg-surface px-4 py-2 text-center text-xs text-muted">
          AutoClip predicts fit with your style and audience — it does not promise virality.
        </p>

        <StepCard num={1} title="Connect your YouTube channel">
          <p className="mb-4 text-sm">
            {user ? (
              <span className="text-success">
                Connected as {user.channel_title || user.email || 'your channel'}
              </span>
            ) : (
              <span className="text-warning">Not connected</span>
            )}
          </p>
          {!user && (
            <a href="/auth/login">
              <Button className="w-full">Connect YouTube</Button>
            </a>
          )}
        </StepCard>

        <StepCard num={2} title="Channel data">
          <DataGateStatus gate={gateQuery.data} />
          <Button variant="secondary" className="w-full" onClick={syncCatalog}>
            Sync channel data
          </Button>
          <TaskStepper steps={catalog.steps} status={catalog.status} elapsedMs={catalogElapsed} />
        </StepCard>

        <StepCard num={3} title="Tell us about yourself" meta="(optional — 45 seconds)">
          <OnboardingIdentity onSaved={() => setIdentitySaved(true)} />
        </StepCard>

        <StepCard num={4} title="Build your Creator DNA">
          {/* Issue 204: intake is genuinely optional. DNA builds from your video
              data alone; identity only sharpens it (and any later stated-vs-inferred
              conflict is surfaced as a nudge, not enforced here). No step-3 gate. */}
          <p className="mb-4 text-sm text-muted">
            Analyses your top &amp; bottom performers to generate a personalised brief.
            {!identityExists && (
              <>
                {' '}
                <span className="text-fg">Optional: tell us about yourself in step 3 to sharpen it</span>{' '}
                — or build from your video data now.
              </>
            )}
          </p>
          <Button className="w-full" onClick={buildDna}>
            Build Creator DNA
          </Button>
          {buildError && <p className="mt-2 text-center text-xs text-warning">{buildError}</p>}
          {(dna.status === 'done' || briefReady) && (
            <p className="mt-2 text-center text-xs text-success">
              ✓ Your Creator Brief is ready — review &amp; confirm it in step 5 below.
            </p>
          )}
          {dna.status === 'error' && dna.error && (
            <p className="mt-2 text-center text-xs text-warning">{dna.error}</p>
          )}
          <TaskStepper steps={dna.steps} status={dna.status} elapsedMs={dnaElapsed} />
        </StepCard>

        <StepCard num={5} title="Review & confirm your brief">
          <p className="mb-4 text-sm text-muted">
            Check your Creator Brief, then confirm to activate personalised clip scoring.
          </p>
          <Link to="/profile">
            <Button variant="secondary" className="w-full">
              View &amp; confirm brief →
            </Button>
          </Link>
        </StepCard>
      </main>

      {/* ToS/Privacy footer — bare first-run flow still carries the OAuth-verification
          links (Issue 153); this route sits outside AppChrome, so render it here. */}
      <div className="mt-auto w-full">
        <Footer />
      </div>
    </div>
  )
}
