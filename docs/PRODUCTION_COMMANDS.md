# CreatorClip — Production Commands & Skills (Raw)

All skills and commands concatenated in issue-lifecycle order.

---

---
# /issue-workflow
---

Run the full four-phase issue workflow for the issue number or title passed as the argument. Read `docs/SOT.md`, `docs/PROJECT_STATE.md`, `docs/issues.md`, and `docs/DECISIONS.md` before starting Phase 1.

---

## Phase 1 — CHECK (research before any code)

Look up the industry-standard approach for every non-trivial pattern the issue requires. Do not rely on memory — search for current best practices. Then present a brief in this exact format:

> **Issue N — [title]**
> **Approach:** [specific pattern, library, or architecture we'll use]
> **Why for this project:** [1–2 sentences tying it to our stack, constraints, or PRD decisions]
> **Alternatives ruled out:** [what we considered and why it lost]
> **Good to go?**

**Stop here. Do not write implementation code until the user confirms.**

---

## Phase 2 — APPROVE

Wait for explicit confirmation. If the approach changes during the discussion, note the deviation — it belongs in `docs/DECISIONS.md`. "Just go" or "yes" counts as approval.

---

## Phase 3 — BUILD

- Follow all Coding Principles and Production Standards in `CLAUDE.md`
- Write tests alongside the code — not after
- Run the full test suite (`pytest -q`) before moving to Phase 4
- If any test fails, fix it before continuing

---

## Phase 4 — REVIEW & ASSESS

Tests passing is necessary but not sufficient. Run every item in this checklist before marking the issue done.

**Resource lifecycle**
- [ ] All DB connections opened via `get_db()` context manager and guaranteed to close
- [ ] All external clients (Anthropic, HTTP) are module-level singletons, not per-request
- [ ] Temp files or test resources cleaned up after use

**Path and config safety**
- [ ] All file paths are absolute (`Path(__file__).parent / ...`), never relative to CWD
- [ ] All new config values are in `.env.example` with a description
- [ ] Nothing new that belongs in `.gitignore` is left unignored

**Code cleanliness**
- [ ] No `TODO`, commented-out blocks, or leftover debug statements
- [ ] No logic duplicated from an existing function
- [ ] Every new function has a type signature

**Docs**
- [ ] `docs/SOT.md` updated if stack, schema, or file structure changed
- [ ] `docs/DECISIONS.md` updated if implementation diverged from the issue or PRD
- [ ] Pre-monetization items in `CLAUDE.md` updated if this issue resolved any of them

**Close out**
- [ ] All acceptance criteria checked off in `docs/issues.md`
- [ ] `docs/PROJECT_STATE.md` updated with status and session log entry

---

---
# /best-practices (global command)
---

You are a senior software engineer and CS consultant. Your job is to evaluate, guide, or answer questions using industry-standard best practices across every domain of software engineering. When invoked, read the user's request, identify the relevant domain(s), and apply the appropriate standards below. Be specific, cite the principle by name, and give actionable guidance — not platitudes.

If $ARGUMENTS is provided, treat it as the specific topic, question, or code to evaluate.

---

## 1. Core Engineering Principles

### SOLID
- **S — Single Responsibility**: A class or function should have one reason to change. If you need "and" to describe what it does, split it.
- **O — Open/Closed**: Open for extension, closed for modification. Add behavior via composition, inheritance, or parameters — not by editing existing logic.
- **L — Liskov Substitution**: Subtypes must be fully substitutable for their base types. If you override a method and break the parent's contract, you've violated this.
- **I — Interface Segregation**: Don't force a class to implement methods it doesn't use. Prefer narrow, focused interfaces over fat ones.
- **D — Dependency Inversion**: Depend on abstractions, not concrete implementations. Pass dependencies in — don't construct them inside the class.

### DRY (Don't Repeat Yourself)
Extract any logic used more than once. The right unit of extraction is the concept, not the code — if two blocks do the same thing with different variable names, they're duplicates.

### KISS (Keep It Simple)
The simplest solution that satisfies the requirement is the correct one. Complexity is a debt that compounds. Prefer boring, readable code over clever code.

### YAGNI (You Aren't Gonna Need It)
Don't build for imagined future requirements. Three similar cases do not justify an abstraction if you don't know what the fourth will look like. Build what's needed; refactor when the pattern is clear.

### SoC (Separation of Concerns)
Each layer owns one concern: routing owns HTTP, service layer owns business logic, data layer owns persistence. Nothing leaks across. A route handler that does DB queries is a SoC violation.

### Law of Demeter
A method should only call methods on: itself, its parameters, objects it creates, or its direct components. `a.getB().getC().doThing()` is a smell — you're navigating structure you shouldn't know about.

---

## 2. OOP & Design Patterns

### Composition Over Inheritance
Prefer composing behavior from small pieces over deep inheritance hierarchies. Inheritance exposes internals; composition is more flexible and testable.

### Creational Patterns
- **Factory / Factory Method**: When object creation logic is complex or the concrete type varies.
- **Builder**: When constructing objects with many optional parameters. Avoids telescoping constructors.
- **Singleton**: Use sparingly — only for true global state (loggers, connection pools). Test it by injecting the instance, not by calling `getInstance()` everywhere.
- **Dependency Injection**: Prefer constructor injection. Makes dependencies explicit and makes testing straightforward.

### Structural Patterns
- **Adapter**: Wraps an incompatible interface to match what callers expect. Useful at integration boundaries.
- **Decorator**: Adds behavior without modifying the original. Better than subclassing for cross-cutting concerns.
- **Facade**: Provides a simplified interface to a complex subsystem. Use at service layer boundaries.
- **Proxy**: Intercepts calls to control access, add caching, or add logging without changing the target.

### Behavioral Patterns
- **Strategy**: Encapsulate interchangeable algorithms behind a common interface. Pass the strategy in; don't branch on type.
- **Observer**: Decouple event producers from consumers. Pub/sub, event emitters, reactive streams.
- **Command**: Encapsulate a request as an object. Enables undo, queuing, and logging.
- **Repository**: Abstracts data access. Business logic works against an interface; the implementation can swap (DB, cache, API).
- **Template Method**: Define an algorithm skeleton in a base class; subclasses fill in the steps.

### Architectural Patterns
- **Layered / N-Tier**: Presentation → Application/Service → Domain → Data. Dependencies point downward only.
- **Hexagonal (Ports & Adapters)**: Core domain knows nothing about the outside world. Adapters translate. Makes core logic trivially testable.
- **CQRS**: Separate the read model from the write model. Useful when reads and writes have very different performance or complexity profiles.
- **Event Sourcing**: Store state changes as events, not current state. Enables replay, audit, and temporal queries. High complexity — don't default to this.
- **MVC / MVVM / MVP**: Standard UI separation. MVC: Controller coordinates. MVVM: ViewModel exposes bindable state. Use the one your framework expects.

---

## 3. Security

### OWASP Top 10 (current)
1. **Broken Access Control** — Enforce authorization on every request, server-side. Never trust the client.
2. **Cryptographic Failures** — Never store plaintext passwords. Use bcrypt/argon2. TLS everywhere. Don't roll your own crypto.
3. **Injection (SQL, Command, LDAP)** — Parameterized queries always. Never interpolate user input into queries or shell commands.
4. **Insecure Design** — Threat-model before you build. Rate limit, account lockout, and minimal privilege are design decisions, not afterthoughts.
5. **Security Misconfiguration** — Disable debug endpoints in production. Restrict CORS. Set security headers. Remove default credentials.
6. **Vulnerable and Outdated Components** — Pin dependencies and audit regularly (`pip audit`, `npm audit`).
7. **Identification and Authentication Failures** — Short-lived tokens. Refresh rotation. MFA for sensitive actions. Secure password reset.
8. **Software and Data Integrity Failures** — Verify signatures on packages and CI artifacts. Don't blindly trust CDN scripts.
9. **Security Logging and Monitoring Failures** — Log authentication events, access control failures, and input validation failures. Alert on anomalies.
10. **SSRF (Server-Side Request Forgery)** — Validate and allowlist URLs before making server-side HTTP requests.

### General Security Rules
- **Secrets management**: `.env` only, never committed. Use a secrets manager in production (Vault, AWS Secrets Manager, etc.).
- **Least privilege**: Every service, user, and API key should have only the permissions it needs — nothing more.
- **Defense in depth**: Don't rely on one control. Layer authentication, authorization, input validation, and rate limiting.
- **Input validation**: Validate at system boundaries. Use type-safe schemas (Pydantic, Zod, etc.). Never trust user input.
- **Error messages**: Never expose stack traces, internal paths, or DB errors to clients. Log them server-side; return a safe generic message.
- **Security headers**: CSP, X-Frame-Options, X-Content-Type-Options, Strict-Transport-Security, Referrer-Policy.

---

## 4. API Design

### REST Conventions
- Use nouns for resources (`/users`, `/orders`), verbs for actions only when no resource fits.
- HTTP methods as intended: GET (read), POST (create), PUT/PATCH (update), DELETE (remove).
- Correct status codes: 200, 201, 204, 400, 401, 403, 404, 409, 422, 429, 500, 502, 503.
- Idempotency: GET, PUT, DELETE must be idempotent. POST is not.

### Versioning
- Version in the URL (`/v1/`) or via headers. URL versioning is simpler and more explicit.
- Never break existing clients without a deprecation period and a migration path.

### Error Responses
Return structured errors: `{ "error": "code", "message": "human-readable", "request_id": "..." }`. Never return raw exceptions.

### Rate Limiting
Limit by user identity first, IP fallback second. Return `429` with `Retry-After`. Rate limit before hitting expensive resources (LLM calls, DB writes).

---

## 5. Database

### Schema Design
- Normalize to 3NF by default. Denormalize intentionally when read performance demands it.
- Every table needs a primary key. Prefer surrogate keys (auto-increment or UUID) unless natural keys are truly stable and unique.
- Foreign keys should cascade or restrict — never leave orphaned rows silently.

### Query Safety
- Parameterized queries always. Never string-interpolate user input into SQL.
- Index columns used in WHERE, JOIN, and ORDER BY. Profile before adding indexes blindly.
- Use transactions for multi-step writes. Roll back on failure.

### Migrations
- Schema changes go through migration scripts, never manual edits.
- Migrations must be reversible (or explicitly marked as irreversible with justification).
- Test migrations against a copy of production data before running.

### Connection Management
- Use connection pooling. Don't open a new connection per request.
- Set connection timeouts. A hung DB connection should not hang the app.
- In SQLite specifically: enable WAL mode; set `timeout` on connections; use `check_same_thread=False` carefully.

---

## 6. Cloud & Infrastructure

### 12-Factor App
1. Codebase: one repo, many deploys.
2. Dependencies: explicitly declared and isolated.
3. Config: in environment variables, not code.
4. Backing services: treat as attached resources (DB, queue, cache are all the same abstraction).
5. Build/release/run: strictly separated.
6. Processes: stateless and share-nothing. State in backing services.
7. Port binding: self-contained service exports via port.
8. Concurrency: scale out via process model.
9. Disposability: fast startup, graceful shutdown.
10. Dev/prod parity: keep environments as similar as possible.
11. Logs: treat as event streams (stdout); let the platform aggregate.
12. Admin processes: run as one-off processes in the same environment.

### Resilience Patterns
- **Retry with backoff**: Transient failures are normal in distributed systems. Retry with exponential backoff + jitter.
- **Circuit breaker**: Stop calling a failing service after N failures. Fail fast and let it recover.
- **Timeout**: Every external call must have a timeout. Hanging calls are worse than errors.
- **Bulkhead**: Isolate resources per service so one slow dependency can't exhaust the whole pool.
- **Health checks**: Liveness (am I running?) and readiness (am I ready to serve?) are separate concerns.

### IAM / Access Control
- Least privilege on every service account, role, and API key.
- No hardcoded credentials in code or config files.
- Rotate secrets regularly. Revoke immediately on suspected compromise.
- Audit access logs.

---

## 7. Testing

### Test Pyramid
- **Unit (many)**: Test pure functions and domain logic in isolation. Fast, no I/O.
- **Integration (some)**: Test that your code works with real dependencies (DB, external services). Hit a real test DB, not mocks.
- **End-to-end (few)**: Test the full stack through the public API. Slow; cover critical paths only.

### What to Test
- The happy path for every module (the thing it exists to do).
- Boundary values and edge cases that are load-bearing for correctness.
- Error handling at system boundaries (invalid input, external failures).
- Not: implementation internals, every permutation of valid input, behavior guaranteed by the language or framework.

### Test Principles
- Tests must be deterministic. Flaky tests erode trust.
- Each test should test one thing. Assertion bloat hides failures.
- Don't mock the database — integration tests should hit a real (test) DB to catch schema and query bugs.
- Tests are code. Apply the same quality standards: no duplication, clear names, no magic values.

---

## 8. Observability

### Logging
- Use structured (JSON) logging in production. Key fields: `timestamp`, `level`, `message`, `request_id`, `user_id`, `error`.
- Log at appropriate levels: `DEBUG` (dev only), `INFO` (operational events), `WARNING` (unexpected but recoverable), `ERROR` (needs attention).
- Log the what and why of failures, not just that they happened.
- Never log secrets, PII, or full request bodies in production.

### Metrics
- Instrument: request rate, error rate, latency (p50/p95/p99), queue depth, resource utilization.
- Alert on symptoms (high error rate, high latency), not just causes.

### Distributed Tracing
- Propagate a `request_id` (or trace ID) through every service and log entry.
- Correlate logs across service boundaries using this ID.

---

## 9. Code Quality

### Naming
Good names eliminate the need for comments. A function named `get_data()` is a failure; `fetch_user_by_email()` is not. Names should be accurate — if the function does more than the name says, rename or split.

### Function Design
- Functions should do one thing (SRP). If a function is longer than 30 lines, consider splitting.
- Prefer pure functions (no side effects, output depends only on input). Isolate side effects to the edges.
- Limit function parameters. More than 3–4 is a signal the function needs a data object or needs to be split.

### Comments
Only when the WHY is non-obvious: a hidden constraint, a subtle invariant, a known workaround. Code should document the what via naming; comments document the why. Delete stale comments — they're worse than none.

### Cyclomatic Complexity
Keep it low. Deep nesting is hard to reason about. Extract conditions into named variables or helper functions. Prefer early returns (guard clauses) over nested if-else trees.

---

## 10. Dependencies
- Pin versions in production (`==` not `>=`). Unpinned ranges cause silent breakage on redeploy.
- Audit regularly: `pip audit`, `npm audit`. Automate this in CI.
- Minimize dependencies: every dep is a security surface and a maintenance burden. Prefer stdlib where it's sufficient.
- Don't wrap standard libraries: write `datetime.now()`, not a custom `get_current_time()` that wraps it.

---

## 11. Concurrency & Async

### When to Use What
- **Async I/O (`async/await`)**: I/O-bound work — network calls, DB queries, file reads. A single thread handles thousands of concurrent operations by yielding while waiting. Don't use for CPU-bound work — it blocks the event loop.
- **Thread pool**: I/O-bound work that uses blocking libraries (can't be made async). Use `ThreadPoolExecutor`. The GIL (Python) limits CPU parallelism here.
- **Process pool**: CPU-bound work — image processing, data crunching, ML inference. Bypasses the GIL. Use `ProcessPoolExecutor` or `multiprocessing`. Higher overhead.
- **Message queue (Celery, RQ, etc.)**: Long-running or deferrable background work. Decouples task submission from execution.

### Thread Safety
- Shared mutable state across threads is the root of race conditions and deadlocks.
- Use locks (`threading.Lock`) to protect shared state — but minimize the locked region.
- Prefer message passing (queues) over shared memory wherever possible.
- Deadlock happens when two threads each hold a lock the other needs. Prevent by always acquiring locks in a consistent order, or using timeouts.
- Race condition happens when two operations interleave in ways that produce wrong results. Prevent by making operations atomic or serialized.

### Async Pitfalls
- Never `await` inside a CPU-bound loop — you'll starve other coroutines.
- Never call blocking I/O inside an async function without `run_in_executor`.
- `asyncio.gather()` for concurrent coroutines; `asyncio.create_task()` to fire and continue.
- Handle cancellation: tasks can be cancelled; always clean up resources in `finally` or `async with`.

### Immutability as a Default
Prefer immutable data structures. If state can't change, there's nothing to race over. In Python: tuples over lists, frozen dataclasses, avoid global mutable variables.

---

## 12. Performance

### The Cardinal Rule
Profile before you optimize. Premature optimization is the root of most avoidable complexity. Measure; find the actual bottleneck; fix it. `cProfile`, `py-spy`, browser DevTools, `EXPLAIN ANALYZE` in SQL.

### Common Bottlenecks
- **N+1 queries**: Fetching a list of records, then querying the DB once per record. Fix with JOIN or batch fetch. Always check your ORM's query log during development.
- **Missing indexes**: A full table scan on a large table when a WHERE/ORDER BY index would be trivial. Use `EXPLAIN`.
- **Unbounded queries**: No `LIMIT` on a query that could return millions of rows. Always paginate at the DB level.
- **Synchronous I/O in hot paths**: A blocking HTTP call inside a request handler serializes all requests. Make it async or move to a background job.

### Caching Strategy
- **In-process / memoization**: For expensive pure functions with repeated inputs. `functools.lru_cache`. Lives in RAM; lost on restart.
- **Distributed cache (Redis, Memcached)**: Shared across processes/instances. Use for session state, rate limit counters, computed results.
- **HTTP caching (`Cache-Control`, ETags)**: For read-heavy APIs. Let clients and CDNs reuse responses.
- **CDN**: For static assets and geographically distributed reads.
- Cache invalidation is the hard part. Prefer short TTLs + cache-aside over complex invalidation logic.

### Data Volume
- Stream large datasets; don't load them all into memory. Generators in Python, streaming responses in HTTP.
- Compress payloads (gzip/brotli) for large API responses.
- Paginate with cursor-based pagination (not offset) for large, frequently-updated datasets — offsets become stale and expensive.

---

## 13. Error Handling

### Fail Fast
Detect invalid state as early as possible. Validate inputs at the boundary. Raise immediately rather than letting bad data propagate deeper where the root cause becomes obscured.

### Don't Swallow Exceptions
```python
# Bad — the error disappears
try:
    do_thing()
except Exception:
    pass

# Good — at minimum, log it
except Exception as e:
    logger.error("do_thing failed", exc_info=True)
    raise
```
Silent failures are the hardest bugs to diagnose in production.

### Structured Error Types
Define specific exception classes for specific failure modes. `DatabaseConnectionError` is more useful than `RuntimeError("db failed")`. Callers can catch what they can handle and let everything else propagate.

### Retry vs Propagate
- **Retry**: transient failures (network timeout, 503, rate limit 429). Use exponential backoff with jitter. Cap retries (3–5 max).
- **Propagate**: logic errors, bad input, auth failures — these won't succeed on retry.
- **Dead letter queue**: For background jobs, failed tasks that exhaust retries go to a DLQ for inspection, not silent discard.

### Graceful Degradation vs Fail Fast
- **Fail fast**: preferred for data integrity. If you can't write correctly, don't write at all.
- **Graceful degradation**: preferred for user-facing features. If a non-critical component fails (recommendations, analytics), serve the core experience without it. Flag the degradation in logs and metrics.

### Error Context
When catching and re-raising, preserve the chain: `raise NewError("context") from original_error`. The original traceback stays attached.

---

## 14. Git & Version Control

### Commit Hygiene
- **Atomic commits**: one logical change per commit. If the commit message needs "and", it's probably two commits.
- **Conventional Commits format**: `type(scope): short description` — e.g., `feat(auth): add refresh token rotation`, `fix(db): handle null email on login`. Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `perf`.
- Write the commit message for the person who will read `git blame` in 18 months. Describe the why, not the what.
- Never commit secrets, credentials, or `.env` files. If you do, rotate the credentials immediately — git history is permanent.

### Branching Strategy
- **Trunk-based development**: everyone commits to `main` frequently (at least daily), behind feature flags if needed. Preferred for fast-moving teams. Reduces merge hell.
- **Gitflow**: long-lived `develop`, `release`, `hotfix` branches. More process overhead. Better for infrequent, versioned releases.
- Short-lived feature branches (hours to days) are healthier than long-lived ones (weeks). The longer a branch lives, the worse the merge.

### PR Hygiene
- PRs should be small and reviewable in under 20 minutes. If yours takes longer, split it.
- One concern per PR. A refactor PR and a feature PR should not be the same PR.
- Never force-push to `main` or shared branches. Rewrite history only on your own unreviewed branch.
- Resolve review comments, don't just dismiss them.

---

## 15. CI/CD

### Core Principles
- No manual deployments to production. If you can deploy by hand, someone will, and it won't match what CI would have done.
- CI is the gatekeeper. Tests, linting, and security scans run on every PR. Merging a PR that failed CI is a team-level decision, not an individual one.
- Deployment pipeline: build → test → staging deploy → smoke test → production deploy. Each stage is a gate.

### Deployment Strategies
- **Blue/green**: two identical environments; traffic switches atomically. Instant rollback by switching back.
- **Canary**: route a small % of traffic to the new version. Watch metrics. Ramp up or roll back.
- **Feature flags**: decouple deployment (code goes to production) from release (feature is turned on). Lets you dark-launch, A/B test, and kill switches at runtime.

### Rollback
Every deploy must be reversible. Know your rollback procedure before you deploy. Automate it if possible. A rollback that takes hours is not a rollback strategy.

### Environment Parity
Dev, staging, and production should use the same OS, runtime versions, and service configurations. "Works on my machine" is an environment parity failure.

---

## 16. Semantic Versioning & Compatibility

### SemVer: MAJOR.MINOR.PATCH
- **PATCH (1.0.1)**: backwards-compatible bug fix.
- **MINOR (1.1.0)**: backwards-compatible new feature.
- **MAJOR (2.0.0)**: breaking change.

### Rules
- Never silently break a contract. If you remove or change a public API, bump MAJOR and give a deprecation period.
- Deprecation lifecycle: mark `@deprecated` → warn in logs → remove in next major version. At minimum one version warning.
- Internal APIs (not public, not in a library) don't need SemVer, but breaking them still requires coordination.
- Pin your dependencies to a version range you've tested. `^1.2.0` is reasonable for libraries; `==1.2.0` for production deployments.

---

## 17. Functional Programming

### Core Ideas
- **Pure functions**: output depends only on input, no side effects. Easy to test, easy to reason about, safe to parallelize.
- **Immutability**: don't mutate data in place — return new values. Eliminates an entire class of bugs.
- **Side effects at the edges**: the core of your application should be pure functions transforming data. I/O, DB calls, and API calls live at the entry/exit points, not buried inside business logic.
- **Referential transparency**: you can replace a function call with its return value and the program behaves identically. This is the consequence of purity.

### Practical Patterns
- Prefer `map`, `filter`, `reduce` over mutating loops when the intent is transformation.
- Use generators for lazy evaluation of large sequences — they don't build the full collection in memory.
- Avoid global mutable state. Pass state explicitly.
- Higher-order functions (functions that take or return functions) are powerful for composition without subclassing.

### When Not to Go Full FP
Pure FP everywhere in Python is unidiomatic and often slower. Use these principles selectively: keep business logic pure, accept that I/O layers will have side effects, and don't force monadic patterns where a simple `if` works.

---

## 18. AI & LLM Integration

### Context Engineering
The quality of LLM output is almost entirely determined by the quality of context provided. Treat prompt construction with the same care as code.

- Include only what's necessary. Every unnecessary token costs money and dilutes signal.
- Stable system prompts + variable user content. The system prompt is the "policy"; the user turn is the "data." Keep them clearly separated.
- Structure matters. XML tags, headers, and clear delimiters help the model parse complex prompts.
- Be explicit about output format. If you need JSON, say so and give a schema or example.
- Chain of thought for complex reasoning. Ask the model to think step by step before answering.
- Few-shot examples. 2–3 input/output examples in the prompt outperform long instructions for format compliance and style matching.

### Cost Optimization
- **Prompt caching (Anthropic)**: Structure prompts so the large, stable portion (system prompt, long documents) comes first and is reused across calls. Cache hits cost ~10% of normal input tokens.
- **Model selection**: Use the cheapest model that reliably meets quality requirements. Route by task type.
- **Batching**: Use the Batch API for non-real-time workloads. ~50% cost reduction.
- **Output length control**: Set `max_tokens` tightly. Verbose responses cost more and are often lower quality.
- **Cache at the application layer**: If the same prompt is likely to be called repeatedly, cache the LLM response with a TTL.

### Token Management
- Know your model's context window. Don't silently truncate.
- Count tokens before sending, not after.
- For long documents: chunk and summarize, use retrieval (RAG) to fetch only relevant sections.
- Track token usage per call and per user. Log `input_tokens`, `output_tokens`, and `cache_read_tokens`.
- Implement per-user quotas before going to production. An uncapped LLM endpoint will be abused.

### Quality & Evaluation
- Evals are the unit tests of AI features. Before shipping an AI feature, define a small eval set (20–50 examples) with expected outputs and a scoring rubric.
- Golden dataset: Curate a set of real inputs with known-good outputs. Run every prompt change against it.
- Metrics by task type: Classification → precision/recall/F1. Extraction → exact match or ROUGE. Generation → human eval or LLM-as-judge.
- A/B test prompt changes on a sample of real traffic before full rollout.
- Monitor output quality in production: log a sample of inputs/outputs, review them, watch for drift.

### Reliability Patterns
- **Structured output / tool use**: For any LLM call that produces data your code will parse, use JSON mode or tool/function calling. Never parse freeform text if you can avoid it.
- **Retry with backoff on 429 and 529**: Retry with exponential backoff + jitter. Cap at 3–5 attempts.
- **Timeout every call**: A hung LLM call should not hang your server. Set a timeout (30–60s is typical); fail gracefully.
- **Streaming for UX**: For user-facing generation, stream the response.
- **Pin model versions**: Use `claude-sonnet-4-6` not `claude-sonnet-latest`. Model updates can change behavior.
- **Fallback strategy**: If the primary model fails, degrade gracefully — return a cached result, a simpler heuristic answer, or a clear "service unavailable" message.

### Security for AI Features
- **Prompt injection**: User-supplied text that ends up in your prompt can hijack instructions. Sanitize, delimit (`<user_input>` tags), and validate outputs.
- **Never trust LLM output as safe**: Treat LLM output like user input — sanitize before rendering in HTML, validate before using as a DB value, never exec or eval it.
- **Don't leak system prompts**: Sensitive instructions can be extracted with enough prompting. Don't put secrets or internal logic in prompts.

---

## 19. External API Client Patterns

### Every External Call Needs
- **Timeout**: Set connection timeout and read timeout separately.
- **Retry with backoff**: Transient failures should be retried. Use exponential backoff with jitter. Don't retry 4xx errors (except 429).
- **Circuit breaker**: After N consecutive failures, stop calling the service for a cooldown period.
- **Error handling**: Parse error responses. A 400 from an API means something different from a 500.

### Client Architecture
- **Singleton clients**: Initialize HTTP clients and SDK clients once at startup, not per-request.
- Prefer official SDKs over raw HTTP when they exist.
- Centralize external calls: all calls to a given service in one module. Makes it easy to mock, swap, or add observability.

### Auth & Rate Limits
- Store API keys in environment variables, never in code.
- Respect `Retry-After` headers on 429 responses.
- Use idempotency keys for write operations (payment APIs). If you retry a charge without one, you'll double-charge.

### Webhooks
- Validate webhook signatures (HMAC-SHA256) before processing. Never trust an inbound webhook payload without verifying the signature.
- Return `200` immediately, then process async. Webhook delivery has timeouts; slow processing causes retries and duplicate events.
- Make webhook handlers idempotent — you will receive the same event more than once.

---

## How to Apply This Skill

When evaluating a question or code:

1. Identify which domain(s) are relevant.
2. Apply the standards from those sections — cite the principle by name.
3. Give specific, actionable feedback: "This violates SRP because..." or "The industry-standard pattern here is Repository because..."
4. If multiple approaches are valid, state the tradeoff clearly and recommend the one that fits the project's constraints.
5. If the code is already correct, say so. Don't manufacture issues.

---

---
# /best-practices (project skill — SKILL.md)
---

<!-- This skill is deliberately EVERGREEN: it encodes how to find the current
     standard and the durable principles, NOT a frozen list of "current best"
     facts (which rot). Perishable specifics (model ids, tool versions, library
     choices) live in config.py / requirements*.txt and are fetched/verified
     live. See docs/SKILL_FRESHNESS.md. -->

# Best Practices

The One Rule (CLAUDE.md): on every non-trivial decision we **research the current
industry standard FIRST and justify any deviation in `docs/DECISIONS.md`.** This
skill is how that rule is executed. Its value is the *process*, not a snapshot of
2026 opinions.

## Phase-1 procedure (run this before writing code)

1. **Name the decision.** What are we choosing — a library, a pattern, a model, a
   security boundary, a scoring formula?
2. **Research it live.** Use `web_search` (and the `/claude-api` skill for any
   Anthropic SDK decision). Do not answer from memory — your training has a
   cutoff and these facts move. Look for: the current de-facto standard, the
   maintained options, and known failure modes.
3. **Check it against this project.** Does it fit the stack (FastAPI + Celery +
   pgvector + Anthropic/Voyage + R2) and the North Star (deepen the
   channel-knowledge loop)?
4. **Write the CHECK brief** in the CLAUDE.md format (Approach / Why for this
   project / Industry standard checked + source / Alternatives ruled out / Good
   to go?). Record the source + date.
5. **On approval, if it diverges** from the PRD or a prior decision, add a dated
   `docs/DECISIONS.md` entry with what / why / source / alternatives.

## Durable principles (evergreen — safe to apply from memory)

**DRY** — extract any logic used more than once; flag the second occurrence.
**SOLID** — single responsibility, open/closed, Liskov, interface segregation,
dependency inversion. **KISS** — simplest solution wins; no premature
abstraction; a >30-line function that does more than one thing probably splits.

**Production standards** (also enforced mechanically by `/assess` Layer 0):
- No hardcoded secrets; config via `pydantic-settings`; fail-fast on missing required.
- `logging` module only, never `print()`; no PII or token in any log line.
- Pydantic model on every request AND response; correct HTTP status codes; safe error messages (no stack trace / DB error to client).
- Per-creator isolation on every query; parameterized SQL only.
- Resource lifecycle: context-managed DB sessions; module-level singleton external clients; idempotent, retry-safe Celery tasks; temp media cleaned up.
- Type hint on every signature.
- Anthropic SDK: prompt caching (split static/volatile blocks so the cached prefix is actually reused), token usage logged, structured output / token limits — use `/claude-api`.
- No interface or response ever promises virality.

## Where the PERISHABLE facts live (never restated here)

| Fact | Single source | How it stays fresh |
|---|---|---|
| Anthropic model id, web_search tool version | `config.py` (`ANTHROPIC_MODEL`, `ANTHROPIC_WEB_SEARCH_TOOL`) | verify vs live catalog via `/claude-api` before launch |
| Tool / lib versions | `requirements.txt`, `requirements-dev.txt` | `pip-audit` (live CVEs) + scheduled bump |
| "Current best library for X" | not stored — researched per decision | Phase-1 `web_search`, recorded in DECISIONS.md |

If you find a perishable fact hardcoded in code or restated in a skill, that is a
finding: hoist it to its single source.

## Cadence

- **Every non-trivial issue:** Phase-1 procedure above.
- **Quarterly (or when `last_verified` is stale):** re-verify the perishable sources with `deep-research`/`web_search`, then bump `last_verified` here and in `production-assessment`. The `run_layer0.py` freshness gate surfaces staleness.

---

---
# /assess (command wrapper)
---

Run the **production-assessment** skill end to end.

1. Read `.claude/skills/production-assessment/SKILL.md` and follow it exactly.
2. Run Layer 0 (`scripts/run_layer0.py`), then dispatch the Layer-1 subagents in
   parallel (one per module, each writing to `docs/assessment/modules/`), then
   produce the Layer-2 verdict in `docs/assessment/REPORT.md` and snapshot it to
   `docs/assessment/history/`.
3. End by showing me the VERDICT line, the ranked register top 10, and the diff
   vs the previous report.

If `$ARGUMENTS` names a single module (e.g. `worker`), assess only that module:
run Layer 0, dispatch just that one subagent, and update its findings file —
skip the full verdict regeneration.

---

---
# /assess (production-assessment SKILL.md)
---

<!-- last_verified is the freshness anchor: see docs/SKILL_FRESHNESS.md. This skill
     is mostly evergreen process; its only perishable content is the tool pins in
     requirements-dev.txt. The run_layer0.py freshness gate flags this file when
     last_verified is >90 days old. -->

# Production Assessment

A three-layer, context-bounded, repeatable assessment. The governing principle:

> **Tools provide exhaustiveness. Claude provides judgment. Never ask Claude to
> be exhaustive.**

A whole-codebase sweep in one context is the wrong primitive — it is
non-deterministic, unrepeatable, and its recall *drops* as the repo grows. This
skill instead pushes everything mechanizable into a script (perfect recall, zero
tokens) and reserves the model for per-module judgment, dispatched as parallel
subagents that write to disk. The orchestrator reads only short findings files,
never the source — so context stays flat from 16k LOC to 160k.

---

## Inputs / outputs

- Reads: the repo, plus the previous `docs/assessment/REPORT.md` (for diffing).
- Writes:
  - `docs/assessment/_machine.json` — Layer 0 deterministic results
  - `docs/assessment/modules/<module>.md` — one findings file per subagent
  - `docs/assessment/REPORT.md` — ranked register + production-ready verdict
  - `docs/assessment/history/<date>-REPORT.md` — immutable snapshot of this run

---

## Procedure

Run the three layers in order. Do **not** skip Layer 0 — its JSON is the input
the verdict is built on.

### Layer 0 — deterministic floor (the script)

Run the harness. It executes ruff, mypy, pytest-cov, bandit, and pip-audit,
compares each against the committed baselines, and writes `_machine.json`:

```bash
python3 .claude/skills/production-assessment/scripts/run_layer0.py
```

Read `docs/assessment/_machine.json` (small) — **do not** read raw tool output.
Note any gate that regressed against `docs/assessment/baselines.json`, and the
ranked untested-code list from the coverage section.

To re-baseline after fixing or after the first run (captures current reality as
the new floor):

```bash
python3 .claude/skills/production-assessment/scripts/run_layer0.py --update-baseline
```

### Layer 1 — map-reduce judgment (parallel subagents)

For each module below, dispatch **one `Explore`/`general-purpose` subagent in
parallel** (all in a single message). Hand each subagent ONLY:
its slice + `rubric.md` + `subagent-contract.md`. Each subagent writes
`docs/assessment/modules/<module>.md` and returns to you only a 3-line summary
(see the contract). You never read the source yourself.

Modules (slice by existing boundaries):
`clip_engine/`, `dna/`, `preference/`, `youtube/`, `worker/`, `routers/`,
`ingestion/`, `billing/`, `upload_intel/`, `improvement/`, and
`_root_infra` (= `db.py`, `crypto.py`, `config.py`, `auth.py`, `limiter.py`,
`models.py`, `main.py`).

If the repo has grown, add a module per new top-level package — the pattern
scales by adding subagents, not by enlarging any context.

### Layer 2 — verdict

Read `_machine.json` + every `docs/assessment/modules/*.md` + `scale-checklist.md`.
Produce `docs/assessment/REPORT.md` using the template in `report-template.md`:

1. A single **PRODUCTION-READY: YES / CONDITIONAL / NO** verdict.
2. A ranked register (BLOCKER → SEV1 → SEV2 → cleanup), each row with
   `module | file:line | issue | backed fix`.
3. The `scale-checklist.md` axes, each marked ✅ / ⚠️ / ❌ with evidence.
4. A **diff vs the previous REPORT.md** — what's new, fixed, regressed.

Then copy the report to `docs/assessment/history/<YYYY-MM-DD>-REPORT.md`.

A finding is not done until it has a *backed* fix — a concrete design with a
source or a number (pool math, an index, a config value), never just a
complaint. Cite `scale-checklist.md` sections where relevant.

---

## Cadence (how this stays repeatable, not heroic)

- **Per commit / PR:** Layer 0 runs in CI (`.github/workflows/quality.yml`). Cheap.
- **Per PR diff:** `/code-review` + `/security-review` on the diff only.
- **Per milestone / pre-launch:** full `/assess` (all three layers) → REPORT.md.
- **Pre-launch + after infra change:** Locust run (`tests/perf/`) for real
  concurrency evidence (the one thing reading cannot produce).

---

---
# /assess (rubric.md — Layer-1 subagent lens)
---

# Per-Module Assessment Rubric

This is the fixed lens every Layer-1 subagent scores its module against. It is
drawn directly from the CLAUDE.md Phase-4 REVIEW checklist so that the assessment
and the per-issue workflow measure the same things. Score every applicable item;
mark `n/a` with one word of reason when a category does not apply to the module.

Severity scale:
- **BLOCKER** — ships a bug, leak, or outage at scale; must fix before launch.
- **SEV1** — correctness/security defect that will bite under load or over time.
- **SEV2** — real defect, bounded blast radius, fix soon.
- **cleanup** — DRY/KISS/typing/naming; no behavior risk.

---

## 1. Resource lifecycle
- DB sessions acquired via context manager, guaranteed close on every path
  (including exceptions / early return).
- External clients (Anthropic, Voyage, YouTube, R2/storage) are module-level
  singletons, not per-call constructions.
- Celery tasks idempotent under at-least-once delivery and safe to run twice
  concurrently; temp media cleaned up in a `finally`.
- No connection / file handle / subprocess leak on the error path.

## 2. Concurrency & scale (load-bearing for hundreds of users — see scale-checklist.md)
- No sync/blocking call hidden inside an `async def` (requests, time.sleep,
  subprocess.run, blocking DB driver, heavy CPU on the loop thread).
- Shared async resources (engine/pool, redis client) bound to the right loop;
  not recreated per request/task.
- Queries that run per-request are indexed for the access pattern; no N+1.
- Bounded work: no unbounded `fetchall`, no unbounded fan-out, no unbounded
  in-memory accumulation of per-creator data.

## 3. Security & compliance (load-bearing)
- OAuth tokens read via `decrypt()`; never logged, never returned in a response.
- No PII or secret in any log line (grep the module's `logger.*` calls).
- **Per-creator isolation on EVERY query** touching a creator-scoped table —
  a missing `WHERE creator_id = ?` is a cross-tenant leak. Treat as BLOCKER.
- Parameterized SQL only; no f-string/`%`-built queries.
- YouTube ToS / retention respected; source-media purge honored.
- No virality promise in any string, response, or prompt.

## 4. Clip-quality correctness (clip_engine / dna / preference only)
- Clip start anchored to the setup (backward look from peak), not the aftermath.
- Every score cites a named principle from `docs/CLIPPING_PRINCIPLES.md`.
- Ranking is against THIS creator's DNA + audience, not a generic score.
- Preference model applies exponential recency decay; below-threshold fallback
  to DNA + signals is honest and explicit.

## 5. Anthropic SDK usage (any module calling the LLM)
- Prompt caching used (mandatory per architecture).
- Token usage logged after every call.
- Structured output / token limits set; web-search tool used where live research
  is intended.

## 6. Code cleanliness & typing
- No TODO, no commented-out code blocks, no `print()`/debug statements.
- No duplicated logic (DRY) — flag the second occurrence with a pointer to the first.
- Every function signature typed (CLAUDE.md mandates this — the mypy gate enforces
  it mechanically, but flag obvious gaps the gate hasn't caught yet).
- Functions over ~30 lines that do more than one thing (KISS / single responsibility).

## 7. Error handling & API surface (routers only)
- Pydantic model on every request and response.
- Correct HTTP status codes (200/400/401/404/422/500).
- Error messages safe — no stack trace, no DB error, no internal detail to client.

## 8. Config & paths
- All paths absolute.
- Any new config present in `.env.example` with a description.
- Fail-fast on missing required config (pydantic-settings).

---

## What NOT to flag
- Style the formatter/linter already owns (line length, quotes, import order) —
  ruff handles it; do not duplicate.
- Speculative abstractions for scale that isn't in the PRD ("you might one day
  need…") — KISS. Flag only concrete, present defects.

---

---
# /assess (subagent-contract.md — Layer-1 output format)
---

# Layer-1 Subagent Contract

You are assessing exactly ONE module of the CreatorClip codebase. Stay inside
your slice. Do not read or comment on other modules — another agent owns each.

## Your inputs
- The file paths of your assigned module (your slice).
- `rubric.md` — the fixed lens. Score every applicable category.
- `docs/CLIPPING_PRINCIPLES.md` and `docs/COMPLIANCE.md` if your module is in
  `clip_engine/`, `dna/`, `preference/`, `ingestion/`, or `youtube/`.

## Your method
1. Read every file in your slice. For each, walk the rubric categories in order.
2. For each finding, identify the exact `file:line`, the rubric category, a
   severity (BLOCKER / SEV1 / SEV2 / cleanup), and a **concrete fix** — what to
   change, not just what is wrong. If the fix is a scale design (pool size,
   index, idempotency key), give the actual value or shape.
3. Verify load-bearing claims by reading, not assuming: trace token handling to
   `decrypt()`, trace creator-scoped queries to their `WHERE`, trace async paths
   for hidden blocking calls.
4. Be honest about uncertainty — mark a finding `(needs-runtime-confirmation)`
   rather than asserting something a load test would settle.

## Your output — write this file, then return only the 3-line summary

Write `docs/assessment/modules/<module>.md` with EXACTLY this structure:

```markdown
# <module> — assessed <YYYY-MM-DD>

## Findings
- [BLOCKER] routers/clips.py:88 — list endpoint missing `WHERE creator_id` →
  cross-tenant leak | fix: add `.where(Clip.creator_id == current.creator_id)`;
  add regression test asserting creator B cannot read creator A's clip.
- [SEV1] worker/tasks.py:142 — `requests.get(...)` inside `async def _signals` →
  blocks the event loop under concurrency | fix: move to httpx.AsyncClient
  singleton, or run in a threadpool via `asyncio.to_thread`.
- [SEV2] dna/build.py:60 — duplicated normalization of dna/score.py:21 (DRY) |
  fix: extract `_normalize_weights()` into dna/util.py.
- [cleanup] config.py:14 — `get_settings()` return type missing (typing) |
  fix: annotate `-> Settings`.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok / N findings |
| 2 Concurrency & scale | ... |
| 3 Security & compliance | ... |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | ... |
| 6 Cleanliness & typing | ... |
| 7 Error handling / API | ... |
| 8 Config & paths | ... |

## Module verdict
<one of: clean | NEEDS-WORK | has BLOCKER> — <one sentence>
```

## The 3-line summary you return to the orchestrator (and nothing else)
```
<module>: <verdict>
blockers: <n>  sev1: <n>  sev2: <n>  cleanup: <n>
top: <the single most important finding, one line>
```

Returning the full findings into the orchestrator's context defeats the purpose.
The file on disk is the record; your return value is the index entry.

---

---
# /tdd
---

You are enforcing a strict Red-Green-Refactor loop. No exceptions.

## The Loop

### RED — Write a failing test first
- Write the minimum test that captures the next behavior to implement.
- Run the test. Confirm it fails for the right reason (not a syntax error or import issue).
- Do not write implementation code until the test is failing and the failure message makes sense.

### GREEN — Write the minimum code to pass
- Write only enough code to make the failing test pass.
- Do not add anything not required by the current test.
- Run the test. Confirm it passes.

### REFACTOR — Clean without changing behavior
- Improve naming, structure, or clarity.
- Run tests again after every change. All must stay green.
- Only refactor what is genuinely unclear or messy. Don't gold-plate.

## Rules
- If I ask you to skip ahead to implementation, refuse and explain why the test must come first.
- If a test passes without a failing state first, flag it — that test may not be testing anything real.
- One behavior per loop. If we're tempted to write multiple tests at once, pick the most fundamental one and do the others after.
- At the end of each loop, state: what we just tested, whether we're green, and what the next behavior to test is.

## Starting
Ask: "What is the first behavior we need to verify? Describe it in plain English before we write any code."

---

---
# /production-code
---

Audit the codebase for code quality against the project's engineering standards (CLAUDE.md). Read every Python source file before reporting. If $ARGUMENTS is provided, scope the audit to that file or directory.

## Checks to run

### Logging
- [ ] No `print()` calls used for output — only `logging.getLogger(__name__)` with appropriate levels
- [ ] All errors logged at `logging.error()` or `logging.exception()`; routine events at `logging.info()`
- [ ] Logger initialized at module level, not per-function

### Type annotations
- [ ] Every function has a return type annotation
- [ ] Every function parameter has a type annotation
- [ ] `dict | None` and `list[str]` syntax used (Python 3.10+), not `Optional[dict]` or `Dict[str, Any]`

### Hardcoded values
- [ ] No magic numbers (non-obvious numeric literals without a named constant)
- [ ] No hardcoded strings that belong in config (URLs, model names should come from module-level constants)
- [ ] Model name pinned to a `MODEL` constant, not inline

### Resource lifecycle
- [ ] All DB connections opened via `with get_db() as conn:` — none opened without a context manager
- [ ] All external SDK clients (Anthropic, Stripe, etc.) are module-level singletons
- [ ] No per-request client instantiation

### Cleanliness
- [ ] No `TODO`, `FIXME`, `HACK`, or `XXX` comments
- [ ] No commented-out code blocks
- [ ] No debug statements (`breakpoint()`, `pdb`, `ic()`)
- [ ] No unused imports (`grep -n "^import\|^from" --include="*.py" -r .` then verify each is used)

### DRY
- [ ] Is any function body duplicated (even partially) across files?
- [ ] Is any SQL query repeated verbatim in more than one place?
- [ ] Is any error-handling pattern copy-pasted instead of extracted?

### Function size and responsibility
- [ ] Are any functions longer than ~30 lines? (Flag for review — not always wrong, but usually a sign of multiple responsibilities)
- [ ] Does each function do exactly one thing?

### Error handling
- [ ] Are `except Exception` (bare) clauses limited to top-level handlers? Internal code should catch specific exceptions.
- [ ] Do all `HTTPException` raises use the correct status code (400, 401, 404, 422, 502)?
- [ ] Are upstream errors (Anthropic API, DB failures) caught and returned as safe 502/500 responses?

### HTTP status codes reference
| Situation | Code |
|---|---|
| Bad user input | 400 |
| Missing / invalid auth token | 401 |
| Valid token, insufficient permissions | 403 |
| Resource not found | 404 |
| Pydantic validation failure | 422 (FastAPI default) |
| Rate limit exceeded | 429 |
| Upstream API failure | 502 |
| Unexpected server error | 500 |

## Output format

List every file audited. For each violation, output: `filename:line — rule — description`. End with a count of violations per category. If $ARGUMENTS scopes the audit, only report on those files.

---

---
# /production-tech
---

Audit the infrastructure and deployment configuration of this project against production standards. Read `docs/SOT.md`, `docs/SAAS_ROADMAP.md`, and the current file structure before starting.

## Checks to run

### Database
- [ ] Is the database SQLite? If so, flag it — SQLite cannot handle concurrent writes at scale. Recommended path: Postgres via Supabase, Railway, or Render.
- [ ] Is connection pooling configured? (Required for Postgres; SQLite is single-connection.)
- [ ] Is WAL mode enabled on every connection? (For SQLite only — check `db.py`.)
- [ ] Are there automated, tested backups? Verify the backup strategy exists and is documented.

### Hosting & deployment
- [ ] Is the app exposed via a dev tunnel (Cloudflare Tunnel, ngrok)? This is not acceptable for production — flag as BLOCKED.
- [ ] Is there a `Procfile`, `Dockerfile`, or host-specific config file for deployment?
- [ ] Is there a staging environment separate from production?
- [ ] Are all secrets in environment variables, never in code or config files?
- [ ] Is the `.env` file in `.gitignore`? Verify by running `git check-ignore .env`.

### CI/CD
- [ ] Is there a `.github/workflows/` directory or equivalent CI config?
- [ ] Do automated tests run on every push?
- [ ] Is deployment to staging/prod automated or documented?

### Health check
- [ ] Does `GET /health` verify database connectivity (not just return `{"status": "ok"}`)?
- [ ] Is `/health` excluded from auth requirements so monitoring tools can reach it?

### Performance
- [ ] Are there per-user rate limits on expensive endpoints (`/analyze`, `/analyze-reverse`, `/hotspot`)?
- [ ] Is the result cache being used effectively? Check cache hit/miss ratio in logs.
- [ ] Are Anthropic SDK clients module-level singletons (not created per-request)?

## Output format

Report each check as PASS / FAIL / NOT APPLICABLE with a one-line note on what to do for each FAIL. Group by section. End with the highest-severity finding.

---

---
# /code-review
---

Review the code as a senior engineer would — not just for correctness, but for maintainability, clarity, and production-readiness.

## Review Dimensions

**Correctness**
- Does the code do what it claims to do?
- Are edge cases handled (empty input, None, unexpected types, network failures)?
- Are there off-by-one errors, race conditions, or logic gaps?

**Clarity**
- Can someone unfamiliar with this code understand it in 5 minutes?
- Are names (variables, functions, classes) accurate and descriptive?
- Is anything doing too much? Should anything be broken up?

**Robustness**
- What happens when this fails? Is the failure visible and recoverable?
- Are errors handled specifically, or swallowed silently?
- Is logging present where it matters?

**Pythonic quality** (for Python codebases)
- Is the code idiomatic? Would a Python developer feel at home here?
- Are there unnecessary loops, redundant conditionals, or missed standard library tools?

**For FastAPI/LangGraph/RAG projects specifically**
- Are route handlers thin? Is business logic in the right layer?
- Are LangGraph state schemas typed and minimal?
- Is any retrieval or generation logic leaking across layer boundaries?

## Output Format

**Must fix** — Bugs, logic errors, or anything that will cause production problems.
**Should fix** — Clarity or robustness issues worth addressing before merge.
**Consider** — Low-priority improvements or stylistic suggestions. Not blocking.
**What's solid** — Specific things done well. Be concrete, not generic.

## Rules
- Be specific. Reference the function or line being discussed.
- Prioritize ruthlessly. Not everything needs to be in "must fix."
- If the code is genuinely good, say so. Don't manufacture criticism.

---

---
# /production-security
---

Audit this project's security posture against production standards. Read `auth.py`, `main.py`, and `routers/` before starting. Check `requirements.txt` for dependency versions.

## Checks to run

### Secrets management
- [ ] Are any API keys, JWT secrets, or passwords hardcoded in source files? (`grep -r "sk-ant\|secret\|password" --include="*.py"`)
- [ ] Is `.env` in `.gitignore`? (`git check-ignore .env`)
- [ ] Is `.env.example` present with key names but no values?
- [ ] Are secrets loaded via `os.getenv()` or `python-dotenv`, never via string literals?

### Authentication
- [ ] Do JWTs have an expiry (`exp` claim)? What is the configured TTL?
- [ ] Is there a token refresh flow? If not, users get hard-logged-out — flag as a gap.
- [ ] Are passwords hashed with bcrypt (work factor ≥ 12)?
- [ ] Is there a minimum password length enforced server-side?
- [ ] Does the login endpoint return the same error for "user not found" and "wrong password" (no user enumeration)?

### Input validation
- [ ] Does every POST/PUT endpoint use a Pydantic model? List any that don't.
- [ ] Are string lengths bounded on user-supplied fields (location, tags, notes, wants)?
- [ ] Are SQL queries parameterized everywhere? (`grep -n "%" --include="*.py" -r routers/ main.py` — look for string interpolation in SQL)

### HTTP security headers
- [ ] Are the following headers set on every response?
  - `Strict-Transport-Security` (HSTS)
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: DENY`
  - `Content-Security-Policy`
  - `Referrer-Policy: no-referrer`
- [ ] If not, a FastAPI middleware block in `main.py` can add all of them in ~10 lines.

### CORS
- [ ] Is `ALLOWED_ORIGINS` set to `*`? This must be changed to the exact production domain before launch.
- [ ] Are only necessary HTTP methods listed in `allow_methods`?

### Webhook security
- [ ] If Stripe webhooks are implemented, is `stripe.Webhook.construct_event()` used to verify the `Stripe-Signature` header? Unsigned events must return 400.

### Dependencies
- [ ] Run `pip audit` (or `pip-audit`) and report any CVEs in installed packages.
- [ ] Are all dependencies pinned to exact versions in `requirements.txt`?

### Error exposure
- [ ] Do error responses expose stack traces, internal paths, or DB error messages to clients?
- [ ] Are all 5xx responses returning generic messages (no internal detail)?

## OWASP Top 10 quick check
| Risk | Status | Notes |
|---|---|---|
| A01 Broken Access Control | ? | Check: can user A read user B's saved results? |
| A02 Cryptographic Failures | ? | bcrypt for passwords, HS256 JWT — check key strength |
| A03 Injection | ? | SQL parameterized? No shell exec? |
| A05 Security Misconfiguration | ? | CORS *, missing headers, debug mode |
| A07 Identification & Auth Failures | ? | JWT expiry, no refresh, user enum |

## Output format

Report each check as PASS / FAIL / UNKNOWN. For FAILs, name the exact file and line to fix. Flag any BLOCKED items (must fix before public traffic) at the top.

---

---
# /production-standard
---

Run a full production-readiness audit across all five dimensions. For each area, report: what passes, what is missing or broken, and the specific fix needed. Be explicit — name the file and line where action is required.

## What to audit

### 1. Tech (`/production-tech`)
- Database: is SQLite being used in a context that requires concurrent writes?
- Hosting: is the app exposed via a dev tunnel or running on a real host?
- Backups: is there an automated backup strategy for the database?
- CI/CD: are tests run automatically on push? Is deployment automated?
- Staging: is there a staging environment separate from production?
- Health check: does `/health` verify DB connectivity, or just return 200?

### 2. Security (`/production-security`)
- Secrets: are any secrets hardcoded or committed?
- HTTP headers: are HSTS, X-Frame-Options, X-Content-Type-Options, CSP present?
- CORS: is `ALLOWED_ORIGINS=*` in use? It must be locked to the production domain before launch.
- Auth: does JWT have expiry? Is there a refresh flow? Are passwords strength-validated?
- Input validation: are all user inputs validated at the boundary (Pydantic models on every endpoint)?
- Dependencies: run `pip audit` and report any known vulnerabilities.
- Webhook signatures: are any inbound webhooks (Stripe, etc.) verifying signatures?

### 3. Code quality (`/production-code`)
- No `print()` for logging — only `logging` module
- All functions have return type annotations
- No hardcoded values (magic numbers, API keys, config strings)
- No TODO / commented-out / debug code in the working tree
- All DB connections use the context manager (`with get_db()`)
- External clients (Anthropic, HTTP) are module-level singletons, not per-request
- No logic duplicated across files

### 4. Engineering principles (`/production-principles`)
- Single Responsibility: do any functions exceed ~30 lines or do more than one conceptual thing?
- DRY: is any logic copy-pasted across files?
- Error handling: are errors caught at the right boundary and returned as safe HTTP responses?
- Status codes: are the correct HTTP codes used (400 bad input, 401 unauth, 404 not found, 422 validation, 502 upstream)?
- Logging levels: are errors logged at ERROR, warnings at WARNING, routine events at INFO?

### 5. Process (`/production-process`)
- Is `docs/SOT.md` current with the actual file structure and API surface?
- Is `docs/DECISIONS.md` updated for every deviation from the PRD?
- Is `docs/PROJECT_STATE.md` current?
- Are all acceptance criteria checked off for closed issues?
- Is the full test suite passing?
- Are all new features covered by at least one test?

## Output format

For each area, output a table:

| Check | Status | Action required |
|---|---|---|
| No hardcoded secrets | PASS | — |
| HTTP security headers | FAIL | Add middleware in main.py:51 |

Finish with a one-line verdict: **READY**, **NEEDS WORK**, or **BLOCKED** (blocked = a hard blocker that must be fixed before any public traffic).

---

---
# /production-principles
---

Audit the codebase against fundamental software engineering principles. This skill looks at the *shape* of the code — responsibilities, coupling, abstraction — not syntax or style (that's `/production-code`). If $ARGUMENTS is provided, scope to that file or module.

## SOLID

### Single Responsibility
- [ ] Does each module (`auth.py`, `cache.py`, `scoring.py`, etc.) have exactly one reason to change?
- [ ] Does each function do exactly one conceptual thing?
- [ ] Are any route handlers doing business logic that belongs in a helper function?
  - Example: computing scores inline in a route instead of calling `scoring.py`

### Open/Closed
- [ ] Can new expense categories, tier names, or scoring weights be added without editing existing logic?
- [ ] Are conditionals like `if category == "rent"` scattered through the code, or centralized in config/constants?

### Liskov Substitution
- [ ] Do any subclasses or implementations of a type break the contract of the parent? (Flag any `isinstance` checks that exist to work around a type violation)

### Interface Segregation
- [ ] Does any module import from another module and use only one function out of many? If so, consider whether the dependency is appropriate.
- [ ] Are Pydantic models scoped to their use case (request, response, DB row) or are they overloaded?

### Dependency Inversion
- [ ] Do route handlers depend on concrete implementations (e.g., `_anthropic_client.messages.create` called directly in the handler) or on abstractions?
  - For a small project, direct calls are acceptable — but flag if the same call pattern appears in 3+ places without extraction.

## DRY (Don't Repeat Yourself)

- [ ] Is any block of logic (≥3 lines that do the same thing) duplicated across files?
- [ ] Are there multiple places that build a prompt for the same endpoint? (Should be one `_build_*_prompt` function per endpoint)
- [ ] Is the JSON extraction pattern (`raw.find("{")`, `json.loads(...)`) duplicated? (Should be one `_extract_json` helper)
- [ ] Are BLS weights or national average constants defined in more than one place (Python and JS)?

## KISS (Keep It Simple)

- [ ] Are there abstractions that exist "for future use" with no current use case?
- [ ] Are there functions with more parameters than necessary?
- [ ] Is any data transformed through more steps than the problem requires?
- [ ] Is the simplest solution being used, or was complexity added that the acceptance criteria don't require?

## Error boundary discipline

Production systems handle errors at well-defined layers. Check:

| Layer | Responsibility | How to verify |
|---|---|---|
| Pydantic models | Reject invalid input shapes | Every endpoint has a request model |
| Route handler | Catch upstream errors (Anthropic, DB), return safe HTTP responses | `try/except anthropic.APIError` around every API call |
| `get_db()` | DB connection lifecycle | All DB access inside `with get_db()` |
| Global handler | Catch anything that slipped through | FastAPI exception handler registered in `main.py` |

- [ ] Is there a global exception handler registered? If not, unhandled errors return FastAPI's default 500 with a stack trace — flag this.

## Naming

- [ ] Are function names verbs that describe what they do (`compute_score`, `get_cached`, not `score` or `data`)?
- [ ] Are variable names self-documenting (no single-letter names outside loop indices)?
- [ ] Are constants `UPPER_SNAKE_CASE`, functions/variables `snake_case`, JS `camelCase`?

## Output format

Group findings by principle. For each violation: `filename:line — principle — what the problem is and the simplest fix`. Flag anything that would make the codebase materially harder to extend or debug.

---

---
# /production-process
---

Audit the project's development process health. Read `docs/PROJECT_STATE.md`, `docs/SOT.md`, `docs/DECISIONS.md`, `docs/issues.md`, and run `git log --oneline -20` before starting.

## Issue workflow (Check → Approve → Build → Review)

Every issue must clear all four phases before being closed. Audit recently closed issues:

- [ ] Was each issue researched before implementation began? (Check → Approve)
- [ ] Was implementation code written alongside tests, not after?
- [ ] Was the full test suite run and passing before the issue was closed?
- [ ] Were all acceptance criteria checked off in `docs/issues.md`?

## Documentation currency

- [ ] `docs/SOT.md` reflects the actual current file structure and API surface (no phantom files, no missing routes)
- [ ] `docs/PROJECT_STATE.md` is up to date — matches the actual issue completion status
- [ ] `docs/DECISIONS.md` has an entry for every deviation from the PRD or issues spec
- [ ] `docs/issues.md` has all acceptance criteria checked off for every closed issue
- [ ] No `TODO` or "TBD" left in any doc file

## Phase 4 hygiene checklist (run before closing any issue)

**Resource lifecycle**
- [ ] All DB connections use `get_db()` context manager
- [ ] External clients are module-level singletons
- [ ] Temp files and test resources cleaned up after use

**Path and config safety**
- [ ] All file paths are absolute (`Path(__file__).parent / ...`)
- [ ] New config values are in `.env.example` with a description
- [ ] Nothing new that belongs in `.gitignore` is left unignored

**Code cleanliness**
- [ ] No `TODO`, commented-out blocks, or leftover debug statements
- [ ] No logic duplicated from an existing function
- [ ] Every new function has a type signature

**Docs**
- [ ] `docs/SOT.md` updated if stack, schema, or file structure changed
- [ ] `docs/DECISIONS.md` updated if implementation diverged from the issue or PRD

## Test suite health

- [ ] Run `pytest -q` — all tests must pass before any issue is closed
- [ ] Every new endpoint or function added in the last 3 issues has at least one test
- [ ] No tests are marked `skip` or `xfail` without a documented reason and removal condition
- [ ] Test DB is a real SQLite temp DB, not a mock (check `tests/conftest.py`)

## Git hygiene

- [ ] No secrets in git history (`git log --all --full-history -- .env`)
- [ ] Commit messages describe *why*, not just *what*
- [ ] No force-pushes to main without explicit justification

## When to create a DECISIONS.md entry

Create an entry any time:
- Implementation diverges from what the issue spec says
- A library or pattern is chosen over a well-known alternative
- Something is explicitly descoped
- A bug or external constraint forces a different approach than planned

Do NOT create entries for: routine implementation choices, things already in the PRD verbatim, or decisions with no meaningful alternatives.

## Output format

Report each check as PASS / FAIL / UNKNOWN. For FAILs, be specific: which issue, which doc, which line. Flag anything that would block a new issue from starting at the top.

---

---
# /close-out
---

Write or refresh `LEFT_OFF.md` at the repository root so a brand-new Claude context — with zero
memory of this session — can read that one file and resume exactly where we left off.

`LEFT_OFF.md` is a **living handoff contract**, not a changelog and not a source of truth. It
orients the next session and points to the canonical docs; it never duplicates them.

Optional emphasis passed by the user: $ARGUMENTS

---

## Phase 1 — Gather real state (do not guess)

Run these and read the output before writing anything:

- `git rev-parse --abbrev-ref HEAD` + `git log -6 --oneline` — current branch and recent commits
- `git status --short` — uncommitted / untracked changes
- `git rev-list --left-right --count @{u}...HEAD` if there's an upstream; and if an `origin/main`
  exists, `git rev-list --left-right --count origin/main...HEAD` — am I ahead/behind the trunk?
- If this is a GitHub repo: `gh run list --limit 5` — current CI / deploy status
- Read the existing `LEFT_OFF.md` if present — refresh it; keep whatever is still true

Then extract from **this conversation** (only you have it): the single active goal, the in-flight
blocker if any, the exact next action, and anything already proven that shouldn't be re-debugged.

## Phase 2 — Write `LEFT_OFF.md` at the repo root

Use real values from Phase 1. Never invent IDs, hosts, URLs, or SHAs. Keep it tight and high-signal
— a reader should orient in under a minute. Include these sections:

1. **Header** — `Last updated` (today's date), branch + HEAD short-sha, working-tree state.
2. **CURRENT FOCUS** — the ONE isolated goal in a sentence, then **→ NEXT ACTION**: a numbered list
   of the precise steps to resume (exact commands, what to check). If blocked, name the blocker and
   its most likely cause.
3. **WHAT WORKS NOW** — what's done/verified, so the next session doesn't re-investigate it.
4. **THE ARC THAT LED HERE** — a short numbered history of how this goal came to be.
5. **KEY COORDINATES & FACTS** — a table of load-bearing specifics: URLs, IDs, hosts, branch,
   image/artifact names, where credentials live (by **name only**, never values).
6. **CONSTRAINTS & GOTCHAS** — non-obvious traps for the next session (e.g. "verify origin/main is
   current first", "secrets are write-only/unreadable", "pushing to main triggers a deploy").
7. **POINTERS** — links to the repo's canonical source-of-truth docs and any memory directory.

## Phase 3 — Keep things honest

- If the repo has a source-of-truth doc set (e.g. `docs/SOT.md`, `PROJECT_STATE.md`), make sure
  `LEFT_OFF.md` is consistent with them and registered in the file layout if the project tracks one.
  Don't duplicate their content — point to them.
- If you ran this as the close-out of an issue workflow (where you're already committing doc/state
  updates), include `LEFT_OFF.md` in that same commit. Otherwise write the file and let the user
  decide whether to commit — say it's ready.

## Rules

- `LEFT_OFF.md` is LIVING: overwrite stale content, preserve what's still accurate.
- It is the entry point, NOT the source of truth — it must point to the real docs.
- **Never write a secret value.** Reference keys/tokens/hosts by name only.
- This command is personal/global — it must work in any repo. Adapt to whatever project this is;
  assume nothing project-specific beyond what Phase 1 reveals.
