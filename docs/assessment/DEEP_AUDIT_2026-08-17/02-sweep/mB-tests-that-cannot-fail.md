# Modality B — Tests that cannot fail

**Swept:** 260 test modules / 3,068 `def test_*` / 3,234 gating tests, plus the 6 frontend
structural gates and the scripts that back CI gates.
**Method:** pattern-first, not file-first. Four AST sweeps over `tests/` + `scripts/` +
`.claude/skills/production-assessment/scripts/`, then a **measured diff** of every module-level
scope literal against what a live scan of the repo produces right now. Everything was run with
`.venv/bin/python` (fastapi 0.137.1) or the repo's own `npx vitest` / `npx vite build`.

**Honest yield:** the "always-true assertion" and "empty iteration" sweeps came back almost
clean — 1 `assert True` (in a self-test *about* the quarantine marker), 1 `except: pass` (in
conftest, deliberate), 0 parametrize-over-empty. **That is a real and creditable result.** The
yield is concentrated entirely in **one shape: the hand-maintained literal registry**. I found
**101** module-level list/tuple/set/dict literals in `tests/` + `scripts/`; I diffed the ~20 that
define a gate's *scope* against ground truth. **Eleven had drifted, and three of those drifts are
covering a live defect today.**

Headline: **three gates are green over defects that are on `main` right now** —
a cost-safety cap that does not exist on the most expensive LLM route in the app, two Tailwind
utilities that render nothing, and eight RLS-policied tenant tables that the RLS regression test
has never touched.

---

## Sweep 1 — always-true assertions / empty iteration (near-clean)

AST scan of every `ast.Assert` in `tests/`:

| shape | hits |
|---|---|
| `assert <constant>` | 1 (`tests/test_quarantine_marker.py:88` — deliberate, inside a test that *asserts a marker is registered*) |
| `except: pass` swallowing a failure | 1 (`tests/conftest.py:81` — deliberate service probe) |
| `assert isinstance(...)` on a freshly-constructed object | 32 (all paired with real value assertions; spot-checked 8, none vacuous) |
| `@pytest.mark.parametrize` over an empty or possibly-empty list | **0** |

Dynamic parametrize sources are all floor-guarded: `_load_scenarios()` has
`test_eval_scenario_count_floor` + a second pin in `tests/test_eval_transparency.py:103`;
`_scenario_files()` is cross-checked against the landing page. **No `all([])` instances found.**
The `drill_rate_limit` class of bug (OFF_COURSE_BUGS `:154`) does not recur inside `tests/`.

One scope note on the eval guards: `_load_scenarios()` and the skip-marker scan both glob
`tests/eval/scenarios/*.yaml` **non-recursively**, so the 2 YAML files under
`tests/eval/scenarios/ranking/` are outside both the count floor and the skip-marker scan. Not
worth an issue on its own; noted for whoever hardens the harness next.

---

## Sweep 2 — the literal-registry census (where all the yield is)

101 module-level literals. The ones that define **what gets checked** and their measured drift:

| Registry | file:line | listed | ground truth | drift | live defect? |
|---|---|---|---|---|---|
| `_LLM_ROUTES` + `_RENDER_ROUTES` | `tests/test_creator_quota.py:30,41` | 9 + 4 | 17 LLM-flagged routes | **8 unchecked** | **YES — 4 violate the invariant** |
| `SUSPECT` | `frontend/src/test/design-tokens.contract.test.ts:68` | 17 names | any undeclared `--color-*` | open-ended | **YES — 2 tokens, 3 call sites** |
| `_TENANT_TABLES` | `tests/test_rls_isolation_integration.py:265` | 17 | 26 policied tables | **8 unchecked** | latent (policies exist) |
| `_BILLED_LLM_ROUTES` | `tests/test_flags.py:410` | 10 | 17 | **7 unchecked** | latent (all gated today) |
| `_LLM_RENDER_ROUTERS` | `tests/test_security_baselines.py:304` | 6 routers | 8 routers with billed writes | **2 unchecked** | latent |
| `_ADMIN_SESSION_ALLOWLIST` scope | `tests/test_worker_invariants.py:197` | `worker/tasks.py` only | 5 sites in 4 other modules | **4 modules** | latent |
| `_LLM_MODULES` | `tests/test_llm_metrics_coverage.py:24` | 13 | 17 call-site modules | **4 unchecked** | latent |
| `_TASK_MODEL_KEYS` | `tests/test_model_config.py:15` | 14 | 17 in `config.py` | **3 unchecked** | latent |
| `_BUILDERS` | `tests/test_grounding_honesty.py:26` | 5 | 7 named in its own docstring | **2 unchecked** | latent |
| `WHITELIST` scan scope | `tests/test_compliance_no_virality.py` | `static/` (5 files) | + 260-file React SPA | **SPA unscanned** | see B5 |
| `copies` | `tests/test_principles_registry.py:28` | 4 sites | 3 code + 1 test — **correct today** | none | no |

Clean (verified, no drift): `_ANTHROPIC_CALL_SITES` (backed by a real AST sweep, bidirectional
staleness check), `_GOLDEN_NAMES` (schema-hash + model pins), `_ADMIN_SESSION_ALLOWLIST` contents
(bidirectional), `_SENSITIVE_KEYS` / `_FULL_BLOCKLIST_KEYS`.

---

# Candidates

---

## B1 (HIGH) — the per-creator daily quota gate is a 9-item list; 4 of the 17 LLM routes it claims to cover are uncapped, and the most expensive one has NO daily cap at all

**Evidence:** `tests/test_creator_quota.py:30` (`_LLM_ROUTES`), `:41` (`_RENDER_ROUTES`),
`:110` (`test_every_llm_route_has_daily_cap_stacked_on_hourly`).

**Claims to verify** — module docstring, `tests/test_creator_quota.py:1-12`:
> *"Issue 228 — per-creator pre-job daily quota on **every** LLM/render endpoint… Every LLM/render
> handler carries a STACKED '/day' limit alongside its existing hourly burst limit."*

This is the test behind the ✅ in `CLAUDE.md` → Pre-Public-Launch Requirements:
*"✅ Per-creator rate limiting + usage quotas before each LLM/render job — shipped via Issues 228."*

**Why it does not verify that:** the test iterates a 9-name literal. Resolving the real route
table (FastAPI 0.137 `_IncludedRouter`-aware) and reading `limiter._route_limits` gives **17**
routes carrying `Depends(require_flag("llm_generation"))`. Eight are outside the list. Four of
those eight **fail the stated invariant**:

```
POST /api/chat/messages                    routers.chat.post_message      ['25 per 1 day']      <- no burst cap
POST /api/chat/.../regenerate              routers.chat.regenerate        ['25 per 1 day']      <- no burst cap
POST /creators/me/identity/chat            routers.creators.identity_chat ['40 per 1 hour']     <- no daily cap
POST /creators/me/dna/build                routers.creators.build_dna     ['120 per 1 minute']  <- no daily cap, 120/min burst
```

`routers/creators.py:541-547` is the sharpest: the decorator comment says
*"The DNA build fires a Sonnet call via dna/brief.py, so it belongs behind the same kill switch
and spend gate as every other LLM route"* — the kill switch and `require_budget` were added, and
the pre-existing `@limiter.limit("120/minute")` was never replaced with the stacked
`LLM_DAILY_LIMIT`. The daily ceiling Issue 228 exists to impose is absent, and the burst cap is
120/minute on a full DNA-build enqueue.

`_RENDER_ROUTES` has the same shape: `routers.review.trim_render` and `routers.clips.create_clip`
are budget-gated render routes outside the 4-item list (both happen to be correctly stacked).

**Repro:**
```bash
PYTHONPATH=. .venv/bin/python - <<'PY'
import os; from cryptography.fernet import Fernet
for k,v in {"ANTHROPIC_API_KEY":"x","DATABASE_URL":"postgresql+psycopg://c:d@localhost:5432/c",
 "REDIS_URL":"redis://localhost:6379/0","GOOGLE_OAUTH_CLIENT_ID":"x","GOOGLE_OAUTH_CLIENT_SECRET":"x",
 "OAUTH_REDIRECT_URI":"http://localhost:8000/auth/callback","TOKEN_ENCRYPTION_KEY":Fernet.generate_key().decode(),
 "JWT_SECRET_KEY":"test-jwt-secret-32-bytes-minimum-!","ALLOWED_ORIGINS":"http://localhost:8000",
 "LOG_DIR":"","STORAGE_BACKEND":"local"}.items(): os.environ.setdefault(k,v)
from fastapi.routing import APIRoute
from main import app; from limiter import limiter
def collect(r,out):
    for x in getattr(r,"routes",[]) or []:
        if isinstance(x,APIRoute):
            for m in x.methods or set(): out[(x.path,m)]=x
        elif type(x).__name__=="_IncludedRouter":
            c=x.effective_route_contexts; c=c() if callable(c) else c
            for ctx in c:
                for m in ctx.methods or set(): out[(ctx.path,m)]=ctx
            collect(getattr(x,"original_router",None),out)
        else: collect(x,out)
by={}; collect(app.router,by)
for (p,m),ctx in sorted(by.items()):
    d=getattr(ctx,"dependant",None)
    if not d: continue
    if "require_flag_llm_generation" not in {getattr(x.call,"__name__","") for x in d.dependencies}: continue
    q=f"{d.call.__module__}.{d.call.__name__}"
    lims=[str(l.limit) for l in limiter._route_limits.get(q,[])]
    ok = any("day" in s.lower() for s in lims) and any(("hour" in s.lower() or "minute" in s.lower()) for s in lims)
    print(("OK   " if ok else "BROKEN"), m, p, lims)
PY
```
Prints 4 `BROKEN` lines. Then `.venv/bin/pytest tests/test_creator_quota.py -q` — **all green.**

Adding `"routers.creators.build_dna"` to `_LLM_ROUTES` fails the suite immediately.

---

## B2 (HIGH) — the design-token contract gate uses a 17-name denylist; two undeclared tokens are live today and render nothing

**Evidence:** `frontend/src/test/design-tokens.contract.test.ts:68` (`SUSPECT`), `:89`
(`const undeclared = SUSPECT.filter(...)`).

**Claims to verify** — file header, `:6-13`:
> *"Issue 400a: a utility that names a token which does not exist FAILS SILENTLY. Tailwind emits
> nothing, the element inherits, and the page still renders — so neither a type check nor a
> rendered-DOM assertion can see it. Two live examples this test was written from, both shipped
> for months."*

**Why it does not verify that:** the test's second case only reports a violation when the utility
name is in a hardcoded 17-item `SUSPECT` list. Any *other* undeclared token name is silently
allowed. Two are live on `main`:

| utility | site(s) | declared token? |
|---|---|---|
| `bg-surface-raised` | `frontend/src/components/ActivityPanel.tsx:148`, `:191` | no — only `--color-surface` and `--color-raised` exist |
| `bg-accent-subtle` | `frontend/src/components/profile/BrandKitSection.tsx:119` | no — `accent-soft`/`-text`/`-hover`/`-active`/`-border` exist |

These are the *identical* defect to the two the gate was written from (`text-error`,
`bg-[color:var(--color-border)]`), on the ActivityPanel rows and the brand-kit callout.

**Repro (proves both halves — gate green AND CSS absent):**
```bash
cd frontend
npx vitest run src/test/design-tokens.contract.test.ts   # 3 passed
npx vite build --outDir /tmp/distcheck
grep -c '\.bg-surface-raised\|\.bg-accent-subtle' /tmp/distcheck/assets/*.css   # -> 0
grep -c '\.bg-surface\b\|\.bg-accent-soft' /tmp/distcheck/assets/*.css          # -> nonzero
```
Confirmed on this tree: `.bg-surface-raised` = 0, `.bg-accent-subtle` = 0, `.bg-surface` = 1,
`.bg-accent-soft` = 1, `.shadow-accent-glow` = 1.

Adding `'surface-raised','accent-subtle'` to `SUSPECT` turns the gate red immediately.

**Aggravating factor:** the `Frontend (lint, test, build)` job is **not a required check**
(`docs/BRANCHING.md:100-127`), so even a red version of this gate cannot block a merge.

---

## B3 (HIGH) — the RLS regression test still hardcodes its table list; 8 policy-bearing tenant tables have never been exercised

**Evidence:** `tests/test_rls_isolation_integration.py:265` (`_TENANT_TABLES`, 17 entries),
`:579` (`_CHILD_TABLES`, 6 entries), consumed at `:316`, `:438`, `:751`.

**Claims to verify** — `:245-264` and `:288-293`:
> *"The tenant-owned tables with direct `creator_id` columns."* … *"**For every tenant-owned
> table**, an unfiltered `SELECT *` … returns zero rows belonging to Creator B. This is the
> property RLS is purchased to provide."*

**Why it does not verify that:** the comment above the tuple documents that the *previous*
version of this test hardcoded `("clips","signals")` and therefore passed vacuously on 2 of 17
tables (OFF_COURSE_BUGS `:25`). The fix replaced a 2-item literal with a 17-item literal. It is
the same construction. Scanning the migrations for `tenant_isolation` policies yields **26**
tables. Eight carry a policy and appear in **neither** tuple:

```
chat_conversations  clip_impressions  clip_publications  creator_style
data_exports        notifications     summaries          video_context
```
(`summaries` gets one incidental `creator_id` assertion inside the child-table test; the other
seven are entirely unexercised — no cross-tenant read check, no deny-by-default check, no
WITH CHECK write check.)

Separately, five tables carry a `creator_id` column and **no** policy at all
(`creator_api_keys`, `creator_identity`, `event_logs`, `notification_deliveries`,
`notification_preferences`). Two of those are documented as deliberate; `creator_api_keys` and
`creator_identity` are not, and the test's shape means it can never surface the question.

**Repro:**
```bash
.venv/bin/python - <<'PY'
import ast,glob,pathlib,re
tabs=set()
for f in glob.glob("alembic/versions/*.py"):
    s=pathlib.Path(f).read_text()
    if "ROW LEVEL SECURITY" not in s and "CREATE POLICY" not in s: continue
    for m in re.finditer(r"ALTER TABLE\s+(\w+)\s+ENABLE ROW LEVEL SECURITY",s,re.I): tabs.add(m.group(1))
    for m in re.finditer(r"CREATE POLICY\s+\w+\s+ON\s+(\w+)",s,re.I): tabs.add(m.group(1))
    for n in ast.walk(ast.parse(s)):
        if isinstance(n,ast.Assign):
            for t in n.targets:
                if isinstance(t,ast.Name) and "TABLE" in t.id.upper():
                    try: v=ast.literal_eval(n.value)
                    except Exception: continue
                    for e in ([v] if isinstance(v,str) else v):
                        tabs.add(e[0] if isinstance(e,(list,tuple)) else e)
import importlib.util as iu
sys_scope={"audience_activity","clip_edit_documents","clip_feedback","clips","creator_dna",
"creator_insights","creator_style_notes","demographics","dna_embeddings","improvement_briefs",
"minute_deductions","minute_packs","preference_models","usage","video_feedback","videos",
"youtube_tokens","video_metrics","retention_curves","transcripts","signals","clip_outcomes","chat_messages"}
print("policied:",len(tabs)); print("policied but UNTESTED:",sorted(tabs-sys_scope))
PY
```
The integration lane is green on every PR while those eight tables have zero RLS coverage.

---

## B4 (HIGH) — the kill-switch / spend-guard gate verifies 10 of the 17 LLM routes; the 7 it misses include both chat routes and identity chat

**Evidence:** `tests/test_flags.py:410` (`_BILLED_LLM_ROUTES`), consumed at `:450-467`.

**Claims to verify** — `tests/test_flags.py:448-450` + `docs/AUDIT_KNOWN_ISSUES.md:230`:
> *"Every LLM-reaching endpoint carries both `require_flag('llm_generation')` and
> `require_budget` — that gating is complete and **CI-enforced**."*

**Why it does not verify that:** this test is *better* built than most — it carries the
`_IncludedRouter`-aware resolver (`:427-443`) written precisely because
`tests/test_response_models.py` went vacuous, plus an `unknown` assertion that turns "route not
found" into a loud failure. But its **scope is still a 10-item literal**. Resolving the app gives
17 `llm_generation`-flagged routes. The 7 unverified:

```
POST /api/chat/messages
POST /api/chat/conversations/{conversation_id}/regenerate
POST /creators/me/identity/chat
POST /creators/me/improvement-brief
POST /creators/me/video-analysis
POST /videos/{video_id}/clips/generate
POST /videos/{video_id}/clips/generate-more
```

`/creators/me/identity/chat` is exactly the route the ground-truth taxonomy names as a past
money-path leak (Class 8, `:114`/`:116` — *"identity chat entirely unbilled — invisible to the
spend guard"*). `POST /videos/{id}/clips/generate` is the core product action. All 7 carry both
dependencies today — so this is latent — but the *claim of enforcement* is false for 41% of the
surface, and the fix is one line: derive the set from the resolver instead of listing it.

**Repro:** the B1 script, printing the `require_flag_llm_generation` set (17) and diffing against
the literal (10).

---

## B5 (MEDIUM) — the "no response ever promises virality" gate scans 3 real payloads, 5 legacy static files, and 29 HTTP 401 bodies

**Evidence:** `tests/test_compliance_no_virality.py:73` (`test_no_virality_in_openapi_response_bodies`),
`:116` (`test_no_virality_in_static_assets`).

**Claims to verify** — module docstring `:1-6` and `CLAUDE.md` → Production Standards:
> *"Asserts that **no JSON response body**, static asset, or OpenAPI schema description contains a
> forbidden virality-promise phrase."* … *"No response ever promises virality"* (a Phase-4
> checklist item marked *"structural test green"*).

**Why it does not verify that:**

1. **The crawler is unauthenticated.** It walks the OpenAPI paths and `client.get(path)` with no
   session cookie. Measured on this tree: 34 of 104 documented paths are crawled (23 GETs are
   skipped for path params, and the whole POST surface is out of scope by construction), and of
   those 34, **29 return `{"detail":"Not authenticated"}`**, 1 returns 404, 1 returns 400. Only
   **three** responses carry product content: `/billing/packs`, `/creators/niches`, `/health`.
   Every LLM-generated body — DNA briefs, clip rationales, title suggestions, hook reports,
   improvement briefs, insights copy — is *never scanned by this test*.
2. **The asset walk points at a retired directory.** `static/` now holds 5 scannable files
   (`landing.html`, `tos.html`, `privacy.html`, `accessibility.html`, `_design-tokens.css`) —
   per `CLAUDE.md`, the vanilla static app pages were retired in Issue 226. The product UI is
   `frontend/src` (**260 `.ts`/`.tsx` files**), which no virality gate walks at all; there is no
   `no-virality` test among the 6 frontend structural gates.

**Repro:**
```bash
PYTHONPATH=. .venv/bin/python - <<'PY'   # (env preamble as in B1)
from fastapi.testclient import TestClient
from main import app
from collections import Counter
s=app.openapi(); c=Counter()
with TestClient(app, raise_server_exceptions=False) as cl:
    for p,i in s["paths"].items():
        op=i.get("get")
        if not op or any(x.get("in")=="path" for x in op.get("parameters",[])): continue
        c[cl.get(p).status_code]+=1
print(c, "of", len(s["paths"]), "documented paths")
PY
# -> Counter({401: 29, 200: 3, 404: 1, 400: 1}) of 104 documented paths
find static -name '*.html' -o -name '*.css' -o -name '*.js' | wc -l   # 5
find frontend/src -name '*.ts*' | wc -l                                # 260
grep -rl "viral" frontend/src/test/                                    # (no results)
```

---

## B6 (MEDIUM) — `TestHonestyOnGeneratedBodies` checks string literals the test author wrote, and claims to check generated fixtures

**Evidence:** `tests/test_honesty.py:272-306`, helper defined at `:66`.

**Claims to verify** — the class docstring, `:273-276`, verbatim:
> *"Assert that **mocked Anthropic responses from existing test fixtures** pass the honesty check.
> **This catches the gap where injection could coerce the model into generating a virality
> promise in the body text.**"*
>
> and the helper's own docstring `:69-70`: *"the canonical test-time / **eval-time** honesty
> assertion for generated body text (briefs, title suggestions, hook reports)."*

**Why it does not verify that:** `brief_body` (`:280-286`) and `title_body` (`:291-295`) are
string literals typed into the test file. No fixture, no recorded response, no prompt, no builder
output. The check runs over text the author already knew was clean — a closed loop with the
assertion and the input written in the same breath. It cannot detect an injection-coerced
virality promise because no model output ever reaches it.

Across the whole repo, `assert_no_virality_promise` is applied to exactly **three** real values,
none of them generated: two module constants (`knowledge/clip_titles.DISCLAIMER`,
`knowledge/clip_captions.DISCLAIMER`, `tests/test_llm_robustness.py:744,751`) and one email
template (`tests/test_mailer.py:479`). It is applied to **zero** LLM outputs, and to **zero**
eval-harness outputs despite being documented as the eval-time assertion.

**Repro:**
```bash
grep -rn "assert_no_virality_promise" --include=*.py . --exclude-dir=.venv | grep -v tests/test_honesty.py
# 3 call sites: 2 module DISCLAIMER constants + 1 email template. No fixtures, no goldens,
# no eval outputs, nothing under tests/eval/.
```
Combined with B5, the honesty dimension has no gate over any generated text anywhere.

---

## B7 (MEDIUM) — the Issue-228 AST route sweep walks a 6-router tuple; `routers/chat.py` is not in it

**Evidence:** `tests/test_security_baselines.py:304` (`_LLM_RENDER_ROUTERS`), consumed at `:409`.

**Claims to verify** — `:295-299`:
> *"Cost-safety structural guard. A new handler in any LLM/render router that enqueues billed work
> MUST carry BOTH a `@limiter.limit` decorator AND a `check_positive_balance` / `check_balance*`
> call. Walking the AST catches a gate-less route at commit time instead of in prod."*

**Why it does not verify that:** the AST walk itself is genuinely good — it even ships
`test_ast_sweep_flags_a_gateless_route` (`:429`) proving the detector is not a no-op, and a
`checked >= 10` floor guarding against the sweep matching nothing. **All of that protects the
detector; none of it protects the scope.** The scope is `("clips","titles","thumbnails",
"insights","improvement","analysis")`. Routers that enqueue billed work and are outside it:

- `routers/chat.py` — the two Anthropic call-site entry points confirmed in
  `tests/test_usage_coverage.py`'s own `_ANTHROPIC_CALL_SITES` map (`chat/runner.py::run_chat_turn`,
  `chat/intake.py::run_intake_turn`). It also gates by `Depends(require_budget)` rather than an
  in-body `check_positive_balance`, so even if added it would need the detector extended.
- `routers/review.py` — `trim_render` (`:386-412`, a `render_intake` route with a balance floor).
- `routers/videos.py` — five `check_positive_balance` call sites on the upload path.

The floor assertion (`checked >= 10`) is what makes this look verified; it counts handlers inside
the 6 named routers, so it stays satisfied no matter how many routers are missing.

**Repro:**
```bash
grep -ln "check_positive_balance\|check_balance" routers/*.py
# includes routers/review.py and routers/videos.py, neither in _LLM_RENDER_ROUTERS
grep -n "require_budget\|limiter.limit" routers/chat.py   # billed LLM routes, router never swept
```

---

## B8 (MEDIUM) — the BYPASSRLS allowlist is enforced in exactly one file

**Evidence:** `tests/test_worker_invariants.py:197`
(`test_admin_session_local_call_sites_match_allowlist`), scope set at `:203`
(`ast.parse(inspect.getsource(tasks))`).

**Claims to verify** — the failure message, `:236-239`:
> *"New `AdminSessionLocal` (BYPASSRLS) call site(s)… **Per-creator work must use
> `db.tenant_session(creator_id)` so RLS applies (Issue 231)**; add to the allowlist ONLY for a
> genuine cross-tenant sweep or tenant-id bootstrap."*

**Why it does not verify that:** the allowlist itself is exemplary — bidirectional (it fails on
stale entries too) and AST-based. But it parses **only `worker/tasks.py`**. `AdminSessionLocal`
is instantiated in four other first-party modules, all outside any gate:

```
billing/refund.py          async with AdminSessionLocal() as session
billing/spend_guard.py     async with AdminSessionLocal() as session
routers/notifications.py   async with AdminSessionLocal() as session   <- inside an HTTP handler
youtube/oauth.py           async with AdminSessionLocal() as internal  (x2)
```

Each is individually justified in a comment (the notifications one is the unauthenticated
one-click unsubscribe, keyed by a unique token). **The point is that nothing enforces that.** A
new RLS-bypassing session opened in any router, in `chat/`, or in `billing/` is invisible to the
one gate whose message reads as a global rule — and per the ground-truth taxonomy, the RLS fault
line produced five defects in one day.

**Repro:**
```bash
grep -rn "AdminSessionLocal() as" --include=*.py . --exclude-dir=.venv --exclude-dir=tests \
  | grep -v "^./worker/tasks.py"   # 5 sites in 4 modules, none gated
```

---

## B9 (MEDIUM) — three LLM features slipped past four independent registries at once

**Evidence:** `tests/test_llm_metrics_coverage.py:24`, `tests/test_model_config.py:15`,
`tests/test_grounding_honesty.py:26`, and (already-known) `tests/test_llm_conformance.py:34`.

**Claims to verify:** *"Every module that runs Anthropic inference and therefore must record the
token metric"* (`test_llm_metrics_coverage.py:22`); *"All per-task model keys"*
(`test_model_config.py:14`); *"they iterate the builders so a NEW generator that reintroduces the
placeholder fails here **without anyone remembering to add a case**"*
(`test_grounding_honesty.py:16-19`).

**Why it does not verify that:** all four are literals, and the *same* newer features are absent
from all four:

| module | in metrics registry? | model key in `_TASK_MODEL_KEYS`? | in conformance `_LLM_MODULES`? |
|---|---|---|---|
| `preference/style_distill.py` | no | `ANTHROPIC_MODEL_STYLE_DISTILL` — no | no (this is known finding #7) |
| `analysis/video_context.py` | no | `ANTHROPIC_MODEL_VIDEO_CONTEXT` — no | no |
| `knowledge/clip_metadata.py` | no | `ANTHROPIC_MODEL_CLIP_METADATA` — no | no |
| `routers/insights.py` | no | n/a | n/a |

And `_BUILDERS` (5 entries) omits `knowledge/hooks.py` and `knowledge/thumbnails.py` — the two
builders that assemble the `CREATOR DNA PROFILE:` block inline instead of calling
`dna_system_block`, i.e. precisely the two the structural rule cannot reach any other way. Its own
docstring says *"Seven prompt builders shared the line."* Five are listed.

**Honest severity:** every one of these is compliant today (all 3 modules call
`record_llm_metric`; all 3 model values are valid bare aliases; both omitted builders correctly
omit the block). **This is latent, not live.** It is reported because the *pattern* — one new
feature silently escaping four separate "every module must…" gates in the same commit — is the
mechanism by which finding #7 (`style_distill` shipping with no timeout and no `max_retries`)
happened, and nothing prevents the next one.

**Repro:**
```bash
.venv/bin/python - <<'PY'
import importlib.util as iu, pathlib
s=iu.spec_from_file_location("uc","tests/test_usage_coverage.py"); m=iu.module_from_spec(s); s.loader.exec_module(m)
print(sorted({f for f,_ in m._discover_anthropic_call_sites()}))   # 18 files
PY
grep -c "ANTHROPIC_MODEL_" config.py     # 17 keys vs 14 in _TASK_MODEL_KEYS
grep -l "CREATOR DNA PROFILE" knowledge/*.py   # 7 builders vs 5 in _BUILDERS
```

---

## B10 (MEDIUM) — `test_response_models` still iterates 6 of 126 routes; the fix exists in the repo and was never back-ported

**Evidence:** `tests/test_response_models.py:59` (`for route in app.routes`), `:79`
(`test_guard_catches_undeclared_route`).

**Claims to verify:** *"This test fails if a future endpoint ships without one — the same 'make it
an invariant, not a one-time review' posture as `test_isolation.py`."* (`:5-7`).

**Why it does not verify that:** under FastAPI 0.137.1, `app.routes` holds 6 `APIRoute` objects
and 24 `_IncludedRouter` wrappers. Measured: **6 of 126 effective routes are inspected.** This is
already logged (OFF_COURSE_BUGS `:134`, 2026-08-04) — I am reporting it because it is **still
unfixed on `main`**, and because `tests/test_flags.py:427-443` contains a working resolver written
specifically for this bug, with a comment naming this exact file as the motivating case. The fix
was written, documented, and not applied where the defect lives.

The second test compounds it: `test_guard_catches_undeclared_route` builds a *fresh* `FastAPI()`
and **re-implements the check inline** (`:110-113`) rather than calling the production guard. It
proves a copy of the logic works on a hand-built app — it can never notice that the real guard
sees nothing.

**Repro:**
```bash
PYTHONPATH=. .venv/bin/python -c "
import os;from cryptography.fernet import Fernet
# env preamble as B1
from fastapi.routing import APIRoute; from main import app
print(len([r for r in app.routes if isinstance(r,APIRoute)]), 'of', len(app.routes))"
# -> 6 of 35 ; Counter({'_IncludedRouter': 24, 'APIRoute': 6, 'Route': 3, 'Mount': 2})
.venv/bin/pytest tests/test_response_models.py -q   # green
```

---

## B11 (LOW) — `clips.blended_score` is written on every rerank and read by nothing

**Evidence:** `clip_engine/ranking.py:221` (write), `models.py:766` (column),
`alembic/versions/0059_clip_blended_score.py` (migration), `tests/test_preference_rerank.py:209`
(the 6-entry `readers` map that pins the invariant).

**Claims to verify:** `tests/test_preference_rerank.py:204-209` —
*"The Issue-465 reader map, pinned: recap candidates, clip-explain, proof-of-lift, and the chat
tools all deliberately read the immutable fit `score`. A future switch to `blended_score` must be
an explicit, tested choice."*

**Why it does not verify that:** the reader map is a 6-entry literal, so a *new* reader that
switches to `blended_score` is simply not in the map and the test still passes. More to the point,
the column it guards is a signal with no receiver: `blended_score` appears in `clip_engine/`,
`models.py`, `preference/model.py` (a comment) and its own migration — **and nowhere in
`routers/` or `frontend/src`**. It is written to Postgres on every rerank, used only as the sort
key inside the function that writes it, never serialized into an API response, never read by the
SPA, never queried. Same shape as the already-found `clip_impressions` finding, different column.

**Repro:**
```bash
grep -rn "blended" routers/ frontend/src   # no output
grep -rn "blended_score" --include=*.py . --exclude-dir=.venv --exclude-dir=tests
# only: models.py, clip_engine/ranking.py, preference/model.py (comment), migration 0059
```

---

# What I checked and found genuinely sound

Reported so the next sweep does not re-derive it:

- **`tests/test_usage_coverage.py`** — the layer-2 billing sweep is a *real* AST discovery over the
  repo with a bidirectional map check (unmapped **and** stale both fail). This is the model the
  other registries should copy.
- **`tests/test_worker_invariants.py`** allowlist *contents* — bidirectional, so it cannot go
  stale. Only its file scope is narrow (B8).
- **`tests/test_scoring_goldens.py`** — schema-sha256 pin + configured-model pin means a stale
  golden cannot green-stamp a changed contract. It still replays a recorded body over a patched
  `_ANTHROPIC` (Issue 476's core complaint), but it is honest about that in its docstring.
- **`tests/test_principles_registry.py`** — its 4-site `copies` dict is *correct today*; I verified
  no 5th copy exists (including the SPA).
- **`frontend/src/test/sourceScan.ts`** — globs the whole tree, exposes `sourcePaths()` so a gate
  can assert the glob matched, and uses the TS AST specifically because two prior raw-text gates
  false-positived on comments. Good engineering.
- **The eval anti-hollowing guards** — `SCENARIO_FLOOR` pinned in two files, a skip-marker regex
  scan, and a landing-page↔harness count reconciliation. Only gap is the non-recursive glob.
- **Redaction blocklist tests** — `_FULL_BLOCKLIST_KEYS` asserts the same key across all three
  sinks in one parametrized suite, which is exactly the right shape.

---

# Off-class

Not this class; noting so they are not lost.

1. **`POST /creators/me/dna/build` is rate-limited at 120/minute with no daily cap.** Even setting
   aside the gate hole in B1, this is a live cost-safety defect: 120 Sonnet DNA-build enqueues per
   minute per creator. `routers/creators.py:547`.
2. **`POST /api/chat/messages` and `/regenerate` have no burst cap** — the full
   `CHAT_DAILY_MESSAGE_LIMIT` (25) can be spent in one second, each turn being a tool-using chat
   round-trip. `routers/chat.py:117`, `:161`.
3. **`tests/test_render.py:1552` and `tests/test_signals.py:364`** are the two "run the REAL
   ffmpeg" regression tests (the second's docstring: *"the previous invocation failed on every real
   call while the mocked test stayed green over it"*). Neither carries `@pytest.mark.render_env`,
   so they run in the **default unit lane**, where `ci.yml:96-104` installs ffmpeg **softly**
   (`|| echo "::warning::…"`) — and the hard install happens in a *later* step. If the soft install
   ever warns, both self-skip and the green-over-broken condition returns. Moving both into the
   `render_env` lane (which hard-fails on a missing binary) would close it.
