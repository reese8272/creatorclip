import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import { useAuth } from '@/hooks/useAuth'
import { DisclaimerBand } from '@/components/DisclaimerBand'
import { DnaCard } from '@/components/profile/DnaCard'
import { IdentitySection } from '@/components/profile/IdentitySection'
import { IntakeModeSection } from '@/components/profile/IntakeModeSection'
import { ApiKeysSection } from '@/components/profile/ApiKeysSection'
import type { Identity, IdentityResponse, NicheOption } from '@/types'

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

  return (
    <>
      <DisclaimerBand>
        AutoClip predicts fit with your style and audience — it does not promise virality. This
        brief is grounded in your own channel data.
      </DisclaimerBand>

      <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-6 px-4 py-8">
        <DnaCard identityCreatedAt={identity?.created_at ?? null} />
        <IdentitySection
          key={identity?.version ?? 'new'}
          niches={niches}
          identity={identity}
          conflict={conflict}
          onSaved={() => setReloadToken((t) => t + 1)}
        />
        <IntakeModeSection initialMode={user?.analysis_mode ?? 'auto'} />
        <ApiKeysSection />
      </main>
    </>
  )
}
