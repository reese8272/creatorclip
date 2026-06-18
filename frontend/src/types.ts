// Shapes returned by the existing FastAPI endpoints the profile page consumes.
// Kept deliberately narrow — only the fields the UI reads (Pareto: model what we
// use, not the whole payload).

export type AnalysisMode = 'auto' | 'selective' | 'manual'

export interface CurrentUser {
  channel_title?: string | null
  email?: string | null
  analysis_mode?: AnalysisMode
  onboarding_state?: string
}

export interface Balance {
  minutes_balance: number
  low_balance: boolean
}

export interface DnaProfile {
  version: number
  status: string
  created_at: string
  brief_text: string | null
  optimal_clip_len_s: number | null
  best_source_region: string | null
  optimal_upload_gap_h: number | null
}

export interface DnaResponse {
  profile: DnaProfile | null
  message?: string
}

export interface NicheOption {
  id: string
  label: string
}

export interface Identity {
  version: number
  created_at: string
  niches: string[]
  audience_summary: string
  mission?: string | null
  content_pillars?: string[]
  tone_tags?: string[]
  hard_nos?: string[]
}

export interface IdentityResponse {
  identity: Identity | null
  conflict?: string | null
}

export interface IdentityPayload {
  niches: string[]
  audience_summary: string
  mission: string | null
  content_pillars: string[]
  tone_tags: string[]
  hard_nos: string[]
}

export interface ApiKey {
  id: string
  name: string
  key_prefix: string
  last_used_at: string | null
  created_at: string | null
}
