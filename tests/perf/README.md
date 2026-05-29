# tests/perf/ — load testing (concurrency evidence)

Unit tests prove correctness; they cannot prove behavior under hundreds of
concurrent creators. The scale-checklist axes A, B, F (pool math, async-loop
hygiene, rate-limit-under-contention) can only be *settled* with a real load
run. This is that run.

> **Tool choice:** Locust (Python), so it reuses the project's auth scheme and
> is maintained in the same language as the codebase. If you later need
> >10k RPS from one box or native Grafana/Prometheus streaming, k6 is the
> documented alternative (see `docs/DECISIONS.md`).

## Prerequisites
1. A **staging** stack (never run against production).
2. A seeded test creator with some videos/clips so the read endpoints return
   realistic payloads (empty tables hide N+1 and serialization cost).
3. The staging `JWT_SECRET_KEY` and that creator's UUID.

## Run
```bash
pip install -r requirements-dev.txt
export CC_BASE_URL=https://staging.example.com
export CC_JWT_SECRET=<staging JWT_SECRET_KEY>
export CC_CREATOR_ID=<seeded creator uuid>
locust -f tests/perf/locustfile.py --host "$CC_BASE_URL" \
       --users 300 --spawn-rate 20 --run-time 5m --headless \
       --csv docs/assessment/loadtest
```
The `--csv` output drops percentile tables into `docs/assessment/` so the
assessment REPORT.md can cite real numbers.

## What to look for (feeds scale-checklist.md)
| Symptom in results | Likely cause | Checklist axis |
|---|---|---|
| p99 latency climbs with users while p50 stays flat | event-loop stall — a sync/blocking call on a hot async path | B |
| Errors spike to `QueuePool limit` / `TimeoutError` | DB pool exhausted vs replicas × pool size | A |
| 429s appear earlier than expected | rate limiter; confirm per-creator not per-IP, and Redis round-trip cost | F |
| `/health` flips to `degraded` under load | DB or Redis saturation | A, E |
| Throughput plateaus then drops | a downstream (R2/YouTube/Anthropic) without a timeout backing up | E |

## Next steps after a run
- Record the numbers in the next `/assess` REPORT.md (axes A/B/F).
- If pool exhaustion appears, implement PgBouncer transaction pooling
  (scale-checklist.md §A) and re-run.
- Separately load-test the Celery path (queue depth + task latency) — the async
  render/generate jobs are not exercised here by design.
