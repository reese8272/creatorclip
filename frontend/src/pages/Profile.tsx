import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { useAuth } from '@/hooks/useAuth'
import { DisclaimerBand } from '@/components/DisclaimerBand'
import { DnaCard } from '@/components/profile/DnaCard'
import { IdentitySection } from '@/components/profile/IdentitySection'
import { AnalyticsPanel } from '@/components/dashboard/AnalyticsPanel'
import { Button } from '@/components/ui/button'
import type {
  ClipCountsResponse,
  Identity,
  IdentityResponse,
  NicheOption,
  VideoListResponse,
} from '@/types'

// Sidebar Library stat row.
function StatRow({ label, value, top }: { label: string; value: string; top?: boolean }) {
  return (
    <div className={`flex justify-between py-2 text-small ${top ? 'border-t border-default' : ''}`}>
      <span className="text-muted">{label}</span>
      <span className="font-mono font-semibold text-fg">{value}</span>
    </div>
  )
}

// Profile (Issue 308): a read-only snapshot of the channel — Creator DNA + stated
// identity + saved work + library/analytics. The clip-production + account controls
// (brand kit, intake, publishing, API keys, account) moved to Settings.
export function Profile() {
  const { user } = useAuth()
  const [niches, setNiches] = useState<NicheOption[]>([])
  const [identity, setIdentity] = useState<Identity | null>(null)
  const [conflict, setConflict] = useState<string | null>(null)
  // Bumping this re-runs the load effect (e.g. after an identity save) without
  // setting state directly in the effect body.
  const [reloadToken, setReloadToken] = useState(0)

  useEffect(() => {
    api<{ options: NicheOption[] }>('/creators/niches')
      .then((d) => setNiches(d.options))
      .catch(() => setNiches([]))
    api<IdentityResponse>('/creators/me/identity')
      .then((d) => {
        setIdentity(d.identity)
        setConflict(d.conflict ?? null)
      })
      .catch(() => {})
  }, [reloadToken])

  // Library stats reuse the dashboard's cached queries (same query keys).
  const videosQuery = useQuery({
    queryKey: ['videos'],
    queryFn: () => api<VideoListResponse>('/videos'),
  })
  const videos = videosQuery.data?.videos ?? []
  const clipCountsQuery = useQuery({
    queryKey: ['clip-counts'],
    queryFn: () => api<ClipCountsResponse>('/videos/clips/counts'),
    enabled: videos.length > 0,
  })
  const clipsRendered = (clipCountsQuery.data?.counts ?? []).reduce((n, r) => n + r.rendered, 0)
  const channelName = user?.channel_title ?? user?.email ?? 'Your channel'

  return (
    <>
      <DisclaimerBand>
        A snapshot of your channel — your synced Creator DNA, identity, and saved work. Edit how clips
        are produced in <Link to="/settings" className="text-accent-text hover:underline">Settings</Link>.
      </DisclaimerBand>

      <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-8">
        <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="font-display text-h1 text-fg">{channelName}</h1>
            <p className="mt-1 text-small text-muted">Channel profile snapshot</p>
          </div>
          <Link to="/settings">
            <Button variant="secondary">⚙ Editing settings →</Button>
          </Link>
        </div>

        <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1fr)_280px]">
          {/* Main: DNA snapshot + identity + saved analyses */}
          <div className="flex flex-col gap-6">
            <DnaCard identityCreatedAt={identity?.created_at ?? null} />
            <IdentitySection
              key={identity?.version ?? 'new'}
              niches={niches}
              identity={identity}
              conflict={conflict}
              onSaved={() => setReloadToken((t) => t + 1)}
            />
            <div className="rounded-md border border-default bg-surface shadow-sm shadow-inset">
              <div className="flex items-center justify-between border-b border-default px-[18px] py-4">
                <span className="text-body font-semibold text-fg">Saved analyses</span>
                <Link to="/insights" className="text-small text-accent-text hover:underline">
                  View saved →
                </Link>
              </div>
              <p className="px-[18px] py-4 text-small text-subtle">
                Your bookmarked performer analyses and improvement briefs live on the Insights page.
              </p>
            </div>
          </div>

          {/* Sidebar: library + YouTube analytics */}
          <div className="flex flex-col gap-4">
            <div className="rounded-md border border-default bg-surface p-[18px] shadow-sm shadow-inset">
              <div className="mb-3 text-label uppercase tracking-[0.08em] text-muted">Library</div>
              <div className="flex flex-col">
                <StatRow label="Videos" value={String(videos.length)} />
                <StatRow label="Clips rendered" value={String(clipsRendered)} top />
                <StatRow label="Shorts published" value="—" top />
                <StatRow label="Clip ratings" value="—" top />
              </div>
            </div>
            <AnalyticsPanel variant="sidebar" />
          </div>
        </div>
      </main>
    </>
  )
}
