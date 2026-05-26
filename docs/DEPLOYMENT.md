# CreatorClip — Deployment

**Last updated**: 2026-05-25
**Target scale**: 10,000+ concurrent creators

---

## Development (Docker Compose)

```bash
cp .env.example .env
# Fill in required vars in .env

docker compose up --build
# App at http://localhost:8000
# Docs at http://localhost:8000/docs (development only)

# Health check
curl http://localhost:8000/health
```

To run tests against the running stack:
```bash
pytest
# Integration tests (requires live postgres + redis):
pytest -m integration
```

---

## Production Target: Kubernetes

Docker Compose is **dev/test only**. Production at 10k+ scale requires Kubernetes.

### Decisions Made (see `docs/DECISIONS.md` for full rationale)

- [x] **Managed K8s provider**: GKE Autopilot — lowest-ops, no node management, Cloud SQL
  supports pgvector, Secret Manager integrates cleanly.
- [x] **Ingress + TLS**: nginx-ingress + Cloudflare Tunnel; TLS terminated at Cloudflare.
- [x] **Celery worker autoscaling**: KEDA with Redis `listLength` trigger on the `celery` queue.
- [x] **GPU / transcription**: Deepgram (hosted) for MVP; WhisperX selectable via config for
  self-hosted GPU cost optimization later. No GPU nodes needed at launch.
- [x] **Database**: Cloud SQL for PostgreSQL 16; pgvector extension enabled post-provision.
- [x] **Helm charts**: Written in `deploy/charts/creatorclip/`. See `deploy/README.md`.
- [x] **Secrets management**: GCP Secret Manager + External Secrets Operator syncs to K8s.
- [x] **Connection pooling**: PgBouncer sidecar in the app pod, transaction mode, 25 conns/pod.

### Preliminary Production Architecture (subject to research)

```
Cloudflare (CDN + DDoS) → Cloudflare Tunnel / Load Balancer
  → K8s nginx-ingress
    → FastAPI pods (HPA on CPU/request count)
    → Celery worker pods (KEDA on Redis queue depth)
    → Celery beat pod (1 replica)

Managed PostgreSQL (pgvector enabled) — PgBouncer for connection pooling
Redis 7 (managed or self-hosted in K8s)
Cloudflare R2 (object storage, zero egress)
```

### Pre-Deploy Checklist (production)

**Gate 1 — Automated**
```bash
pytest  # zero failures, including tests/eval/
```

**Gate 2 — Manual smoke test**
```bash
docker compose up
```
Drive the change at http://localhost:8000:
- [ ] Connect YouTube → ingest a video → candidates render
- [ ] Review flow captures feedback; ranking responds
- [ ] Edge cases: no-cam, captions-only, small catalog
- [ ] Honesty text visible; no virality promise anywhere
- [ ] No regression in adjacent flows (auth, DNA, insights)
- [ ] Browser console clean

**Gate 3 — Production gates (before public launch)**
See `docs/PROJECT_STATE.md` Pre-Public-Launch Gates section.

---

## Transcription Compute Decision

**Must decide before Issue 5:**

| Option | Pros | Cons |
|--------|------|------|
| Self-hosted WhisperX (GPU) | Cheapest at scale, offline, no data-sharing | GPU node management, cold-start latency |
| Deepgram API | Fast, managed, no GPU | Per-minute cost, external data dependency |
| AssemblyAI API | Reliable, word-level timestamps | Per-minute cost, external data dependency |

Decision: log in `docs/DECISIONS.md` when made.
