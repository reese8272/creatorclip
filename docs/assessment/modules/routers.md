# routers — assessed 2026-05-29

Slice: routers/auth.py, billing.py, clips.py, creators.py, improvement.py,
review.py, upload_intel.py, videos.py. Load-bearing focus: per-tenant isolation,
JWT-derived creator_id, Pydantic response surfaces, blocking calls on the request
loop, per-creator rate limiting.

## Findings

### Security & compliance (category 3)
- Per-creator isolation verified clean across the whole module. Every creator-scoped
  read/write derives `creator.id` from the JWT via `get_current_creator`
  (auth.py:31-47) and filters on it, never trusting a request-body id:
  - clips.py:48, 90, 95, 117, 138 (Video/Clip ownership checks + `WHERE creator_id`)
  - videos.py:41, 69-72, 134-137, 176 (list/link/upload/status all scoped)
  - improvement.py:33-36 (prior SEV-0 Issue-33 leak is fixed: joins `Video` and
    filters `Video.creator_id == creator.id`)
  - review.py:45 (clip ownership re-checked before feedback insert)
  - upload_intel.py:23-24, creators.py:33/60/87 (all derive from JWT creator)
  - billing.py:144-167 webhook keys off Stripe `metadata.creator_id` from the
    signature-verified payload — trusted, not client-supplied; acceptable.
- OAuth token handling clean: `decrypt()` used only at auth.py:157 (delete_account
  revocation); the decrypted token is never logged — log lines use `creator_id` only
  (auth.py:169-181). No token, email, or secret in any `logger.*` call in the module.
  auth.py:121 /me and creators.py:15 /me return `email` to the authenticated owner
  only — not a cross-tenant leak.
- Parameterized SQL throughout (SQLAlchemy ORM/Core); no f-string/`%` queries.
- ToS / source-acquisition: link_video (videos.py:58) explicitly does not download;
  storage-key path-traversal guarded by `_validate_youtube_id` (videos.py:24-29)
  before interpolation into `source/{creator.id}/...` (Issue 73). Clean.
- No virality promise in any string or docstring (auth.py:122 explicitly disclaims).

### Concurrency & scale (category 2)
- Blocking-call audit clean. Known heavy sync paths are offloaded via
  `asyncio.to_thread`: R2 upload videos.py:147 (Issue 67); ~120s Anthropic+web_search
  improvement brief improvement.py:68 (Issue 66); paginated boto3 list+delete on
  account deletion auth.py:189 (Issue 67). OAuth revocation uses `httpx.AsyncClient`
  (auth.py:158) — non-blocking.
- start_pipeline (videos.py:162 → worker/tasks.py:45) only enqueues a Celery chain;
  no blocking work on the request path. All other worker handoffs are `.delay()`
  (clips.py:124, creators.py:42, review.py:67) — request returns immediately.
- [SEV2] videos.py:40-55, clips.py:93-99, upload_intel.py:22-25 — unbounded
  `list(result.scalars())` with no LIMIT/pagination. A creator with thousands of
  videos/clips/activity rows loads the whole set into memory and serializes it in a
  single response. | fix: add keyset or offset pagination (`?limit=&before=`) with a
  hard cap (e.g. 100) on list_videos and list_clips; bound the AudienceActivity read.

### Error handling & API surface (category 7)
- [SEV1] response_model coverage gap (known-open Issue 75). Only billing.py declares
  `response_model` (BalanceOut/PackOut/CheckoutOut at lines 56/67/82). Every other
  endpoint returns a bare `dict`/`list[dict]`, so the OpenAPI schema is untyped and
  responses are neither validated nor field-filtered. Endpoints still lacking a `*Out`
  response_model:
  - auth.py:114 `POST /auth/logout`
  - auth.py:121 `GET /auth/me`
  - clips.py:40 `POST /videos/{video_id}/clips/generate`
  - clips.py:82 `GET /videos/{video_id}/clips`
  - clips.py:107 `POST /clips/{clip_id}/render`
  - clips.py:130 `GET /clips/{clip_id}`
  - creators.py:15 `GET /creators/me`
  - creators.py:28 `GET /creators/me/data-gate`
  - creators.py:38 `POST /creators/me/dna/build`
  - creators.py:48 `GET /creators/me/dna`
  - creators.py:78 `POST /creators/me/dna/confirm`
  - improvement.py:19 `GET /creators/me/improvement-brief`
  - review.py:36 `POST /clips/{clip_id}/feedback`
  - upload_intel.py:16 `GET /creators/me/upload-intel`
  - videos.py:34 `GET /videos`
  - videos.py:60 `POST /videos/link`
  - videos.py:91 `POST /videos/upload`
  - videos.py:169 `GET /videos/{video_id}/status`
  (n/a — no JSON body: auth.py:32 /login and auth.py:46 /callback are RedirectResponse;
  auth.py:134 DELETE /me is 204; billing.py:107 webhook intentionally returns a plain
  ack dict.)
  | fix: define `*Out` Pydantic models (ClipOut, VideoOut, DnaOut, BriefOut,
  FeedbackOut, UploadIntelOut, MeOut, etc.) and set `response_model=` on each route;
  replace the hand-built `_clip_response` dict (clips.py:20) with `ClipOut`. This also
  closes response-side leakage risk (explicit field allow-list vs. whatever the dict
  carries).
- [SEV2] request models: videos.py:62 link_video and videos.py:93 upload_video take
  raw `Form(...)` fields with no Pydantic request model. youtube_video_id is regex-
  validated (videos.py:27) so it is not a security hole, but it sits outside the
  "Pydantic model on every request" rule. | fix: acceptable for multipart upload; if
  kept, record the deviation in DECISIONS.md, else wrap link_video's field in a body
  model.
- HTTP status codes correct: 404 ownership (clips.py:49/91/118/139, videos.py:177),
  409 conflict (videos.py:75/141, clips.py:120, creators.py:89), 413 oversize
  (videos.py:124), 400 bad input (improvement.py:39, auth.py:56/60/63), 422 invalid id
  (videos.py:29), 503/502 dependency failures (billing.py:90/103, improvement.py:76),
  202 queued async (clips.py:105, creators.py:36), 201 create (review.py:34), 204
  delete (auth.py:132).
- Error messages safe — no stack trace or DB error reaches the client. Internal
  failures log the exception and return a generic detail (improvement.py:75-76,
  billing.py:101-103). Clean.

### Resource lifecycle (category 1)
- DB sessions come from `Depends(get_session)` on every route — closed by FastAPI
  dependency teardown on all paths. Clean.
- Upload temp file removed in `finally` (videos.py:148-149) and on the size-limit
  early-abort path (videos.py:130). No leak.
- Module-level singletons: `limiter` (limiter.py:31), stripe client module. The only
  per-call client is the deliberately short-lived one-shot `httpx.AsyncClient` in the
  revocation path (auth.py:158) — acceptable.
- billing webhook idempotent: checks `stripe_session_id` already fulfilled before
  granting (billing.py:144-148). Clean.

### Code cleanliness & typing (category 6)
- [cleanup] clips.py:20 `_clip_response(clip: Clip) -> dict` — the hand-maintained
  field mapping is superseded once a ClipOut response_model exists (DRY).
- [cleanup] improvement.py:47 `_avg(lst)` — param and return untyped; annotate
  `_avg(lst: list[float]) -> float | None`.
- Inline `from ... import` inside handlers (clips.py:60/65, creators.py:40/54/84,
  auth.py:78/148/184, improvement.py:58/63) avoids import cycles / heavy worker imports
  at module load — deliberate, not flagged.

### Config & paths (category 8)
- Paths absolute / tempfile-based (videos.py:111-112). Clean.
- Per-creator rate limiting confirmed: `_creator_key` keys on JWT `sub`, falling back
  to remote IP for unauthenticated requests (limiter.py:14-28). It decodes with
  `verify_exp: False` (limiter.py:23), acceptable since the value is only a rate-limit
  bucket key, not authz.

### Anthropic SDK (category 5)
- n/a — the improvement-brief LLM call lives in improvement/brief.py; this router only
  offloads it via to_thread (improvement.py:68). SDK correctness is that module's slice.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok |
| 2 Concurrency & scale | 1 SEV2 (unbounded list endpoints) |
| 3 Security & compliance | ok (isolation, tokens, ToS verified clean) |
| 4 Clip-quality | n/a (not a clip-scoring module) |
| 5 Anthropic SDK | n/a (LLM call lives in improvement/brief.py) |
| 6 Cleanliness & typing | 2 cleanup |
| 7 Error handling / API | 1 SEV1 (response_model gap, 18 endpoints) + 1 SEV2 (Form request models) |
| 8 Config & paths | ok |

## Module verdict
NEEDS-WORK — no BLOCKERs: per-creator isolation, token handling, and event-loop
offloading are all verified clean post-hardening; remaining work is the known-open
Issue-75 response_model coverage (18 endpoints) and pagination on unbounded list
endpoints.
