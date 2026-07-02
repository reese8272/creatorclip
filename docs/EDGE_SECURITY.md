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

## Pre-existing edge settings (do not regress)

- **Bot Fight Mode: ON** (Issue 144). It 403'd GitHub-hosted health checks once already —
  uptime probing uses **Cloudflare Health Checks** (edge-originated, exempt). Any new external
  monitor (e.g. Better Stack, Issue 282) must be verified against Bot Fight Mode before
  trusting its alerts, and `/health` must stay OUT of the rate-limit rule expression.
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
