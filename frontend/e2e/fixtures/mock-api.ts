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
  BrandKit,
  ClipTranscript,
  CurrentUser,
  DataGate,
  DnaResponse,
  IdentityResponse,
  ImprovementBrief,
  InsightsResponse,
  PublicationListOut,
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
      has_peaks: true,
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
      // Non-null so the review walk exercises PublishPanel's publications query
      // (enabled: Boolean(clip.render_uri)) and the Editor renders its player.
      // The path returns JSON, not media — <video> load noise is BENIGN-filtered.
      render_uri: '/clips/c1/download',
      cleaned_render_uri: null,
      applied_title: null,
      applied_description: null,
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
      applied_title: null,
      applied_description: null,
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
      applied_title: null,
      applied_description: null,
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

// Scheduled publishes for the review page's PublishPanel (Issue 196). One
// scheduled row (shows the two-step Confirm affordance) + one done row with a
// youtube_video_id (shows the youtu.be link). privacy_note mirrors the backend
// verbatim (uploads land private — honesty constraint).
const PUBLICATIONS_PRIVACY_NOTE =
  'Pre-audit: clips are uploaded as private. Open YouTube Studio to publish publicly when ready.'

const PUBLICATIONS: PublicationListOut = {
  publications: [
    {
      id: 'p1',
      clip_id: 'c1',
      creator_id: 'cr1',
      task_id: null,
      youtube_video_id: null,
      status: 'scheduled',
      error: null,
      scheduled_at: '2027-01-09T18:00:00+00:00',
      platform: 'youtube',
      confirmed_at: null,
      created_at: '2026-07-01T08:00:00Z',
      updated_at: '2026-07-01T08:00:00Z',
      privacy_note: PUBLICATIONS_PRIVACY_NOTE,
    },
    {
      id: 'p2',
      clip_id: 'c1',
      creator_id: 'cr1',
      task_id: 'task-2',
      youtube_video_id: 'yt12345abcd',
      status: 'done',
      error: null,
      scheduled_at: '2026-06-20T18:00:00+00:00',
      platform: 'youtube',
      confirmed_at: '2026-06-19T10:00:00Z',
      created_at: '2026-06-18T08:00:00Z',
      updated_at: '2026-06-20T18:05:00Z',
      privacy_note: PUBLICATIONS_PRIVACY_NOTE,
    },
  ],
  suggested_windows: [
    { day_of_week: 6, day_name: 'Saturday', hour: 10, activity_index: 0.91, label: 'Saturday 10:00 AM' },
    { day_of_week: 3, day_name: 'Wednesday', hour: 18, activity_index: 0.78, label: 'Wednesday 6:00 PM' },
    { day_of_week: 0, day_name: 'Sunday', hour: 16, activity_index: 0.66, label: 'Sunday 4:00 PM' },
  ],
  privacy_note: PUBLICATIONS_PRIVACY_NOTE,
  truncated: false,
}

// Brand kit (Issue 186) — shaped to BrandKitOut (routers/creators.py). Without
// this fixture the catch-all `{}` left every field undefined, causing the
// controlled-to-uncontrolled React warning on Settings (OFF_COURSE_BUGS
// 2026-06-24). aspect/background stay null so <select> values remain within
// their option sets.
const BRAND_KIT: BrandKit = {
  subtitle: 'bold_pop',
  background: null,
  captions_enabled: true,
  zoom_on_peak: false,
  denoise: false,
  aspect: null,
}

// Word-level transcript for the short-form Editor (GET /clips/{id}/transcript)
// so the transcript/timeline surface renders instead of the empty state.
const TRANSCRIPT: ClipTranscript = {
  clip_id: 'c1',
  clip_duration_s: 35,
  words: [
    { word: 'So', start_s: 0.2, end_s: 0.4, index: 0 },
    { word: 'this', start_s: 0.5, end_s: 0.7, index: 1 },
    { word: 'switch', start_s: 0.8, end_s: 1.2, index: 2 },
    { word: 'actually', start_s: 1.3, end_s: 1.8, index: 3 },
    { word: 'clicks', start_s: 1.9, end_s: 2.3, index: 4 },
    { word: 'on', start_s: 2.4, end_s: 2.5, index: 5 },
    { word: 'the', start_s: 2.6, end_s: 2.7, index: 6 },
    { word: 'downstroke', start_s: 2.8, end_s: 3.5, index: 7 },
  ],
}

// A BBC-audiowaveform payload for v1 (Issue 392). Deliberately speech-shaped —
// bursts separated by pauses — so the audit screenshots exercise the real render
// path, not just the "no peaks" fallback (which v2/v3 still cover).
const PEAKS_V1 = (() => {
  // Must span the WHOLE source (v1 is 1320s), not an arbitrary slice: the
  // short-form timeline windows these peaks to its clip's span, so a short
  // fixture leaves every clip past the end of the data reading as silence.
  // 1320s at 16 kHz / 512 samples-per-pixel = 41,250 pairs.
  const length = Math.round((1320 * 16000) / 512)
  const data: number[] = []
  for (let i = 0; i < length; i++) {
    // Phrases of ~40 pairs with ~15-pair gaps, amplitude varying inside each.
    const inPhrase = i % 55 < 40
    const env = inPhrase ? 0.35 + 0.6 * Math.abs(Math.sin(i * 0.7)) * Math.abs(Math.cos(i * 0.13)) : 0.02
    const v = Math.round(env * 120)
    data.push(-v, v)
  }
  return { version: 1, sample_rate: 16000, samples_per_pixel: 512, bits: 8, length, data }
})()

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
  '/creators/me/brand-kit': BRAND_KIT,
}

function json(route: Route, body: unknown, status = 200): Promise<void> {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

/** Seed for the per-page edit-document state (Issue 409). */
export interface EditDocSeed {
  revision: number
  doc: { version: number; cuts: { id: string; start_s: number; end_s: number }[]; last_applied_at: null }
}

/** Default mirrors the real server for an unedited clip: a synthesised empty
 * document at revision 0. Deliberately visual-noop so every existing spec and
 * pixel baseline renders exactly as before; specs that need a stored document
 * override the `editDocSeed` option. */
const EMPTY_EDIT_DOC: EditDocSeed = {
  revision: 0,
  doc: { version: 1, cuts: [], last_applied_at: null },
}

interface MockCtx {
  editDoc: { revision: number; doc: unknown }
  unmatchedGets: string[]
}

async function dispatch(route: Route, seed: Seed, ctx: MockCtx): Promise<void> {
  const { pathname } = new URL(route.request().url())
  const method = route.request().method()

  // Server-side edit document (Issue 391): GET hydrates, PUT compare-and-sets
  // and advances the revision — the same contract as routers/clips.py, so the
  // autosave → Saved loop runs for real instead of against the catch-all `{}`.
  if (/^\/clips\/[^/]+\/edit-document$/.test(pathname)) {
    const clipId = pathname.split('/')[2]
    if (method === 'PUT') {
      const body = route.request().postDataJSON() as { base_revision: number; doc: unknown }
      if (body.base_revision !== ctx.editDoc.revision) {
        return json(
          route,
          {
            detail: {
              code: 'stale_revision',
              revision: ctx.editDoc.revision,
              doc: ctx.editDoc.doc,
            },
          },
          409,
        )
      }
      ctx.editDoc.revision += 1
      ctx.editDoc.doc = body.doc
      return json(route, {
        clip_id: clipId,
        revision: ctx.editDoc.revision,
        doc: ctx.editDoc.doc,
        updated_at: '2026-08-04T00:00:00+00:00',
        clip_duration_s: 40,
      })
    }
    return json(route, {
      clip_id: clipId,
      revision: ctx.editDoc.revision,
      doc: ctx.editDoc.doc,
      updated_at: null,
      clip_duration_s: 40,
    })
  }

  // Waveform peaks (Issue 392). Only v1 has them; every other video 404s, which
  // is the normal terminal state the editor draws a labelled flat track for.
  if (/^\/videos\/[^/]+\/peaks$/.test(pathname)) {
    return pathname === '/videos/v1/peaks'
      ? json(route, PEAKS_V1)
      : json(route, { detail: 'No waveform for this video' }, 404)
  }

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

  // Brand-kit style suggestion (Issue 187): 204 = "no suggestion yet". Must be
  // explicit — the catch-all `{}` is truthy and would render an empty banner
  // (BrandKitSection treats any 200 body as a suggestion).
  if (pathname === '/creators/me/brand-kit/suggestion')
    return route.fulfill({ status: 204 })

  // Word-level clip transcript for the short-form Editor (Issue 307).
  if (method === 'GET' && /^\/clips\/[^/]+\/transcript$/.test(pathname))
    return json(route, TRANSCRIPT)

  // Trim-cut re-render (Wave-1 trim-rerender lane): TaskQueued-shaped 202
  // mirroring routers/review.py trim_render.
  if (method === 'POST' && /^\/clips\/[^/]+\/trim-render$/.test(pathname))
    return json(route, { task_id: 't-trim', status: 'queued', stream_url: null }, 202)

  // Scheduled publishes (Issue 196): list, schedule (201 → new scheduled row),
  // confirm/cancel (mutated row; cancel repurposes status=failed + error).
  if (/^\/clips\/[^/]+\/publications$/.test(pathname)) {
    if (method === 'POST')
      return json(route, { ...PUBLICATIONS.publications[0], id: 'p-new' }, 201)
    return json(route, PUBLICATIONS)
  }
  if (
    method === 'POST' &&
    /^\/clips\/[^/]+\/publications\/[^/]+\/(confirm|cancel)$/.test(pathname)
  ) {
    const cancelled = pathname.endsWith('/cancel')
    return json(route, {
      ...PUBLICATIONS.publications[0],
      status: cancelled ? 'failed' : 'confirmed',
      error: cancelled ? 'Cancelled by creator' : null,
    })
  }

  // Applied publish metadata (Wave-1 metadata lane): PATCH /clips/{id}.
  if (method === 'PATCH' && /^\/clips\/[^/]+$/.test(pathname))
    return json(route, REVIEW_CLIPS.clips[0])

  // Static GETs.
  if (method === 'GET' && pathname in GET_TABLE) return json(route, GET_TABLE[pathname])

  // Anything else (POST actions): benign 200 so initial render never throws.
  // Unmodeled GETs are RECORDED and surfaced at page teardown — a GET falling
  // through here fed the editor `{}` for a whole batch before anything noticed
  // (Issue 409). Specs can assert on the `unmatchedGets` fixture directly.
  if (method === 'GET') ctx.unmatchedGets.push(pathname)
  return json(route, {}, 200)
}

// Extended test with an auto-applied API mock. `seed` and `editDocSeed` are
// overridable options; `unmatchedGets` exposes every GET the mock had no
// modelled answer for.
export const test = base.extend<{
  seed: Seed
  editDocSeed: EditDocSeed
  unmatchedGets: string[]
}>({
  seed: ['authed', { option: true }],
  editDocSeed: [EMPTY_EDIT_DOC, { option: true }],
  // Playwright requires the object-destructuring pattern on fixture functions,
  // including dependency-less ones — `{}` is its documented idiom, so the
  // eslint rule loses this one.
  // eslint-disable-next-line no-empty-pattern
  unmatchedGets: async ({}, use) => {
    await use([])
  },
  page: async ({ page, seed, editDocSeed, unmatchedGets }, use) => {
    const ctx: MockCtx = {
      editDoc: { revision: editDocSeed.revision, doc: structuredClone(editDocSeed.doc) },
      unmatchedGets,
    }
    await page.route(
      (url) =>
        API_PREFIXES.some((p) => url.pathname === p || url.pathname.startsWith(`${p}/`)),
      (route) => dispatch(route, seed, ctx),
    )
    await use(page)
    if (unmatchedGets.length) {
      console.error(
        `[mock-api] unmodeled GETs fell through to the {} catch-all: ${[...new Set(unmatchedGets)].join(', ')}`,
      )
    }
  },
})

export { expect }
