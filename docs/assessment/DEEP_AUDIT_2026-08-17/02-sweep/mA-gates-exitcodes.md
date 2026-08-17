# Modality A — CI gates and exit-code plumbing

**Swept:** all 9 files in `.github/workflows/` line by line;
`.claude/skills/production-assessment/scripts/run_layer0.py` in full (all 8 gates, `--require`,
`--require-fresh`, `--require-coverage`, baseline interaction); `scripts/ci_local.sh` line by line;
`.githooks/pre-push`; `scripts/check_downgrades.py`; `scripts/doctor.py` orchestration + exit code;
`tests/test_ci_config.py` (22 meta-tests) and `tests/test_layer0_module_coverage.py` as the
anti-hollowing layer; live branch protection via `gh api`; live repo secret/variable inventory via
`gh secret list` / `gh variable list`.

**Interpreter:** `.venv/bin/python` for every execution. Nothing was mutated; the only file written
outside this report is `docs/assessment/_machine.json`, which is gitignored, is a regenerated
artifact of `run_layer0.py`, and was rewritten by a repro run (`git status` clean apart from the
pre-existing `LEFT_OFF.md` edit and this audit's own directory).

**Honest yield: 7 candidates, 5 of them with an executed repro, plus 3 low-value notes.** Four are in
the load-bearing path of a *required* check or the production deploy. The single highest-value
structural result is A1: the hardening mechanism the project invented for this exact class
(`--require`, Issue 479) is **incapable by construction** of catching the way four of the eight gates
actually fail. Adding `--require` to `static-gates` — the fix the process map recommends — would not
help.

**Things I checked and found genuinely sound** (recorded so nobody re-checks them): the
`migration-lint` Squawk and downgrade round-trip steps both `set -o pipefail`, and the revision-regex
that could silently `continue` is safe because `alembic/script.py.mako` emits the un-annotated
`revision = '…'` form that the regex matches (all 62 migrations verified); `scripts/check_downgrades.py`
still runs its allowlist-staleness half with zero file arguments; `deploy.yml`'s `alembic current ==
heads` assertion fails closed on an empty read; the `render_env` collect-count guard is real
(`-ge 7`, measured exactly 7); the `eval/clip-quality` commit status correctly targets the PR head
SHA and fails closed if the filter step errors; live branch protection matches `docs/BRANCHING.md`
byte-for-byte today.

---

## A1 — `run_layer0.py`: four gates report a PERFECT SCORE when the tool exits non-zero, and `--require` cannot see it

**Severity: HIGH.** Affects the required check `Types + SAST + deps (mypy, bandit, pip-audit)`.

**Evidence:** `.claude/skills/production-assessment/scripts/run_layer0.py:159-164` (ruff),
`:170-172` (mypy), `:227-231` (bandit), `:274-281` (pip-audit).

**What it claims to verify:** that ruff reports 0 issues, mypy 0 errors, bandit 0 HIGH/0 MEDIUM and
pip-audit 0 vulnerabilities, each against the strict `docs/assessment/baselines.json` floor of `0`.

**Why it does not verify that.** Not one of the four inspects `proc.returncode`. They infer the
result purely from stdout, and every one of them treats *empty stdout* as *a clean result*:

```python
# :171 — mypy: no returncode check, no unparseable branch at all
errors = sum(1 for ln in proc.stdout.splitlines() if ": error:" in ln)
return {"status": "ok", "value": errors, "metric": "mypy_errors", "compare": "max"}

# :161 / :229 / :276 — ruff / bandit / pip-audit
issues = len(json.loads(proc.stdout or "[]"))       # empty stdout -> []  -> 0 issues
results = json.loads(proc.stdout or "{}").get("results", [])   # -> []    -> 0 findings
data    = json.loads(proc.stdout or "{}")                       # -> {}   -> 0 vulns
```

A tool that runs and *fails* writes its diagnostics to **stderr** and leaves stdout empty. That path
produces `{"status": "ok", "value": 0}` — the best possible score — which passes a baseline of 0.

This is strictly worse than the "skipped" path the process map already documents, because
`main():589-592` implements `--require` as *"fail if this gate's status is `skipped`"*. A gate whose
status is `ok` is invisible to `--require`. **The Issue-479 hardening pattern cannot protect these
four gates even if someone adds `--require ruff,mypy,bandit,pip_audit` to `static-gates` tomorrow.**

**Repro (executed, verbatim output).** Any state where `_sources()` resolves empty — a package
rename, a sparse checkout, or `REPO_ROOT = Path(__file__).resolve().parents[4]` (`:36`) pointing at
the wrong directory because `.claude/skills/` moved one level:

```bash
.venv/bin/python - <<'PY'
import importlib.util, pathlib, sys
p = pathlib.Path(".claude/skills/production-assessment/scripts/run_layer0.py")
spec = importlib.util.spec_from_file_location("l0", p); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m._CANDIDATE_SOURCES = []          # _sources() -> []  => `mypy` invoked with zero targets
sys.argv = ["run_layer0.py", "--gates", "mypy", "--require", "mypy"]
print("EXIT CODE (even WITH --require mypy):", m.main())
PY
```

```
Layer 0 — deterministic gates
  mypy       ok       0

Wrote docs/assessment/_machine.json

All runnable gates passed.
EXIT CODE (even WITH --require mypy): 0
```

The mypy process underneath really did exit 2 (`mypy: error: Missing target module, package, files,
or command.`, verified by running it directly). The gate reported **zero type errors** and the job
would be green.

**Real-world triggers, each verified against the real binaries in `.venv`:**

| Trigger | Tool | rc | stdout | Gate result |
|---|---|---|---|---|
| invalid/deprecated key in `[tool.ruff]` after a ruff bump | `ruff check . --output-format json` | 2 | 0 bytes | `ok`, 0 issues |
| `_sources()` empty (rename / wrong `REPO_ROOT`) | `mypy --no-error-summary` | 2 | 0 bytes | `ok`, 0 errors |
| OSV/PyPI unreachable, proxied runner, rate limit, bad `-r` | `pip-audit -f json` | 1 | 0 bytes | `ok`, **0 vulnerabilities** |
| all package dirs moved (e.g. under `src/`) | `bandit -r -f json -q` | 2 | usage text | `skipped` → green (no `--require` on `static-gates`) |

The pip-audit row is the one that will actually happen: a transient advisory-database outage turns
the *dependency-vulnerability* gate from red into a confident green **"0 vulns"** on a required
check.

**The rule this needs:** every gate must assert its own subprocess exited as expected, and an
unexpected exit must be `fail`, never `ok` and never `skipped`. `--require` is the wrong primitive —
it only covers the honest half of the failure space.

---

## A2 — `--require-fresh` never adds `freshness` to the required set, so `freshness.yml`'s only gate exits 0 on a skip

**Severity: MEDIUM-HIGH.** This is the entire content of a quarterly workflow.

**Evidence:** `run_layer0.py:543-548` versus `:594-603`; `gate_freshness` skip path at `:291-293`;
`.github/workflows/freshness.yml:29-31`.

**What it claims to verify:** *"a skill whose `last_verified` is >90 days old fails this job"* —
`freshness.yml`'s own header, and the workflow's single step is
`run_layer0.py --gates freshness --require-fresh`.

**Why it does not verify that.** `main()` builds the required set as:

```python
required = {g.strip() for g in args.require.split(",") if g.strip()}
if args.require_coverage:
    required.add("coverage")          # <- --require-coverage IS wired in
# --require-fresh is NOT
```

`--require_fresh` is consumed only at `:594-598`, and only inside `if status.get("freshness") ==
"stale"`. When `gate_freshness` returns `{"status": "skipped", "detail": "no skills found"}` —
which it does whenever `SKILLS_DIR.glob("*/SKILL.md")` matches nothing (`.claude/skills/` renamed or
moved, skills nested one directory deeper, a sparse checkout, or `REPO_ROOT` off by a level) — the
`stale` branch never fires, `required` is empty so the skip check at `:589` iterates nothing, and the
script prints `All runnable gates passed`.

Note the asymmetry: `--require freshness` *would* catch this. The flag the workflow actually uses
does not. Two flags that read as synonyms have opposite failure semantics.

**Repro (executed):**

```bash
.venv/bin/python - <<'PY'
import importlib.util, pathlib, sys
p = pathlib.Path(".claude/skills/production-assessment/scripts/run_layer0.py")
spec = importlib.util.spec_from_file_location("l0", p); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.SKILLS_DIR = pathlib.Path("/nonexistent/skills")   # skills dir renamed or moved
print("gate_freshness() ->", m.gate_freshness())
sys.argv = ["run_layer0.py", "--gates", "freshness", "--require-fresh"]
print("EXIT CODE:", m.main())
PY
```

```
gate_freshness() -> {'status': 'skipped', 'detail': 'no skills found'}
Layer 0 — deterministic gates
  freshness  skipped  no skills found
All runnable gates passed.
EXIT CODE: 0
```

The staleness cadence is *quarterly*. A silent no-op here would go unnoticed for months by
construction — which is the same profile as the four "dead for weeks" incidents.

---

## A3 — `gate_module_coverage` reports `ok` when the floor-gated modules are absent from the report, and the guard test checks the wrong half

**Severity: HIGH.** Affects the required check `Coverage floor (pytest-cov ratchet)`.

**Evidence:** `run_layer0.py:396-413`, specifically:

```python
rate = _module_line_rate(root, mod)
rates[mod] = rate
if rate is None:
    # Module not found in coverage report — could be untouched in this run.
    # Don't fail: ... Log as unknown.
    continue
```

**What it claims to verify:** per-module coverage floors on the five load-bearing modules —
`clip_engine` 91.0, `preference` 88.0, `crypto` 99.0, `limiter` 99.0, `auth` 99.0. `ci.yml:250-253`
passes `--require coverage,module_coverage,diff_cover` explicitly so that *"a skip of ANY of them …
is a hard job failure, never a green no-op."*

**Why it does not verify that.** The gate has two ways to enforce nothing, and `--require` only
covers one. If `_coverage.xml` is missing or unparseable the status is `skipped` and `--require`
fires — that is Issue 479, and it is fixed. But if `_coverage.xml` **exists and simply does not
contain the module**, `_module_line_rate` returns `None`, the loop `continue`s, `_failures` stays
empty, and `_evaluate` (`:481`) sets the status to **`ok`**. `--require module_coverage` sees a
non-skipped gate and is satisfied. Five floors can read "unknown" and the required job goes green.

This is not hypothetical: it is *literally the Issue-368 failure* (`run_layer0.py:325-331` — "these
two sat at 0.0 — i.e. unenforceable — because the multi-root `--cov` flattened their files into
package `.` and `_module_line_rate` returned `None`. **They were never low-coverage; they were
unmeasured.**"). Issue 368 fixed the *cause* (single `--cov .` root) and left the *silent-continue*
in place, so any future change that moves a module out of the report — a package rename, a
`[tool.coverage.run] omit` addition, a `--cov` invocation change, a module that no test imports —
restores the exact same vacuous green.

**The anti-hollowing test guards the wrong half.**
`tests/test_layer0_module_coverage.py:86-95`, `test_every_floored_module_is_resolvable_in_principle`,
is documented as *"Guard the floors dict itself: no module may sit at an unenforceable 0.0"* — and
its body only asserts `MODULE_COVERAGE_FLOORS` is non-empty and no floor VALUE is `<= 0.0`. It never
touches a coverage report. Nothing anywhere asserts that the five modules actually *resolve* in the
real `_coverage.xml`. The measured RATE going `None` — the thing that actually happened for a year —
is untested.

**Repro (executed):** a perfectly well-formed `_coverage.xml` that happens not to contain the five
modules:

```bash
.venv/bin/python - <<'PY'
import importlib.util, pathlib, tempfile
p = pathlib.Path(".claude/skills/production-assessment/scripts/run_layer0.py")
spec = importlib.util.spec_from_file_location("l0", p); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
tmp = pathlib.Path(tempfile.mkdtemp()); m.ASSESS_DIR = tmp
(tmp/"_coverage.xml").write_text(
 '<?xml version="1.0" ?><coverage line-rate="0.9"><packages>'
 '<package name="somethingelse" line-rate="0.9"><classes>'
 '<class filename="somethingelse/x.py" line-rate="0.9"/></classes></package>'
 '</packages></coverage>')
res = m.gate_module_coverage(); print(res)
print("status:", m._evaluate({"module_coverage": res}, m._load_baselines())[0])
PY
```

```
{'status': 'ok', 'value': {'rates': {'clip_engine': None, 'preference': None, 'crypto': None,
 'limiter': None, 'auth': None}, ..., 'failures': []}, ...}
status: {'module_coverage': 'ok'}
```

Five floors unenforced, gate `ok`, `--require` satisfied, required job green.

**The rule this needs:** a floor whose subject cannot be measured is a FAILURE, not an "unknown".
`rate is None` must append to `failures`, exactly as `rate < floor` does.

---

## A4 — The production deploy preflight runs `doctor.py` **without `--full`**, so it probes no external dependency at all

**Severity: HIGH.** This is the gate on every push-to-prod deploy.

**Evidence:** `.github/workflows/deploy.yml:278-279`:

```yaml
      - name: Preflight check
        run: docker compose -f docker-compose.prod.yml run --rm app python scripts/doctor.py
```

versus `scripts/doctor.py:454-474` and `:500` (`--full` = *"also probe external APIs"*).

**What it claims to verify.** It is named "Preflight check" and it is the only verification between
"pull the new image" and "run migrations / roll out". `docs/DEPLOYMENT.md` and the process map both
describe `doctor.py` as *"env/secret validator + live probes for Postgres, Redis, Anthropic, Voyage,
Deepgram, R2, Stripe"*.

**Why it does not verify that.** `audit()` gates the entire external-probe section behind `full`:

```python
if not offline:
    sections.append(("Live — internal", [_live_postgres(...), _live_redis(...)]))
    if full:
        sections.append(("Live — external APIs",
            [_live_anthropic(...), _live_voyage(...), _live_deepgram(...), _live_r2(...), _live_stripe(...)]))
```

Without `--full`, the deploy preflight live-probes **Postgres and Redis only**. Anthropic, Voyage,
Deepgram, R2 and Stripe get presence/format checks against `.env` strings and nothing more.

**Repro (executed):**

```bash
.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, "scripts")
import doctor as d
d._live_postgres = lambda env, s: d.Result("postgres", d.Status.OK, "stub")
d._live_redis    = lambda env, s: d.Result("redis", d.Status.OK, "stub")
for full in (False, True):
    print(f"full={full!s:5s} -> {[t for t,_ in d.audit({}, offline=False, full=full)]}")
PY
```

```
full=False -> [... 'Storage', 'Billing', ..., 'Live — internal']
full=True  -> [... 'Storage', 'Billing', ..., 'Live — internal', 'Live — external APIs']
```

**This is the 10-week Stripe outage's exact geometry, still in place.** `OFF_COURSE_BUGS.md:148`
records that `doctor.py`'s Stripe probe used a raw `httpx.get` instead of the app's client, and
`docs/GO_LIVE.md:71` cited "Stripe live-verified" over a total outage. Someone then fixed the
probe-path defect properly — `scripts/doctor.py:389-391` now says *"probe through the app's own
client factory rather than a parallel boto3 construction, so a misconfigured `worker/storage._r2`
(the class of failure the Stripe probe missed) is what gets tested."* **And the automated caller
never invokes it.** The corrected probe runs only when a human types `--full` by hand.

**Second, compounding defect.** `has_failures` (`:477-478`) counts **only `Status.FAIL`**.
`Status.WARN` and `Status.SKIP` never affect the exit code. So even under `--full`:

- `:343` — Anthropic: *"SDK lacks `models.list` — cannot verify"* → `WARN` → exit 0. *A probe that
  reports it could not verify anything is a pass.*
- `:387` — `_live_r2`: *"R2 not fully configured"* → `SKIP` → exit 0.
- `:380-383` — `_live_r2`: `STORAGE_BACKEND != r2` → `SKIP` → exit 0.
- `:249` — `STRIPE_SECRET_KEY` not set → `WARN "not set (billing disabled)"` → exit 0.

**Live state that makes this concrete.** `gh secret list` on `reese8272/creatorclip` shows
`R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and `R2_BUCKET` are **not set**, so
`deploy.yml:234-237`'s `sync_secret` prints `skip R2_BUCKET (no GitHub secret set)` on every deploy —
the R2 sync that was added *"after a prod upload silently FAILED"* is inert — while `deploy.yml:247-253`
unconditionally pins `STORAGE_BACKEND=r2`. If the VM's hand-edited R2 creds ever drift, the config is
pinned to a backend whose credentials nothing checks, and the preflight that could catch it is not
running the probe.

**The rule this needs:** the deploy preflight must pass `--full`, and `SKIP`/"cannot verify" on a
dependency the deployed config declares as active must be a `FAIL`.

---

## A5 — The nightly live-API harness reports success with **zero tests executed** if a secret is empty, and both meta-tests written to prevent that assert YAML strings

**Severity: MEDIUM-HIGH** (latent today — both secrets are currently set — but the trap is armed and
the guard is decorative).

**Evidence:** `.github/workflows/llm-e2e-nightly.yml:64,89-97,101-104,126` ·
`tests/test_llm_live.py:18-22` · `tests/test_llm_live_scoring.py:42-46` ·
`tests/ingestion/test_transcription_live.py:28-32` · `tests/conftest.py:13` ·
guards at `tests/test_ci_config.py:491-511` and `:513-536`.

**What it claims to verify.** This workflow is the *only* place three things are exercised against a
real model: the Issue-319 feature-module conformance lane (structured output, honesty disclaimer,
prompt-cache landing, typed SDK exceptions, no PII in logs), the **Issue-476 scoring behavioural
lane** — the remediation for the clipping-integrity audit's SEV1 finding that `score_candidates`, *the
LLM call that decides which clips ship*, is evaluated nowhere — and the Issue-481 live ASR
timing-fidelity leg.

**Why it does not verify that.** All three lanes self-skip on a falsy key:

```python
_LIVE = os.environ.get("RUN_LLM_LIVE") == "1" and bool(os.environ.get("ANTHROPIC_API_KEY"))
_skip_unless_live = pytest.mark.skipif(not _LIVE, reason="Set RUN_LLM_LIVE=1 and ANTHROPIC_API_KEY …")
```

`${{ secrets.ANTHROPIC_API_KEY }}` on an unset/deleted/renamed/rotated-out secret expands to the
**empty string**, not to nothing. `bool("")` is `False`. And `tests/conftest.py:13` uses
`os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-real")` — `setdefault` does **not** replace
an existing empty value, so the rescue path does not fire either. Every test skips and pytest exits
`0`. The step's `${PIPESTATUS[0]}` plumbing is correct; it faithfully propagates a `0` earned by
running nothing. Same shape for `DEEPGRAM_API_KEY` on the ASR leg.

**Repro (executed):**

```bash
RUN_LLM_LIVE=1 ANTHROPIC_API_KEY="" \
DATABASE_URL="postgresql+psycopg://creatorclip:dev_password@localhost:5432/creatorclip" \
REDIS_URL="redis://localhost:6379/0" GOOGLE_OAUTH_CLIENT_ID=stub GOOGLE_OAUTH_CLIENT_SECRET=stub \
OAUTH_REDIRECT_URI=http://localhost:8000/auth/callback \
TOKEN_ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
JWT_SECRET_KEY="stub-jwt-secret-32-bytes-minimum-!" ALLOWED_ORIGINS=http://localhost:8000 LOG_DIR="" \
.venv/bin/python -m pytest tests/test_llm_live.py tests/test_llm_live_scoring.py -m llm_live -q
```

```
sssssssss                                                                [100%]
9 skipped, 1 deselected, 1 warning in 0.07s
PYTEST EXIT: 0
```

**The workflow already knows and says nothing that matters.** `llm-e2e-nightly.yml:137-138`:

```bash
grep "SCORING-MARGIN" "$RESULT_FILE" >> "$GITHUB_STEP_SUMMARY" \
  || echo "No SCORING-MARGIN lines — scoring lane did not run." >> "$GITHUB_STEP_SUMMARY"
```

The literal string *"scoring lane did not run"* is written into a step summary that nothing reads
and nothing gates on, while the job conclusion is `success`. A signal with no receiver, sitting
directly on top of the failure it describes.

**The two guards protect the wrong half.** `test_nightly_runs_transcription_live_leg`
(`tests/test_ci_config.py:491`) asserts the strings `tests/ingestion/test_transcription_live.py`,
`-m transcription_live`, `RUN_TRANSCRIPTION_LIVE: "1"` and `secrets.DEEPGRAM_API_KEY` appear in the
YAML — and its own docstring names the hazard exactly: *"without it the live test skips and the leg
silently stops running."* `test_llm_nightly_runs_scoring_behavioral_lane` (`:513`) does the same for
the LLM lane. Both pin the *invocation text*. Neither pins that a single test **executed**. The unit
job's `render_env` guard shows the project already knows the correct pattern —
`test "$(pytest -m render_env --collect-only -q | grep -c '::')" -ge 7` (`ci.yml:140`) — and the
nightly has no equivalent.

**The rule this needs:** a live lane must assert a non-zero *executed* (not collected) count, and an
empty-string secret must fail loudly rather than degrade to skip.

---

## A6 — The RLS activation workflow's only verification passes vacuously against an empty table

**Severity: MEDIUM.** Sits on the fault line that produced five defects in one day (taxonomy Class 7).

**Evidence:** `.github/workflows/activate-rls.yml:241-252`.

```bash
VIDEOS=$(docker compose ... psql -tA -U creatorclip_app -d creatorclip \
  -c "SELECT count(*) FROM videos;" 2>&1 || echo "ERROR")
if [ "$VIDEOS" != "0" ]; then
  echo "::error::Expected 0 visible videos for creatorclip_app without GUC; got $VIDEOS."
  exit 1
fi
echo "  ✅ RLS enforced: app role sees 0 rows without a creator-id GUC."
```

**What it claims to verify:** the printed conclusion is unambiguous — *"RLS enforced: app role sees 0
rows without a creator-id GUC."*

**Why it does not verify that.** `count(*) = 0` has two causes, and the check cannot distinguish
them: (a) RLS policies are hiding rows that exist — the claim; (b) **there are no rows.** A role with
`rolbypassrls = true` and no policies at all returns `0` from an empty `videos` table. This is the
`all([]) == True` shape at the SQL layer, and it guards precisely the control whose failure was
`OFF_COURSE_BUGS.md:47` (SEV1 — prod's app role had `rolbypassrls=true`, the entire RLS backstop
inactive). There is no positive control: nothing establishes that `videos` is non-empty from a
privileged role first, and nothing shows the same query returning `> 0` once the `app.creator_id`
GUC is set.

Note it does fail closed on a psql error (`2>&1` folds stderr into `VIDEOS`, which then `!= "0"`),
so the only false-green is the empty-table one — but that is exactly the state of a fresh VM, a
restored staging volume, or a pre-first-upload environment, i.e. the states in which somebody would
plausibly rehearse this workflow.

**Repro (state, not command):** run `activate-rls.yml` against any database where
`SELECT count(*) FROM videos` is 0. It prints `✅ RLS enforced` and exits 0 whether or not RLS is
active. To make the failure observable, run it with `rolbypassrls` still granted on an empty table.

**The rule this needs:** assert both directions — `count > 0` as a privileged role (proof there is
something to hide) and `count = 0` as the app role without a GUC.

---

## A7 — The required patch-coverage gate exits 0 for any diff with no measurable lines, including changes to the gate script itself

**Severity: MEDIUM.** Affects the required check `Coverage floor (pytest-cov ratchet)`.

**Evidence:** `run_layer0.py:430-454` (`gate_diff_cover`) + `:483-484` (status is decided solely by
`proc.returncode`) + `pyproject.toml:63-69` (`[tool.coverage.run] omit`).

**What it claims to verify:** *"changed lines must be >= 80% covered"* (`gate_diff_cover` docstring),
enforced by `diff-cover --fail-under=80` and made a hard requirement by
`--require coverage,module_coverage,diff_cover` in `ci.yml:250-253`.

**Why it does not verify that.** `diff-cover` computes its percentage only over lines that appear in
`_coverage.xml`. Paths in the `omit` list contribute zero lines, so a diff confined to them yields
"no lines with coverage information" and `diff-cover` exits **0**. `gate_diff_cover` then reports
`{"status": "ok", "value": "n/a"}` and `--require` — which only rejects `skipped` — is satisfied.
The omit list is `tests/*`, `alembic/*`, `*/__init__.py`, `scripts/*`, `.claude/*`.

Consequences worth naming: **`scripts/*` includes the operational and security surface** —
`scripts/deploy.sh`, `scripts/doctor.py`, `scripts/drills.py`, `scripts/llm_harness.py`,
`scripts/rotate_token_key.py`, `scripts/backup_pg.sh`, `scripts/check_downgrades.py`. **`.claude/*`
includes `run_layer0.py` itself** — the gate script is exempt from the coverage gate it implements,
so every finding in A1/A2/A3 above lives in code with a 0% patch-coverage requirement. `frontend/`
is never in the Python coverage report at all, so the entire TypeScript surface also satisfies this
required gate vacuously (and per the process map has no coverage measurement of its own).

**Repro (executed):** a two-commit repo whose diff touches only an omitted path:

```bash
mkdir dcrepo && cd dcrepo && git init -q -b main && mkdir scripts
echo "print(1)" > scripts/tool.py && echo "def f(): return 1" > app.py
git add -A && git commit -qm init && git checkout -qb feat
printf 'print(1)\nprint(2)\nprint(3)\n' > scripts/tool.py && git add -A && git commit -qm "scripts only"
git update-ref refs/remotes/origin/main refs/heads/main
cat > cov.xml <<'EOF'
<?xml version="1.0" ?><coverage line-rate="0.9" version="7.0"><sources><source>.</source></sources>
<packages><package name="." line-rate="0.9"><classes>
<class filename="app.py" line-rate="1.0"><lines><line number="1" hits="1"/></lines></class>
</classes></package></packages></coverage>
EOF
.venv/bin/python -m diff_cover.diff_cover_tool cov.xml --compare-branch=origin/main --fail-under=80 --quiet
echo "DIFF-COVER EXIT: $?"     # -> 0
```

**The rule this needs:** distinguish "patch coverage is above 80%" from "there was nothing to
measure", and decide deliberately which omitted paths are allowed to satisfy the gate silently.

---

## Lower-value notes (real, but not worth an issue on their own)

- **`staging-drills.yml:74-81`** — the step named "Wait for staging /health" ends with
  `test -n "${BODY:-}"`. It asserts only that *some* body came back; it never checks
  `status == "ok"`, unlike the equivalent step in `deploy.yml:342-358` which does. Given the recorded
  behaviour that `/health` returns HTTP 200 with `"degraded"` when Redis is down, the drills can run
  against a half-dead stack and this step still passes.
- **`ci.yml:96-104, 187-195, 227-236, 510-520, 692-701`** — the soft ffmpeg install degrades to
  `|| echo "::warning::…"`. In the required `Unit tests (pytest)` job this silently skips four
  default-lane tests (`tests/test_render.py:1552`, `tests/test_signals.py:364`,
  `tests/test_overlay_bands.py:205,386`) rather than failing. Low blast radius (four tests, and
  passwordless sudo is present on ubuntu-latest), but the failure mode is a warning, not red. The
  `coverage` job fails closed on the same condition because the skips would drop `clip_engine` under
  its floor.
- **Nothing machine-checks the required-context list.** `tests/test_ci_config.py` has 22 meta-tests
  about the workflows and **none** compare `docs/BRANCHING.md:100-127` (or the live protection API)
  against the `name:` values of the jobs in `ci.yml`. They match today — verified live via
  `gh api repos/reese8272/creatorclip/branches/main/protection` — but a job rename, a new job, or a
  protection edit drifts silently. A `--require`-style pin here would be cheap.

---

## Off-class

Found while inventorying repo secrets (`gh secret list` / `gh variable list`, 2026-08-17). These are
config facts, not gate defects, and several confirm findings the audit already has:

- **Not set as secrets:** `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`,
  `SENTRY_DSN`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS`, `CC_JWT_SECRET`.
  Set: `ANTHROPIC_API_KEY`, `DEEPGRAM_API_KEY`, `VOYAGE_API_KEY`, `YOUTUBE_API_V3_KEY`,
  `STRIPE_SECRET_KEY`, `GHCR_TOKEN`, `PRODUCTION_URL`, `VPS_*`.
- **Not set as variables:** `CC_CREATOR_ID`, `CC_BASE_URL`, `CC_STAGING_CREATOR_ID`. Only
  `PRODUCTION_URL = https://autoclip.studio` exists.
- Therefore: known finding #6 (prod critical-journey smoke silently downgrades to a warning) is
  **confirmed live** — `CC_JWT_SECRET` is genuinely unset, so `deploy.yml:365-367` takes the WARNING
  branch on every deploy. Known finding #9 (Sentry/OTel dormant) is likewise confirmed at the
  secret-store level. And even if `CC_JWT_SECRET` were set tomorrow, `CC_CREATOR_ID` would still be
  empty, so `llm_harness.py --flow core` would run against a blank creator id.
- `deploy.yml`'s R2 secret sync — added specifically because a prod upload silently failed — is a
  no-op on every deploy, while the same step unconditionally pins `STORAGE_BACKEND=r2`.
