// Network-boundary mock of the FastAPI backend for the SPA E2E harness (Issue
// 162). We intercept the cookie-authed API prefixes with page.route() and reply
// with fixtures shaped to frontend/src/types.ts — the industry-standard way to
// test a frontend in isolation (playwright.dev/docs/network). This lets every
// page render in a real browser without a live backend (no Docker here).
//
// Two seeds: 'authed' (the default — /auth/me returns a creator) and 'anon'
// (/auth/me 401s, so AuthGate redirects to /login). Override per-spec with
// test.use({ seed: 'anon' }).

import { test as base, expect, type Route } from '@playwright/test'
import type {
  Analytics,
  Balance,
  CurrentUser,
  DataGate,
  DnaResponse,
  IdentityResponse,
  ImprovementBrief,
  InsightsResponse,
  ReviewClipListResponse,
  SavedInsightsResponse,
  UploadIntel,
  VideoListResponse,
} from '../../src/types'

type Seed = 'authed' | 'anon'

// API prefixes the SPA calls (see vite.config.ts proxy + lib/api.ts). Everything
// else (HTML, JS, CSS, HMR, fonts) falls through to the real Vite dev server.
const API_PREFIXES = ['/auth', '/billing', '/creators', '/videos', '/clips', '/api', '/tasks']

// ── Fixtures (shaped to src/types.ts) ────────────────────────────────────────

const USER: CurrentUser = {
  channel_title: 'Pixel Forge',
  email: 'creator@example.com',
  analysis_mode: 'auto',
  onboarding_state: 'complete',
  setup: {
    step: 'complete',
    label: "You're all set",
    next_action_type: 'wait',
    next_action_url: null,
    progress_index: 4,
    progress_total: 4,
  },
}

const BALANCE: Balance = {
  minutes_balance: 142,
  low_balance: false,
  trial_active: true,
  trial_days_remaining: 9,
}

const VIDEOS: VideoListResponse = {
  state: 'populated',
  videos: [
    {
      id: 'v1',
      youtube_video_id: 'dQw4w9WgXcQ',
      title: 'I Built a Mechanical Keyboard From Scratch',
      kind: 'long',
      ingest_status: 'done',
      duration_s: 1320,
      created_at: '2026-06-10T12:00:00Z',
      origin: 'youtube',
      clippable: true,
    },
    {
      id: 'v2',
      youtube_video_id: 'abc12345678',
      title: '60-Second Soldering Tip That Saved My Build',
      kind: 'short',
      ingest_status: 'done',
      duration_s: 58,
      created_at: '2026-06-12T09:30:00Z',
      origin: 'youtube',
      clippable: true,
    },
    {
      id: 'v3',
      youtube_video_id: 'xyz98765432',
      title: 'Desk Setup Tour 2026',
      kind: 'long',
      ingest_status: 'running',
      duration_s: null,
      created_at: '2026-06-18T15:45:00Z',
      origin: 'upload',
      clippable: false,
    },
  ],
}

const REVIEW_CLIPS: ReviewClipListResponse = {
  clips: [
    {
      id: 'c1',
      video_id: 'v1',
      setup_start_s: 120,
      start_s: 125,
      end_s: 160,
      peak_s: 150,
      score: 0.86,
      rank: 1,
      principle: 'Open Loop',
      reasoning:
        'Starts at the question setup before the reveal, holding the curiosity gap open until the payoff.',
      render_status: 'done',
      render_uri: null,
      cleaned_render_uri: null,
    },
    {
      id: 'c2',
      video_id: 'v1',
      setup_start_s: 410,
      start_s: 415,
      end_s: 448,
      peak_s: 440,
      score: 0.74,
      rank: 2,
      principle: 'Payoff Proximity',
      reasoning: 'The build pays off within seconds of the cut-in, rewarding the scroll-stop fast.',
      render_status: 'done',
      render_uri: null,
      cleaned_render_uri: null,
    },
    {
      id: 'c3',
      video_id: 'v1',
      setup_start_s: 880,
      start_s: 885,
      end_s: 921,
      peak_s: 905,
      score: 0.61,
      rank: 3,
      principle: 'Emotional Peak',
      reasoning: 'Genuine reaction at the first power-on — strong emotional spike for the audience.',
      render_status: 'pending',
      render_uri: null,
      cleaned_render_uri: null,
    },
  ],
}

const INSIGHTS: InsightsResponse = {
  totals: {
    videos_analyzed: 48,
    shorts: 30,
    longs: 18,
    ingested_done: 48,
    total_minutes_processed: 1240,
  },
  dna: {
    version: 3,
    status: 'ready',
    optimal_clip_len_s: 42,
    best_source_region: 'mid-roll (8–12 min)',
    optimal_upload_gap_h: 72,
  },
  top_performers: [
    {
      video_id: 'v1',
      youtube_video_id: 'dQw4w9WgXcQ',
      title: 'I Built a Mechanical Keyboard From Scratch',
      kind: 'long',
      performance_score: 0.92,
    },
    {
      video_id: 'v2',
      youtube_video_id: 'abc12345678',
      title: '60-Second Soldering Tip That Saved My Build',
      kind: 'short',
      performance_score: 0.81,
    },
  ],
  bottom_performers: [
    {
      video_id: 'v3',
      youtube_video_id: 'xyz98765432',
      title: 'Desk Setup Tour 2026',
      kind: 'long',
      performance_score: 0.21,
    },
  ],
}

const UPLOAD_INTEL: UploadIntel = {
  data_available: true,
  best_windows: [
    { day_name: 'Saturday', label: 'Sat 10am–12pm', activity_index: 0.91 },
    { day_name: 'Wednesday', label: 'Wed 6–8pm', activity_index: 0.78 },
    { day_name: 'Sunday', label: 'Sun 4–6pm', activity_index: 0.66 },
  ],
  optimal_gap_hours: 72,
}

const BRIEF: ImprovementBrief = {
  status: 'ready',
  brief: [
    '1. **Lead with the payoff**',
    'Your top clips open on motion, not on a slow throat-clear. Cut the first 2–3s of setup.',
    '',
    '2. **Tighten cold opens**',
    '- Remove filler intros ("hey guys, so today")',
    '- Start on the first visible action',
    '- Promise the result in the first line',
  ].join('\n'),
  error: null,
}

const DNA: DnaResponse = {
  profile: {
    version: 3,
    status: 'confirmed',
    created_at: '2026-06-15T10:00:00Z',
    brief_text:
      '**Your channel DNA**\nFast-paced maker content with hands-on builds, dry humor, and a payoff-forward structure.',
    optimal_clip_len_s: 42,
    best_source_region: 'mid-roll (8–12 min)',
    optimal_upload_gap_h: 72,
  },
}

const IDENTITY: IdentityResponse = {
  identity: {
    version: 2,
    created_at: '2026-06-14T10:00:00Z',
    niches: ['DIY & Crafts', 'Tech'],
    audience_summary: 'Hobbyist makers, 18–34, who watch to learn one concrete technique per video.',
    mission: 'Make hardware projects approachable for beginners.',
    content_pillars: ['Builds', 'Quick tips', 'Honest reviews'],
    tone_tags: ['energetic', 'nerdy', 'encouraging'],
    hard_nos: ['clickbait thumbnails', 'fake urgency'],
  },
  conflict: null,
}

const DATA_GATE: DataGate = {
  long_form_videos: 18,
  shorts: 30,
  long_form_ready: true,
  shorts_ready: true,
  ready: true,
}

const NICHES = {
  options: [
    { id: 'diy', label: 'DIY & Crafts' },
    { id: 'tech', label: 'Tech & Gadgets' },
    { id: 'gaming', label: 'Gaming' },
    { id: 'edu', label: 'Education' },
  ],
}

const API_KEYS = {
  keys: [
    {
      id: 'k1',
      name: 'CI pipeline',
      key_prefix: 'cc_live_ab',
      last_used_at: '2026-06-18T08:00:00Z',
      created_at: '2026-05-01T08:00:00Z',
    },
  ],
}

const SAVED_INSIGHTS: SavedInsightsResponse = {
  insights: [
    {
      id: 's1',
      title: 'Hook patterns that retain',
      content: 'Your retention holds when the first line names the outcome. Lead with it.',
      dna_version: 3,
      created_at: '2026-06-16T08:00:00Z',
    },
  ],
}

const ANALYTICS: Analytics = {
  videos_in_period: 12,
  total_views: 340_000,
  total_watch_time_h: 5400,
  avg_view_duration_s: 184,
  avg_engagement_rate: 0.064,
  metrics_available: true,
}

const SUMMARIES = {
  summaries: [
    {
      id: 'sum1',
      video_id: 'v1',
      status: 'ready',
      render_status: 'done',
      target_duration_s: 300,
      render_uri: null,
      created_at: '2026-07-01T08:00:00Z',
      segments: [
        {
          start_s: 30,
          end_s: 75,
          score: 0.86,
          principle: 'Open Loop',
          rationale: 'Poses the build question before the reveal.',
        },
        {
          start_s: 240,
          end_s: 290,
          score: 0.71,
          principle: 'Pattern Interrupt',
          rationale: 'The soldering mishap breaks the rhythm and re-hooks.',
        },
      ],
    },
  ],
}

// Static GET endpoints → fixture body.
const GET_TABLE: Record<string, unknown> = {
  '/billing/balance': BALANCE,
  '/videos': VIDEOS,
  '/creators/me/insights': INSIGHTS,
  '/creators/me/insights/saved': SAVED_INSIGHTS,
  '/creators/me/upload-intel': UPLOAD_INTEL,
  '/creators/me/improvement-brief': BRIEF,
  '/creators/me/dna': DNA,
  '/creators/me/identity': IDENTITY,
  '/creators/me/data-gate': DATA_GATE,
  '/creators/niches': NICHES,
  '/creators/me/api-keys': API_KEYS,
}

function json(route: Route, body: unknown, status = 200): Promise<void> {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

async function dispatch(route: Route, seed: Seed): Promise<void> {
  const { pathname } = new URL(route.request().url())
  const method = route.request().method()

  // Auth probe drives AuthGate: authed → user, anon → 401 (redirect to /login).
  if (pathname === '/auth/me') {
    return seed === 'authed'
      ? json(route, USER)
      : json(route, { detail: 'Not authenticated' }, 401)
  }

  // Dynamic sub-resources.
  if (/^\/videos\/[^/]+\/clips$/.test(pathname)) return json(route, REVIEW_CLIPS)
  if (method === 'GET' && /^\/videos\/[^/]+\/summaries$/.test(pathname))
    return json(route, SUMMARIES)
  if (pathname === '/creators/me/insights/analytics') return json(route, ANALYTICS)

  // Static GETs.
  if (method === 'GET' && pathname in GET_TABLE) return json(route, GET_TABLE[pathname])

  // Anything else (POST actions, unmodeled GETs): benign 200 so initial render
  // never throws. Interaction-driven flows get their own specs later.
  return json(route, {}, 200)
}

// Extended test with an auto-applied API mock. `seed` is an overridable option.
export const test = base.extend<{ seed: Seed }>({
  seed: ['authed', { option: true }],
  page: async ({ page, seed }, use) => {
    await page.route(
      (url) =>
        API_PREFIXES.some((p) => url.pathname === p || url.pathname.startsWith(`${p}/`)),
      (route) => dispatch(route, seed),
    )
    await use(page)
  },
})

export { expect }
