import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { useAuth } from '@/hooks/useAuth'
import { DisclaimerBand } from '@/components/DisclaimerBand'
import { EmptyStatePrompt } from '@/components/EmptyStatePrompt'
import { DnaCard } from '@/components/profile/DnaCard'
import { YouTubeConnectionCard } from '@/components/profile/YouTubeConnectionCard'
import { AnalyticsPanel } from '@/components/dashboard/AnalyticsPanel'
import { Button } from '@/components/ui/button'
import type {
  ClipCountsResponse,
  Identity,
  IdentityResponse,
  NicheOption,
  SavedInsightsResponse,
  VideoListResponse,
} from '@/types'
import { ArrowRight, SettingsIcon } from '@/components/ui/icon'
import { ICON_INLINE, ICON_SIZE } from '@/components/ui/iconSizes'

// Sidebar Library stat row.
function StatRow({
  label,
  value,
  top,
  emptyHint,
}: {
  label: string
  value: string
  top?: boolean
  /** Shown instead of a bare em-dash: what fills this stat, and where (Issue 412). */
  emptyHint?: string
}) {
  const empty = value === '\u2014'
  return (
    <div className={`flex justify-between gap-3 py-2 text-small ${top ? 'border-t border-default' : ''}`}>
      <span className="shrink-0 text-muted">{label}</span>
      {empty && emptyHint ? (
        <span className="text-right text-subtle">{emptyHint}</span>
      ) : (
        <span className="font-mono font-semibold text-fg">{value}</span>
      )}
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

  useEffect(() => {
    api<{ options: NicheOption[] }>('/creators/niches')
      .then((d) => setNiches(d.options ?? []))
      .catch(() => setNiches([]))
    api<IdentityResponse>('/creators/me/identity')
      .then((d) => setIdentity(d.identity))
      .catch(() => {})
  }, [])

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

  // Saved analyses — reuses the Insights "saved" query/key; rows link to Insights.
  const savedQuery = useQuery({
    queryKey: ['saved-insights'],
    queryFn: () => api<SavedInsightsResponse>('/creators/me/insights/saved'),
  })
  const saved = savedQuery.data?.insights ?? []

  return (
    <>
      <DisclaimerBand>
        A snapshot of your channel — your synced Creator DNA, identity, and saved work. Edit how clips
        are produced in <Link to="/settings" className="text-accent-text underline">Settings</Link>.
      </DisclaimerBand>

      <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-8">
        <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
          <div>
            {/* Issue 355: the nav says "Channel" and the h1 is the channel's own
                name, so the nav word had no anchor on the page. The kicker gives
                it one without displacing the creator's name from the heading. */}
            <p className="text-label uppercase tracking-[0.08em] text-muted">Channel</p>
            <h1 className="font-display text-h1 text-fg">{channelName}</h1>
            <p className="mt-1 text-small text-muted">Channel profile snapshot</p>
          </div>
          <Link to="/settings">
            <Button variant="secondary">
              <SettingsIcon className={ICON_SIZE.md} aria-hidden="true" /> Editing settings <ArrowRight className={`${ICON_SIZE.md} ${ICON_INLINE}`} aria-hidden="true" /></Button>
          </Link>
        </div>

        <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1fr)_280px]">
          {/* Main: DNA snapshot + saved analyses (read-only; editing lives in Settings) */}
          <div className="flex flex-col gap-6">
            {/* The `reauth_required` notification and its email both link here
                (worker/tasks.py, notify/copy.py). Until 2026-08-14 this page had
                no reconnect control, so the weekly Testing-mode token expiry
                dead-ended the creator. */}
            <YouTubeConnectionCard />
            <DnaCard identity={identity} niches={niches} />
            <div className="rounded-md border border-default bg-surface shadow-sm inset-shadow-highlight">
              <div className="flex items-center justify-between border-b border-default px-[18px] py-4">
                {/* Issue 355: "Saved insights" everywhere — Insights calls the
                    same endpoint and query key ['saved-insights'] by that name. */}
                <span className="text-body font-semibold text-fg">Saved insights</span>
                <span className="text-label text-subtle">
                  {saved.length > 0 ? `${saved.length} saved` : ''}
                </span>
              </div>
              {savedQuery.isError ? (
                <p className="px-[18px] py-4 text-small text-danger">Could not load saved insights.</p>
              ) : saved.length === 0 ? (
                <EmptyStatePrompt
                  className="px-[18px] py-4"
                  title="No saved insights yet."
                  detail="Bookmark a performer analysis or improvement brief and it lands here."
                  actionLabel="Go to Insights"
                  actionTo="/insights"
                />
              ) : (
                saved.map((s) => (
                  <div
                    key={s.id}
                    className="flex items-center justify-between border-b border-default px-[18px] py-3 last:border-b-0"
                  >
                    <div>
                      <div className="text-small text-fg">{s.title || 'Saved analysis'}</div>
                      <div className="font-mono text-label text-subtle">
                        {new Date(s.created_at).toLocaleDateString(undefined, {
                          month: 'short',
                          day: 'numeric',
                          year: 'numeric',
                        })}
                      </div>
                    </div>
                    <Link to="/insights" className="text-label text-accent-text hover:underline">
                      Open <ArrowRight className={`${ICON_SIZE.md} ${ICON_INLINE}`} aria-hidden="true" />
                    </Link>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Sidebar: library + YouTube analytics */}
          <div className="flex flex-col gap-4">
            <div className="rounded-md border border-default bg-surface p-[18px] shadow-sm inset-shadow-highlight">
              <div className="mb-3 text-label uppercase tracking-[0.08em] text-muted">Library</div>
              <div className="flex flex-col">
                <StatRow label="Videos" value={String(videos.length)} />
                <StatRow label="Clips rendered" value={String(clipsRendered)} top />
                <StatRow label="Shorts published" value="—" top emptyHint="none yet — publish from Review" />
                <StatRow label="Clip ratings" value="—" top emptyHint="none yet — keep/drop clips in Review" />
              </div>
            </div>
            <AnalyticsPanel variant="sidebar" />
          </div>
        </div>
      </main>
    </>
  )
}
