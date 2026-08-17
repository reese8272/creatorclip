# D07 — Security, tenancy, privacy, compliance

**Domain researcher output, Phase 1 of the deep standards audit. 2026-08-17.**
Read-only pass. Every claim below is anchored to a `file:line` on this tree (HEAD `1def133`)
or to a dated 2026 source. Two findings were reproduced empirically with `.venv/bin/python`.

---

## Verdict

The **tenancy** model is the strongest thing in this repo and I am not going to manufacture a
complaint about it — three layers, a required-argument GUC seam, and a test-pinned admin
allowlist put it above what most Series-A products ship. The **privacy/erasure** machinery is
likewise unusually thorough for an 82-day-old project: the R2 enumeration reaches orphans that
a prefix sweep cannot, and the backup-restore replay script exists. What is *not* in good shape
is **the availability-and-cost control plane**: the single most-documented "deliberate decision"
in this domain — that the rate limiter fails open — **is not what the code does**, and the
divergence turns a Redis blip into a silent total outage that the only continuous uptime signal
reports as green. That is instance #5 of the house failure mode, and it lives here.

---

## What the current standard is, with sources

**Fail-open vs fail-closed.** The 2026 consensus is *asymmetric by control type*, not uniform:
availability controls fail open, money/authorization controls fail closed. "For most systems,
fail-open is the right choice… however, financial systems processing payments might prefer to
reject rather than risk processing without rate limits"
([System Design Handbook, 2026](https://www.systemdesignhandbook.com/guides/design-a-rate-limiter/);
[Nerd Level Tech, *Fail-Open vs Fail-Closed Middleware: Hono + Redis (2026)*](https://nerdleveltech.com/fail-open-vs-fail-closed-hono-middleware-redis-tutorial)).
Both sources converge on the same implementation detail: the correct fail-open is **a local
in-memory token bucket plus a circuit breaker**, not "skip the check" — "configure an in-memory
`insuranceLimiter` so a store outage degrades gracefully"
([Level Up Coding, 2026](https://levelup.gitconnected.com/i-built-a-distributed-rate-limiter-from-scratch-in-node-js-heres-what-production-taught-me-e2a383976d4f)).
Note that the *inverse* bug is also a known-in-the-wild 2026 defect class —
[firecrawl#3728, "Rate limiter fails closed on rate-limit Redis outage — every request gets a 429"](https://github.com/firecrawl/firecrawl/issues/3728).
This repo has that bug, in the 500 flavour.

**Session revocation.** The 2026 standard is not "add a deny list"; it is **short access token
(5–15 min) + refresh token with server-side state**, so revocation means refusing to mint the
next access token ([OneUptime, *How to Handle JWT Revocation*, 2026-02-02](https://oneuptime.com/blog/post/2026-02-02-jwt-revocation/view);
[JSONCraft, *JWT Best Practices in 2026*](https://jsoncraft.dev/docs/jwt-best-practices-2026/);
[FusionAuth, *How to Manage JWT Expiration and Revoke JWTs*](https://fusionauth.io/articles/tokens/revoking-jwts)).
A deny list is explicitly the *fallback* when you cannot shorten the token.

**Secrets at this scale.** The 2026 comparison sets are consistent: for a single self-hosted VM
the two defensible answers are **SOPS/age** (file-based, git-committable ciphertext, no service to
run) or **Infisical** (self-hostable);
Doppler is ruled out if secrets must stay inside your own network
([GitGuardian, *Top 16 Secrets Management Tools for 2026*](https://blog.gitguardian.com/top-secrets-management-tools/);
[Bytebase, *Best Secrets Manager for Database Credentials in 2026*](https://www.bytebase.com/blog/best-secrets-manager-for-database-credentials/);
[Infisical, *Best Secrets Management Tools 2026*](https://infisical.com/blog/best-secret-management-tools)).
"Hand-edited `.env` on the box" is below all of them, but the gap that actually matters at 100
users is **versioning and drift detection**, not a vault.

**Postgres RLS multi-tenancy.** Direct-column `tenant_id` policies under `FORCE ROW LEVEL
SECURITY` with a non-BYPASSRLS app role is the current standard shape
([OneUptime, 2026-01-25](https://oneuptime.com/blog/post/2026-01-25-row-level-security-postgresql/view);
[Rico Fritzsche, *Mastering PostgreSQL RLS for Rock-Solid Multi-Tenancy*](https://ricofritzsche.me/mastering-postgresql-row-level-security-rls-for-rock-solid-multi-tenancy/)).
This project already implements it correctly.

**YouTube API Services Developer Policies** (fetched 2026-08-17,
<https://developers.google.com/youtube/terms/developer-policies>). Two clauses matter and the
project has one of them subtly backwards in its favour:
- **III.E.4.b** permits storing "data retrieved through the YouTube Analytics API service" and
  "statistics provided through other YouTube API services, such as the number of views for a
  video" **indefinitely** — *provided* the client verifies every 30 days that authorization has
  not been revoked. The 30-day clock is a **re-verification** obligation, not a hard TTL.
- **III.D.2.3** on revocation: "delete all API Data related to that user… your API Clients will
  need to periodically reconfirm that its authorization tokens are still valid and delete API
  Data associated with users whose authorization tokens cannot be refreshed."
- **III.E.4.h**: clients must not "access or use API Data to create new or derived data or
  metrics."

---

## Findings

### F1 — The rate limiter fails **CLOSED with an unhandled HTTP 500**, not open. The decision on record is the opposite of the code. `[HIGH]`

**Evidence:** `limiter.py:129-133` (no `swallow_errors`, no `in_memory_fallback`),
`main.py:145-147`, `docs/DECISIONS.md:2634-2636`, `limiter.py:42-44`, `limiter.py:148-151`,
`docs/AUDIT_BRIEF.md` §5 row 1.

`slowapi==0.1.9` `Limiter.__init__` defaults `swallow_errors=False` and
`in_memory_fallback_enabled=False`. `extension.py:631-646` re-raises any non-`RateLimitExceeded`
exception from `_check_request_limit` when neither is configured. Every rate-limited route in
this app is *decorator*-limited, and `middleware.py:_should_exempt` hands decorated routes back
to the decorator, so the decorator path is the live one — and it raises.

Reproduced against a dead Redis with the exact construction args from `limiter.py`:

```
status: 500
endpoint body executed (LLM would have been called)? False
```

**Failure scenario:** Redis on the prod VM stalls for >100 ms (`socket_timeout=0.1`,
`limiter.py:83-86` — a BGSAVE fork, an AOF rewrite, or CPU starvation from the two concurrent
ffmpeg/MediaPipe encodes on the same box is sufficient; Redis need not be *down*). Every
decorated route raises `redis.exceptions.ConnectionError`/`TimeoutError` → FastAPI 500. That
includes `GET /auth/me` (`routers/auth.py:401`, `120/minute`), which the SPA's `AuthGate` calls
on every load, and the OAuth callback (`20/minute`). **Result: nobody can sign in and nobody
who is signed in can use the product** — from a Redis latency spike, not a Redis outage.

**Why this is a real finding and not pedantry:** `docs/DECISIONS.md:2634` records the accepted
risk as *"a Redis stall degrades to fail-open, so the ceiling is momentarily unenforced…
accepted and consistent with every other limit in `limiter.py`."* Every other Redis touchpoint
in the repo genuinely does fail open (`DECISIONS.md:6842` OAuth refresh lock,
`:7522`/`:7589` the `aset_owner` sites, `flags.py`, `billing/spend_guard.py:373-375`). The
limiter is the one place where the house posture was *documented* and *not implemented*, and it
is the one place where the consequence is a total outage. `limiter.py` carries a **99 % coverage
floor** (`run_layer0.py:334`) — the joint-tightest in the repo — and `tests/test_rate_limiting.py`
has **no test whatsoever** for Redis-unavailable behaviour (21 tests: attachment, key funcs,
limit-string introspection, one 429). 99 % of lines, 0 % of the property the module's 50-line
docstring is about.

**Verdict:** `deviation-unjustified` — argued directly against `DECISIONS.md:2634`.

---

### F2 — `/health` returns **200 `"degraded"`** when Redis is down; the runbook says it returns 503. Combined with F1, a total outage presents as green. `[HIGH]`

**Evidence:** `main.py:553-567`; `docs/DEPLOYMENT.md:162-163`; `docker-compose.prod.yml:22-30`.

`health()` gathers three probes and returns `{"status": "degraded" | "ok", …}` — always with the
default **200**. `docs/DEPLOYMENT.md:162-163` states: *"the `/health` endpoint already returns
`{"status":"ok",…}` (degrades to non-`ok` **+ 503** when a backing service is down)."* It does
not. The compose healthcheck comment (`docker-compose.prod.yml:23-24`) then *deliberately*
treats degraded as alive: *"Liveness only: /health returns 200 even when a dependency is
degraded, so this restarts the app only when it stops serving."* Both statements cannot be the
basis of the same monitoring design.

**Failure scenario:** Redis degrades → every user-facing route 500s (F1) → `/health` answers
`200 {"status":"degraded","redis":"error"}` → the Docker healthcheck is satisfied, `autoheal`
does not fire → the Cloudflare Health Check, the *only continuous uptime signal in production*
(`process-map.md` §6), sees 200 and stays green. There are **zero alert rules anywhere in the
repo** (`process-map.md` §6), so nothing else fires either. This is the exact shape of the
9-day silent outage already logged at `OFF_COURSE_BUGS.md:104`.

**The one thing that could save it is unverifiable from this repo:** `DEPLOYMENT.md:156` step 3
tells the operator to enable a **response-body match on `"status":"ok"`** in the Cloudflare
dashboard. If that box is ticked, the outage *is* caught. But it is a manual dashboard step with
no config-as-code, and the sentence six lines below tells the operator the endpoint returns 503
anyway — which makes the body match read as belt-and-braces rather than the sole mechanism.
An operator who trusts the doc will not treat step 3 as load-bearing.

**Verdict:** `gap`. **This is my nomination for instance #5** of the `AUDIT_BRIEF.md` §6 failure
mode: an intermediate layer (`/health`) reports success without exercising the thing it claims
to verify (that the app can serve a request).

---

### F3 — The Redis client used by the spend guard has **no socket timeout**. A Redis *stall* hangs `require_budget` forever; "fail open on error" never fires, because a hang is not an error. `[HIGH]`

**Evidence:** `youtube/_redis.py:32` — `redis.from_url(settings.REDIS_URL, decode_responses=True)`,
no `socket_timeout`, no `socket_connect_timeout`. Consumers:
`billing/spend_guard.py:237` (`_record_and_enforce`), `:365` (`creator_block_status`, reached
from `require_budget`, a `Depends` on **every** LLM route), plus `youtube/oauth.py`'s refresh
lock and `youtube/quota.py`.

Issue 312 (`limiter.py:18-49`) diagnosed precisely this class — an unbounded Redis round-trip
blocking the event loop — and fixed it for *one* of the two Redis clients in the process. The
fix was never propagated to the other, and no test or gate pins the invariant
(`tests/test_rate_limiting.py:278 test_limiter_storage_has_bounded_socket_timeout` covers the
slowapi client only).

**Failure scenario:** Redis is reachable but slow (same triggers as F1). `require_budget` awaits
`r.ttl(...)` with no deadline. `uvicorn --workers 2` (`docker-compose.prod.yml:14`); each hung
request also holds a checked-out DB session. The `except Exception → fail open` arm at
`spend_guard.py:373-375` is never reached because nothing raises. The API goes unresponsive with
no error, no log line, and no 500 — a worse signal than F1, and again a green `/health`.

**Verdict:** `gap`.

---

### F4 — Two tenant tables have a direct `creator_id` and **no RLS policy and no recorded exemption**; the tenant-table list is a hand-maintained tuple in a test file. `[MEDIUM]`

**Evidence:** `models.py:279-318` (`creator_api_keys`), `models.py:590-620` (`creator_identity`)
— both carry `creator_id … ForeignKey("creators.id", ondelete="CASCADE")`; neither appears in
any `CREATE POLICY tenant_isolation ON …` or in migration 0010/0038/0040/0044/0045's table
loops. Compare `models.py:1671-1673` (`notification_preferences`), `:1723` (
`notification_deliveries`) and `COMPLIANCE.md:108` (`event_logs`), each of which carries an
explicit *"this table does NOT have its own RLS policy, because…"* docstring. These two carry
nothing.

`creator_api_keys` has a legitimate structural reason (the bearer-key lookup at
`api_key.py:111-119` runs pre-auth with no GUC, exactly like `creators`) — but that reason is
written down nowhere, so the next reader cannot tell an exemption from an omission.
`creator_identity` has no such reason: every access in `dna/identity.py:25-98` already filters
by `creator_id`, so it would sit behind a standard policy unchanged. It holds
`mission`, `audience_summary`, `style_sample`, `hard_nos` — creator-authored free text that is
injected into LLM prompts.

**The mechanism gap is the real finding.** `tests/test_rls_isolation_integration.py:265-284` is
a **hardcoded 17-name tuple**; `_CHILD_TABLES` at `:579-586` is a hardcoded 6-name tuple.
Nothing in `tests/` queries `pg_policies` or `pg_class.relrowsecurity` (grep: zero hits
repo-wide). This is the third recurrence of one bug: `OFF_COURSE_BUGS.md:46` (two tenant tables
never got a policy, found by a manual sweep on 2026-06-30) and `:25` (the guard test written to
prevent that iterated a hardcoded `("clips","signals")` and passed vacuously on 15 of 17
tables). Both times the correct structural fix — *derive the list from the catalog* — was
identified and not made.

**Failure scenario:** any future `routers/*.py` handler that reads `CreatorIdentity` by primary
key rather than by `creator_id` (the pattern `routers/_owned.py` exists to prevent, and which
`dna/identity.py:98` already uses for the supersede `UPDATE … WHERE CreatorIdentity.id ==
current.id`) returns another creator's identity profile. RLS — bought precisely so "even when
the application forgets `WHERE creator_id`, the database refuses" — is not there to stop it, and
the integration suite reports green because the table is not in the tuple.

**Verdict:** `gap`.

---

### F5 — The 30-day YouTube retention purge is implemented exactly as documented for four tables, and **silently leaves a verbatim frozen copy of the same analytics in `creator_dna`, which is never deleted.** `[MEDIUM]`

**Evidence:** `worker/tasks.py:4321-4410` purges `video_metrics`, `retention_curves`,
`audience_activity`, `demographics` on `fetched_at < now() - 30d` — this matches
`docs/COMPLIANCE.md:19-40` claim-for-claim, including the advisory lock, the explicit
`RetentionCurve` cascade, and the daily beat entry (`worker/schedule.py:59`). **The documented
control is real.** But:

- `dna/builder.py:229-235` and `:331-345` write `views`, `engagement_rate`,
  `avg_view_duration_s`, `retention_spike_times`, `top_avg_views`, `top_avg_engagement_rate`
  into `patterns_jsonb` — verbatim YouTube Analytics values, per video, top-10 and bottom-10.
- `dna/profile.py:5` — *"DNA profiles are versioned and **never deleted**: draft → confirmed →
  superseded."* There is no DNA entry in `worker/schedule.py`'s beat schedule, no staleness
  column on `creator_dna`, and the purge task explicitly scopes itself to four tables.
- `docs/COMPLIANCE.md:105` classifies the whole of Creator DNA as *"Creator-owned derivative
  data / Until creator deletes"* — one table row, no analysis.

**The exposure is narrower than it first looks, and I want to be precise about that.** Per
III.E.4.b as fetched today, Analytics data may be retained **indefinitely** so long as
authorization is re-verified every 30 days — so for an *active* creator, the frozen DNA copy is
fine and the project's own purge is stricter than required. The gap is exactly the case the
purge task was built for: **a creator whose token is revoked or expires.** `refresh_youtube_analytics`
stops advancing `fetched_at`, the four tables are purged at day 30 — and `creator_dna` keeps
their view counts, engagement rates and retention-spike timestamps forever, alongside
`dna_embeddings` derived from the brief. III.D.2.3 requires deleting *all* API Data for that
user. (III.E.4.h — "must not use API Data to create new or derived data or metrics" — is a
second, larger question about the DNA product concept that is out of scope here and belongs to
whoever owns the Google OAuth verification, Issue 29.)

**Failure scenario:** a beta creator disconnects their channel at
myaccount.google.com. Day 30: the four analytics tables are purged and the log line reads
`Purged stale YouTube analytics — metrics=N …`. `creator_dna.patterns_jsonb` still contains
their per-video view counts. A Google API compliance audit — which
`COMPLIANCE.md:47-49` notes is *triggered by the quota-extension request the project intends to
make* — reads the retention claim, then reads the table.

**Verdict:** `gap`. Cheapest honest fix: extend the purge task to null `patterns_jsonb`'s
analytics-derived keys (or delete the DNA row) for creators with no live token past the cutoff;
or add one COMPLIANCE.md paragraph arguing the copy is aggregated-and-anonymised. Either is
defensible; the absence of both is not.

---

### F6 — Erasure throws away the purge result: a refused R2 delete leaves media alive with no DB pointer, unreachable by any later sweep. This is the concrete answer to "what survives deletion." `[MEDIUM]`

**Evidence:** `worker/erasure.py:177-199` — `purge_uris` returns the set of URIs it *succeeded*
in deleting, and its docstring says explicitly: *"the caller must keep the DB pointer for any
URI NOT in the returned set so a later sweep or a repeated erase can retry it."*
`routers/auth.py:513-520` calls it, logs `len(purged)/len(uris)`, **discards the return value**,
and proceeds to `session.delete(creator)` + `commit()` regardless.

The docstring names its own trigger: *"Object-Lock refusal on `clips/` per Issue 258."*
`COMPLIANCE.md:100` confirms `clips/` carries an R2 Object Lock window and that the intended
posture is *"a refused delete is logged, the DB pointer is kept, and the retention sweep
retries."* On the account-deletion path the pointer is **not** kept — the row is gone.

**Failure scenario:** a creator with a clip rendered inside the Object Lock window calls
`DELETE /auth/me`. The R2 delete for `clips/{clip_id}.mp4` returns 403. `purge_uris` logs a
warning and omits it. The `clips` row cascades away. `append_audit(action="creator.deleted")`
is written. The MP4 — the creator's face and voice — now lives in R2 with no row referencing it,
no prefix that would catch it (`clips/` is deliberately non-creator-scoped,
`COMPLIANCE.md:100`), and `scripts/reapply_erasures.py` cannot help because it replays
`erase_creator` for *resurrected creator rows*, and this creator was never resurrected. Nothing
in the repo will ever find that object again. The endpoint returned 204.

**What else survives erasure, for completeness** (each of these is *correctly* handled and
documented — listing them so the answer is complete, not to file them):
`audit_log` row (deliberate, UUID only, no PII — `routers/auth.py:568-575`, Issue 247);
encrypted `pg_dump` backups for ≤56 days with a mandatory `reapply_erasures.py` on restore
(`COMPLIANCE.md:104`, Issue 254); Stripe-side payment records (not addressed anywhere in
`COMPLIANCE.md` — legitimate under a legal-obligation basis, but unstated);
Redis spend/rate counters (TTL-bounded, ≤2 days); Sentry (`send_default_pii=False` + a
`before_send` scrubber, `observability.py:611-676`). The DB cascade itself is **complete** — I
checked all 28 `creator_id` foreign keys in `models.py` and every one is
`ondelete="CASCADE"`, and `event_logs` (the one table with no FK) is purged explicitly at
`routers/auth.py:552-560`.

**Verdict:** `gap`.

---

## Answers to the six questions I was asked

**1. Blast radius of the fail-open decisions, and the middle option.**
The premise is wrong in a way that matters: **the limiter does not fail open** (F1). A pure
Redis outage therefore costs **$0 in LLM spend** — requests 500 before reaching Anthropic — and
costs **100 % of availability** instead. The two halves of the recorded risk are not
simultaneous; they are inverted.

The residual money exposure is the *partial* case (Redis healthy enough for slowapi's single
`INCR`, unhealthy or slow for the spend guard's `EVAL`+`MGET`, or F3's hang). With the guard
disarmed the binding constraint becomes the daily job caps: `LLM_DAILY_JOB_LIMIT=50` and
`RENDER_DAILY_JOB_LIMIT=60` per creator (`config.py:210,220`). At the repo's own price book
(`config.py:164-165`, Opus $5/$25 per MTok) a `generate_clips` run on a 20-minute source is
roughly $0.50–1.00 all-in across the Opus scoring + video-context + clip-metadata calls plus
Deepgram at $0.0097/min. **50 jobs/day ≈ $25–50 per creator per day, against a
`SPEND_CAP_CREATOR_DAILY_USD` of $5.00** (`config.py:974`). So the guard is worth ~$20–45 per
active creator per day, and the global $50/day and $400/month ceilings
(`config.py:977-978`) are what actually stand between a runaway loop and a four-figure bill.
The dangerous property is not the size of the number — it is that
`_warn_fail_open` (`spend_guard.py:112-119`) emits **one log line per process** and the repo
contains **zero alert rules**, so a disarmed cost cap is indistinguishable from a working one.

**The middle option exists and is two lines.** It is exactly the asymmetry the 2026 sources
describe:
- *Limiter → genuinely fail open, with a local bucket.* `Limiter(..., in_memory_fallback_enabled=True)`
  gives precisely the "local token bucket fallback" the question asks about — slowapi already
  implements the `_storage_dead` circuit-breaker latch (`extension.py:634-640`); it is inert only
  because the flag is off. This turns F1 from a 500 into per-process rate limiting, which at
  `--workers 2` is a 2× effective limit — an entirely acceptable degradation.
- *Spend guard → fail CLOSED.* Once the limiter genuinely fails open, the guard is the **only**
  thing between a bug and an unbounded Anthropic bill. A 429 on `/clips/generate` is a degraded
  feature with honest copy already written (`spend_guard.py:98-102`); an unbounded bill at a
  100-user beta with no alerting is not recoverable. Note the guard's own kill-switch path
  already fails *closed* in spirit — `ensure_within_budget` consults `llm_generation`, a **DB**
  flag, which survives a Redis outage.

**2. Non-revocable JWTs.** The recorded reasoning at `auth.py:67-83` is *correct on the
mechanism and wrong on the standard it cites*. It rejects a Redis `jti` deny-list on the
grounds that it would make auth hard-depend on Redis — a good instinct that F1 vindicates. But
the 2026 standard was never "add a deny list"; it is **short access token + server-side refresh
token**, with the deny list as the fallback for people who cannot shorten the token
([OneUptime 2026](https://oneuptime.com/blog/post/2026-02-02-jwt-revocation/view),
[FusionAuth](https://fusionauth.io/articles/tokens/revoking-jwts)). The comment even reaches the
right conclusion — *"for higher-assurance revocation, issue a shorter-lived token"* — and then
leaves `JWT_EXPIRY_MINUTES` at 60. **At a ≤100-user private beta with no admin-privilege
escalation path, the 60-minute window is a defensible call and I would leave it.** What is not
defensible is that it is undefended in one place it matters: `JWT_SECRET_KEY` has **no rotation
support** (single key, `config.py:85`, `auth.py:97-103`) while `TOKEN_ENCRYPTION_KEY` has a
full MultiFernet rotation runbook. If the JWT signing key leaks, every session for every creator
is forgeable and the only remedy is a hard secret swap that logs everyone out — with no runbook,
and the key living hand-edited in `/opt/autoclip/.env`. Adding a `kid` header and a
`JWT_SECRET_KEY_PREVIOUS` accepted-key list is ~10 lines and mirrors the pattern `crypto.py`
already establishes. That is the cheap win, not revocation.

**3. Does right-to-erasure complete? Mostly yes.** See F6 for the one thing that genuinely
escapes (Object-Lock-refused renders) and for the full survivor list. Two corrections to the
brief's own low-confidence framing: the DB cascade is **complete** (28/28 `creator_id` FKs are
`ondelete="CASCADE"`), and the `clips/` key-namespace worry named in
`AUDIT_KNOWN_ISSUES.md` §F is **closed** — `worker/erasure.py:83-101` unions the DB pointers
with three deterministically constructed keys per clip, which reaches the clean-confirm orphans
that have no row pointing at them. That is a genuinely good design and `tests/test_erasure_keys.py`
pins each pattern. I also confirmed the §F ask on the data export: `_collect_creator_export`
(`worker/tasks.py:5153-5230`) does **not** touch `youtube_tokens` or `creator_api_keys`, and
`Creator` carries no secret columns — so it is clean today. There is no structural test pinning
that, and `_row_to_dict` (`:5148-5150`) blindly serializes every column of whatever model it is
handed, so it is a latent trap of the same shape as the Stripe `amount_total` gap already filed.

**4. The one-tenant-one-user bet — cost, and should it be decided now?**
**The 27 RLS policies are the cheap part.** They are uniform
(`creator_id = NULLIF(current_setting('app.creator_id', true),'')::uuid`), and rewriting them to
`creator_id IN (SELECT tenant_id FROM memberships WHERE user_id = …)` is one mechanical
migration over a generated list — genuinely a day's work, and the seam
(`db.py::tenant_session`, one GUC, one `after_begin` listener) does not change shape at all.

**The expensive part is that `creators` is simultaneously the tenant and the principal.** It
carries the login identity (`email`, `channel_id`), the OAuth grant
(`youtube_tokens.creator_id`), the consent + COPPA attestation, the billing balance
(`minute_packs`, `usage`), *and* it is the FK target of all 28 tenant tables. The session JWT's
`sub` claim is that same id (`auth.py:86`), and it is used interchangeably as
"who is acting" (`request.state.creator_id`, the audit `actor`) and "whose data is this"
(the RLS GUC, `routers/_owned.py`). Splitting them later touches auth, the JWT contract, every
issued cookie, the rate-limit key (`limiter.py:110-126` — one editor would exhaust the whole
team's per-creator quota), the spend-guard keys, the erasure path (does removing an editor erase
the tenant?), and the Art. 15 export scope. That is not a weekend.

**Yes, decide it now — because the cheap half of the decision expires.** Two actions, both
essentially free today and both expensive after the first paying cohort:
(a) write the one-paragraph entry saying "one creator = one login; no team seam in v1, revisit
at N", so the next refactor argues against a position rather than a vacuum; and (b) **add a
distinct `tid` claim alongside `sub` in `create_session_token` now** and read the GUC from `tid`
everywhere. Today `tid == sub`, so it is a no-op — but it converts an *implicit* conflation into
an *explicit* one, and it means the day a team seam is wanted, every already-issued token and
every call site already distinguishes principal from tenant. Roughly 10 lines.

**5. Secret handling.** The application-secret story is genuinely good and above standard for
this scale: `crypto.py` is 55 clean lines, MultiFernet with a primary+previous key, an
`lru_cache` singleton with a documented cache-clear contract for tests, `TokenDecryptError`
carrying no ciphertext, a field validator that rejects a malformed `TOKEN_ENCRYPTION_KEY_PREVIOUS`
before it can silently break a live rotation (`config.py:1059-1075`), a zero-downtime rotation
script and runbook, and a ≥32-byte floor on `JWT_SECRET_KEY` (`config.py:1042-1055`). The
committed-looking `.env` at repo root is **untracked and gitignored** (`git check-ignore -v .env`
→ `.gitignore:2`) — not a finding, and worth saying so since the process map flagged it.

The **infra**-secret story is the gap, and at this scale the right diagnosis is *versioning and
drift detection*, not a vault. `deploy.yml:180-182` states `DATABASE_URL`, `REDIS_URL`,
`JWT_SECRET_KEY`, `TOKEN_ENCRYPTION_KEY` and the OAuth creds "remain VM-managed and are
intentionally not listed here" — they exist in exactly one place, `/opt/autoclip/.env`, edited
by hand, with no history. A hand-edit typo on `TOKEN_ENCRYPTION_KEY` is unrecoverable: every
stored OAuth refresh token becomes undecryptable and every creator must re-authorize. Per the
2026 comparisons above, **SOPS + age** is the proportionate answer here — encrypted
`ops/secrets.prod.env` committed to the repo, decrypted at deploy time, no service to run, git
history as the versioning and diff mechanism. Infisical is the answer if a UI is wanted;
Doppler is ruled out by the "stays on my box" preference the VM deployment already expresses.
This is a judgement call and "not yet, at 100 users" is a legitimate answer — but the
**escrow** half is not optional and is already filed (Issue 255).

**6. YouTube ToS / 30-day purge — is it really implemented as documented?** Yes, for what it
claims to cover, and I verified it line by line (F5). The implementation is *stricter* than
III.E.4.b requires. The gaps are (i) the frozen DNA copy (F5), and (ii) a small doc-drift
artifact: `COMPLIANCE.md:93-96` still lists Video metrics / Retention curves / Audience activity
/ Demographics with retention *"Refresh per YouTube policy — Refresh cadence TBD — confirm from
ToS"*, four rows that section 2 of the same file (`:19-40`) resolved on 2026-05-31. The
authoritative statement and the summary table disagree inside one document.

---

## What is genuinely right here — specifically

1. **`db.py::tenant_session(creator_id)` makes the GUC a required argument.** This is the
   correct shape of the control: the *type system* enforces what convention cannot. Paired with
   `AdminSessionLocal` for cross-tenant sweeps and an allowlist pinned by
   `tests/test_worker_invariants.py:172`, it means a new worker task cannot silently acquire
   cross-tenant reach — it has to add itself to a list that a test guards. I have not seen a
   better version of this in a codebase this young.
2. **`routers/_owned.py`** — 47 lines, one generic `get_owned()`, one query, 404 for both
   missing and foreign. The right primitive, and it is documented as defence-in-depth *under*
   RLS rather than as the isolation mechanism.
3. **Migration 0045's `NULLIF` hardening.** The bare `::uuid` cast on an empty-string GUC threw
   on pooled connections and 500'd instead of denying (`OFF_COURSE_BUGS.md:90`). The fix
   converts a crash into a deny, which is the correct direction, and the incident is written
   into the migration.
4. **`worker/erasure.py`'s enumeration design** — DB pointers ∪ deterministically constructed
   keys, with the reason (the clean-confirm swap orphans a render with no row pointing at it)
   written at the top of the file and each pattern pinned by `tests/test_erasure_keys.py`. Most
   teams prefix-sweep and never learn what they missed.
5. **`routers/auth.py:568-575`** — the deletion audit row stores the creator UUID and
   deliberately *not* the email or channel id, with the reasoning (writing PII into a
   never-purged RLS-exempt table would let erased data survive the erasure) inline. That is a
   subtlety many mature products get wrong.
6. **`scripts/reapply_erasures.py`** — an idempotent replay of the erasure cascade against the
   audit trail after a backup restore, made mandatory in the DR runbook. The honest ~56-day
   backup ceiling is stated in `COMPLIANCE.md:104` rather than glossed.

Two more worth naming because they defuse plausible findings: Redis persistence is properly
configured (`docker-compose.prod.yml:118` — AOF `everysec` + RDB `--save 300 100`), so the spend
counters survive a restart rather than silently resetting the caps to zero; and the
per-creator spend breach path is scoped so that **one creator can never trip the global kill
switch** (`spend_guard.py:299-317`) — a distinction most implementations miss.

---

## Decisions this domain needs but does not have

1. **A single fail-open/fail-closed posture table.** Which Redis-dependent controls fail open,
   which fail closed, and *why*, in one place. The posture is currently asserted in five
   docstrings, one DECISIONS entry, and contradicted by the code in the one case that matters.
2. **"One creator = one login" as an explicit v1 bet**, with the `tid`-claim seam taken now
   (see Q4). Currently the largest structural bet in the tenancy model with zero recorded
   position.
3. **What `/health` means.** Liveness, readiness, or dependency status — it is currently all
   three, and the compose healthcheck and the Cloudflare runbook read it two different ways.
   Standard answer: split `/health` (liveness, always 200 if serving) from `/ready`
   (dependency-gated, 503 when degraded) and point the uptime monitor at `/ready`.
4. **RLS coverage as a derived property, not a list.** A test that reads
   `information_schema.columns` for `creator_id` and asserts a matching `pg_policies` row +
   `relforcerowsecurity`. This converts the three-time-recurring Class-7 bug into an impossible
   one, and it is the mechanism the project has twice identified and twice not built.
5. **JWT signing-key rotation** — a `kid` header + accepted-key list, mirroring `crypto.py`'s
   MultiFernet pattern. Currently the only credential whose compromise has no graceful remedy.
6. **Derived-data retention policy under the YouTube ToS** — a written position on whether
   Creator DNA, `dna_embeddings`, `video_insights` and `videos.title/published_at/duration_s`
   for `origin=youtube` rows are inside or outside the III.E.4/III.D.2.3 perimeter. This is the
   question a Google API compliance audit will ask, and the quota extension the project needs
   (`COMPLIANCE.md:47-49`) triggers that audit.
7. **A secrets-of-record decision** — SOPS/age vs Infisical vs "hand-edited `.env` is fine at
   100 users, revisit at N". Any of the three is defensible; none is written down.
</content>
</invoke>
