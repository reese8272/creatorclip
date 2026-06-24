import { useEffect, useState, type ReactNode } from 'react'
import { useAuth } from '@/hooks/useAuth'
import { api } from '@/lib/api'
import { DisclaimerBand } from '@/components/DisclaimerBand'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { BrandKitSection } from '@/components/profile/BrandKitSection'
import { IdentitySection } from '@/components/profile/IdentitySection'
import { IntakeModeSection } from '@/components/profile/IntakeModeSection'
import { PublishingSection } from '@/components/profile/PublishingSection'
import { ApiKeysSection } from '@/components/profile/ApiKeysSection'
import { AccountDeletion } from '@/components/profile/AccountDeletion'
import type { Identity, IdentityResponse, NicheOption } from '@/types'

// A titled section card (matches the prototype's Settings card chrome).
function SettingsCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-md border border-default bg-surface shadow-sm shadow-inset">
      <div className="border-b border-default px-[18px] py-[15px] text-body font-semibold text-fg">
        {title}
      </div>
      {children}
    </div>
  )
}

// A not-yet-wired control, shown honestly: the label + description from the
// design, a disabled mock control, and a "Soon" badge. No faux persistence —
// per the agreed scope (docs/DECISIONS.md, Issue 308) we never imply an effect
// that isn't there.
function ComingSoonRow({
  label,
  description,
  mock,
}: {
  label: string
  description: string
  mock: ReactNode
}) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-default px-[18px] py-[15px] last:border-b-0">
      <div>
        <div className="flex items-center gap-2 text-small text-fg">
          {label}
          <Badge variant="muted">Soon</Badge>
        </div>
        <div className="mt-0.5 text-small text-subtle">{description}</div>
      </div>
      <div className="pointer-events-none opacity-50">{mock}</div>
    </div>
  )
}

// A disabled segmented-control mock for the preview rows.
function SegmentedMock({ options, active }: { options: string[]; active: number }) {
  return (
    <div className="inline-flex gap-0.5 rounded-md border border-strong bg-bg p-[3px]">
      {options.map((o, i) => (
        <span
          key={o}
          className={
            i === active
              ? 'rounded-sm bg-accent-soft px-3 py-1 text-xs text-accent-text'
              : 'rounded-sm px-3 py-1 text-xs text-muted'
          }
        >
          {o}
        </span>
      ))}
    </div>
  )
}

function ToggleMock({ on }: { on: boolean }) {
  return (
    <span
      className={`relative inline-block h-[22px] w-[38px] rounded-full ${on ? 'bg-accent' : 'bg-strong'}`}
    >
      <span
        className={`absolute top-0.5 h-[18px] w-[18px] rounded-full ${on ? 'left-[18px] bg-on-accent' : 'left-0.5 bg-muted'}`}
      />
    </span>
  )
}

// Settings — "How AutoClip edits and packages your clips." (Issues 304/308).
// The clip-production + account controls relocated here from Profile. Controls
// backed by a real field are fully functional (Brand kit, Intake mode, Publishing,
// API keys, Account); the design's not-yet-wired controls are shown as honest
// disabled previews. No backend changes (scope: docs/DECISIONS.md, Issue 308).
export function Settings() {
  const { user } = useAuth()
  // Channel identity editing relocated here from the (now read-only) Profile.
  const [niches, setNiches] = useState<NicheOption[]>([])
  const [identity, setIdentity] = useState<Identity | null>(null)
  const [conflict, setConflict] = useState<string | null>(null)
  const [reloadToken, setReloadToken] = useState(0)

  useEffect(() => {
    api<{ options: NicheOption[] }>('/creators/niches')
      .then((d) => setNiches(d.options ?? []))
      .catch(() => setNiches([]))
    api<IdentityResponse>('/creators/me/identity')
      .then((d) => {
        setIdentity(d.identity)
        setConflict(d.conflict ?? null)
      })
      .catch(() => {})
  }, [reloadToken])

  return (
    <>
      <DisclaimerBand>
        AutoClip predicts fit with your style and audience — it does not promise virality.
        Recommendations are estimates grounded in your own data, not guarantees.
      </DisclaimerBand>

      <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-4 px-4 py-8">
        <header className="mb-1">
          <h1 className="font-display text-h1 text-fg">Settings</h1>
          <p className="mt-1 text-body text-muted">
            How AutoClip edits and packages your clips. These defaults apply to every new video; you
            can override per clip in the editor.
          </p>
        </header>

        {/* Channel identity — relocated from Profile (functional). */}
        <IdentitySection
          key={identity?.version ?? 'new'}
          niches={niches}
          identity={identity}
          conflict={conflict}
          onSaved={() => setReloadToken((t) => t + 1)}
        />

        {/* Captions & rendering — functional (brand kit). */}
        <BrandKitSection />

        {/* Captions — design previews not yet wired to render. */}
        <SettingsCard title="Captions — more">
          <ComingSoonRow
            label="Position"
            description="Where captions sit in frame"
            mock={<SegmentedMock options={['Top', 'Center', 'Lower third']} active={1} />}
          />
          <ComingSoonRow
            label="Highlight color"
            description="Active-word emphasis"
            mock={
              <div className="flex gap-2">
                <span className="h-6 w-6 rounded-sm bg-accent ring-2 ring-accent-border" />
                <span className="h-6 w-6 rounded-sm" style={{ background: 'oklch(75% 0.16 75)' }} />
                <span className="h-6 w-6 rounded-sm" style={{ background: 'oklch(68% 0.17 145)' }} />
                <span className="h-6 w-6 rounded-sm" style={{ background: 'var(--color-fg)' }} />
              </div>
            }
          />
        </SettingsCard>

        {/* Cuts & pacing — previews (tune per-clip in the Editor today). */}
        <SettingsCard title="Cuts & pacing">
          <ComingSoonRow
            label="Cut density"
            description="How aggressively to tighten — tune per clip in the Editor today"
            mock={<SegmentedMock options={['Tight', 'Balanced', 'Relaxed']} active={1} />}
          />
          <ComingSoonRow
            label="Remove filler words"
            description='Cut "um", "uh", "like" — available now per clip in the Editor'
            mock={<ToggleMock on />}
          />
          <ComingSoonRow
            label="Trim long silences"
            description="Collapse pauses — available now per clip in the Editor"
            mock={<ToggleMock on />}
          />
        </SettingsCard>

        {/* Tone & language — previews. */}
        <SettingsCard title="Tone & language">
          <ComingSoonRow
            label="Title & caption voice"
            description="Matches your Creator DNA tone"
            mock={<SegmentedMock options={['Casual', 'Neutral', 'Polished']} active={0} />}
          />
          <ComingSoonRow
            label="Profanity filter"
            description="Bleep or mute strong language"
            mock={<ToggleMock on={false} />}
          />
        </SettingsCard>

        {/* Workflow — intake is functional. */}
        <IntakeModeSection initialMode={user?.analysis_mode ?? 'auto'} />
        <SettingsCard title="Workflow — more">
          <ComingSoonRow
            label="Notify when clips are ready"
            description="Email on render completion"
            mock={<ToggleMock on />}
          />
        </SettingsCard>

        {/* Brand kit — watermark / bumpers preview. */}
        <SettingsCard title="Brand kit">
          <ComingSoonRow
            label="Watermark / logo"
            description="Overlaid on every clip"
            mock={
              <span className="flex h-10 w-10 items-center justify-center rounded-sm border border-dashed border-strong font-mono text-[11px] text-subtle">
                PNG
              </span>
            }
          />
          <ComingSoonRow
            label="Intro / outro"
            description="Optional bumper clips"
            mock={<span className="rounded-sm border border-strong px-3 py-1.5 text-xs text-fg">Add</span>}
          />
        </SettingsCard>

        {/* Account & access — relocated from Profile (functional). */}
        <PublishingSection canPublish={user?.can_publish ?? false} />
        <ApiKeysSection />
        <AccountDeletion />

        {/* Footer (design chrome). Each section above saves on its own; a single
            global save/reset over the not-yet-wired previews would imply an
            effect that isn't there, so these are disabled honestly. */}
        <div className="flex items-center justify-between gap-3 border-t border-default pt-4">
          <p className="text-label text-subtle">Each section saves on its own.</p>
          <div className="flex gap-2">
            <Button variant="ghost" disabled title="Per-control DNA defaults are coming">
              Reset to DNA defaults
            </Button>
            <Button variant="secondary" disabled title="Each section above saves on its own">
              Save changes
            </Button>
          </div>
        </div>
      </main>
    </>
  )
}
