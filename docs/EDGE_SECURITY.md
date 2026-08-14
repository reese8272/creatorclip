# Edge Security — Cloudflare config as code (Issue 286)

> **This file is the committed source of truth for the Cloudflare edge configuration of
> `autoclip.studio`.** The zone is click-ops-free by policy: any change to WAF / rate-limiting /
> bot settings MUST be recorded here (exact expressions + thresholds) in the same PR that
> announces the change. Rationale + tier decision: `docs/DECISIONS.md` 2026-07-02 (Issue 286).

## Topology

All traffic reaches the origin through a Cloudflare Tunnel (`cloudflared` in
`docker-compose.prod.yml`; no open inbound ports — see `docs/ACCESS.md`), so **100% of requests
traverse the Cloudflare edge** and rate-limiting rules apply with no topology change.

## Plan constraint (the load-bearing fact)

Cloudflare **Free** allows exactly **1 rate-limiting rule**, matching on URI path only, with
IP-based counting (Pro = 2 rules; Business adds more fields). The beta therefore ships ONE
combined pre-auth rule. Upgrade trigger: observed abuse needing per-path thresholds
(e.g. separate login vs. probe limits) → Pro. The legacy `cloudflare_rate_limit` API/Terraform
resource is retired (2025-06-15) — any future Terraform must use `cloudflare_ruleset` with
`phase = "http_ratelimit"`.

## Rule 1 (the only Free-tier rule) — pre-auth abuse

| Field | Value |
|---|---|
| Name | `preauth-rate-limit` |
| Expression | `(starts_with(http.request.uri.path, "/auth/"))` |
| Counting | Same IP (Free-tier fixed) |
| Rate | **10 requests / 1 minute** per IP |
| Action | **Managed Challenge** (escalate to Block only after observing false-positive rate) |
| Duration | 1 minute |

Why `/auth/*`: it is the unauthenticated surface the app-level limiter structurally cannot
protect (slowapi keys on `creator_id`), and OAuth-callback flooding burns the shared YouTube
API quota. Why Managed Challenge first: the standard log → challenge → block progression;
a challenge stops bots without locking out a creator behind CGNAT.

**Normal-use headroom check:** a legitimate login = 2–3 `/auth/*` hits (login → callback).
10/min per IP is ~3 full login flows per minute per IP — generous for humans, hostile to loops.

## Rule 2 (WAF managed-rules exception) — Stripe webhook delivery

**Deployed 2026-08-14. This rule is load-bearing for revenue: without it, no purchase can ever
credit minutes.** See Issue 485.

| Field | Value |
|---|---|
| Name | `stripe-webhook-skip-waf` |
| Type | Managed-rules **exception** (Security rules → Create → Managed rules) |
| Expression | `(http.request.uri.path eq "/billing/webhook" and ip.src in {3.18.12.63 3.130.192.231 13.235.14.237 13.235.122.149 18.211.135.69 35.154.171.200 52.15.183.38 54.88.130.119 54.88.130.237 54.187.174.169 54.187.205.235 54.187.216.72 35.157.207.129 3.69.109.8 3.120.168.93})` |
| Action | **Skip all remaining rules** (WAF managed rulesets) |
| Placement | **First** — an exception must sit above the managed ruleset's `Execute` rule or it does nothing |
| Logging | `Log matching requests` ON |

**Why this is safe despite being a WAF skip.** The endpoint authenticates by **Stripe signature**
(`construct_webhook_event`, `routers/billing.py`), which is cryptographic and independent of the
edge. The IP list is only a scoping narrowing, not the security boundary. Skipping signature-verified
traffic from the vendor's own published IPs on one exact path costs nothing.

**What it fixes.** Cloudflare's **OWASP Core Ruleset** was blocking Stripe's webhook POSTs outright
— Stripe received a Cloudflare "Sorry, you have been blocked" page (Ray `a2acda78fc1e5509`, source
`54.187.205.235`, which is on Stripe's published webhook list). The payload contains nothing
malicious; this is the well-documented OWASP *anomaly-score* false positive on Stripe webhook JSON
(same failure Troy Hunt documented on Have I Been Pwned, resolved the same way — a path+IP
exception). Do **not** try to "fix" the payload or lower the paranoia level globally.

⚠️ **Bot Fight Mode was NOT the cause, despite being the intuitive suspect** (see below). Verified
via Security Events → the blocking service was the OWASP Core Ruleset. Worth stating because it
changes the fix entirely: managed rulesets run on the Ruleset Engine and **can** be skipped, whereas
Bot Fight Mode cannot (see the note below). Chasing Bot Fight Mode first is a dead end.

**Maintenance.** Stripe gives **7 days' notice** before changing webhook IPs via the
[api-announce list](https://groups.google.com/a/lists.stripe.com/g/api-announce). Re-check the list
at <https://docs.stripe.com/ips> (or `https://stripe.com/files/ips/ips_webhooks.txt`) at least
twice a year, and after any unexplained webhook failure.

## Pre-existing edge settings (do not regress)

- **Bot Fight Mode: ON** (Issue 144). It 403'd GitHub-hosted health checks once already —
  uptime probing uses **Cloudflare Health Checks** (edge-originated, exempt). Any new external
  monitor (e.g. Better Stack, Issue 282) must be verified against Bot Fight Mode before
  trusting its alerts, and `/health` must stay OUT of the rate-limit rule expression.
  ⚠️ **Bot Fight Mode cannot be skipped by a WAF custom rule or exception** — it does not run on
  the Ruleset Engine, so `Skip`/`Bypass`/`Allow` have no effect on it
  ([Cloudflare docs](https://developers.cloudflare.com/bots/get-started/bot-fight-mode/)). On the
  Free plan the only lever that exempts traffic from it is an **IP Access Rule** (Bot Fight Mode
  cannot trigger when one matches first); Super Bot Fight Mode (Pro) does support Skip. Remember
  this before designing any future allowlist against it — Rule 2 above works only because its
  target is a *managed ruleset*, not Bot Fight Mode.
- **Tunnel ingress**: hostname → `app:8000` mapping lives in the Zero Trust dashboard
  (`docs/ACCESS.md`).

## Apply (operator, ~5 min)

Dashboard: zone `autoclip.studio` → Security → WAF → Rate limiting rules → Create rule →
enter the table above verbatim. Or via API:

```bash
# List existing http_ratelimit ruleset (note the ruleset id):
curl -s -H "Authorization: Bearer $CF_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/rulesets?phase=http_ratelimit"

# Create the rule (entrypoint ruleset for the phase):
curl -s -X PUT -H "Authorization: Bearer $CF_API_TOKEN" -H "Content-Type: application/json" \
  "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/rulesets/phases/http_ratelimit/entrypoint" \
  -d '{
    "rules": [{
      "description": "preauth-rate-limit",
      "expression": "(starts_with(http.request.uri.path, \"/auth/\"))",
      "action": "managed_challenge",
      "ratelimit": {
        "characteristics": ["ip.src", "cf.colo.id"],
        "period": 60,
        "requests_per_period": 10,
        "mitigation_timeout": 60
      }
    }]
  }'
```

## Verify (external — the acceptance criterion)

From a non-allowlisted IP: `for i in $(seq 1 20); do curl -s -o /dev/null -w "%{http_code}\n" \
https://autoclip.studio/auth/login; done` → expect 200s flipping to a challenge/429 well before
20, **with the origin (`docker compose logs app`) showing no corresponding request flood** —
the block must happen at the edge. Record the transcript date here when run: ________

Sources (accessed 2026-07-02):
https://developers.cloudflare.com/waf/rate-limiting-rules/ ·
https://developers.cloudflare.com/terraform/additional-configurations/rate-limiting-rules/ ·
https://developers.cloudflare.com/waf/reference/legacy/old-rate-limiting/upgrade/
