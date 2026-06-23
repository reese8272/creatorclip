# Runbook — Kubernetes & Deploy (Issues 275, 279)

> Hand-drafted W0 runbook. Both `external`. **Issue 275 is the deploy-track linchpin** — it unblocks
> verification of 276/277/278/280 and makes the 259 pool-math / 261 load-test [DEC]s falsifiable.
> The Helm chart **already exists** at `deploy/charts/creatorclip/`; the gap is it has never RUN on K8s
> ("staging" today is Docker-Compose on the prod VM). See also `docs/DEPLOYMENT.md` for chart details.

---

## Issue 275 — GKE staging cluster + first real Helm deploy (chart parity with prod)

**Goal:** prove the existing chart on real Kubernetes end-to-end. Locked architecture: **GKE Autopilot +
Cloud SQL PG16 (pgvector) + KEDA-on-Redis** (`docs/DECISIONS.md`).

**Prerequisites:**
- `gcloud` authenticated to the target project (run `gcloud auth login` yourself in this session via
  `! gcloud auth login` if needed), `kubectl`, `helm`, and billing enabled.
- Secrets escrowed in **GCP Secret Manager** (Issue 255) — External Secrets resolves from there.

**Steps:**
1. Create a minimal **GKE Autopilot** staging cluster.
2. Create a small **Cloud SQL PG16** instance with the **pgvector** extension; create the app DB/role.
3. Provision managed **Redis** (Memorystore or Upstash).
4. Install cluster prereqs: **KEDA**, **nginx-ingress**, **External Secrets** (pointed at GCP Secret Manager).
5. Run the **Alembic migration Job** (per `deploy/README` §6) against Cloud SQL.
6. `helm install` of `deploy/charts/creatorclip` end-to-end → app + worker + beat pods all reach **Ready**,
   External Secrets resolve.
7. Send a request through the ingress and run **one render job** to completion on the GKE worker; get `/health` green
   and the `llm_harness` flow passing **on GKE** (not the compose VM).
8. Document cluster bring-up + teardown in `deploy/README` and `docs/STAGING_ACCESS.md` (supersede the
   Docker-Compose-on-prod-VM staging).

**Done when:**
- [ ] GKE Autopilot + Cloud SQL PG16 (pgvector) + managed Redis exist and are reachable
- [ ] `helm install` deploys app+worker+beat; all pods Ready; External Secrets resolve
- [ ] A request succeeds through the ingress and one render job completes on the GKE worker
- [ ] `docs/STAGING_ACCESS.md` documents the new staging topology

---

## Issue 279 — Container supply-chain: cosign signing + SBOM + SLSA provenance

**Why:** `docker-publish.yml` pushes to GHCR with **no signature, SBOM, or provenance** — no way to verify
image origin/contents (2025 baseline; SLSA Build L2 is available out-of-the-box with the GitHub-native attestor).
For a SaaS holding creators' OAuth tokens + PII, that's a real launch risk.

**Steps (edit `.github/workflows/docker-publish.yml`, after build/push):**
1. **Keyless cosign sign** of the pushed GHCR digest via the Actions OIDC identity (no stored keys; recorded in Rekor).
2. Generate an **SBOM** (Syft or buildx provenance/sbom attestors) and attach it as an attestation.
3. Emit **SLSA build provenance** via `actions/attest-build-provenance`.
4. Add a `cosign verify` / `gh attestation verify` step in CI and document it in `deploy/README`.
5. **Pin the digest** in the Helm `image` reference. (Stretch: Kyverno/policy-controller admission rule so the
   cluster only runs signed images.)

**Done when:**
- [ ] `docker-publish.yml` keyless-signs the pushed digest (OIDC, in Rekor)
- [ ] SBOM attached + SLSA provenance attested
- [ ] Signature + provenance verify green in CI

> Note: 279 is mostly a CI-YAML change (could be partly built by an agent), but it's grouped here because
> verification needs the live GHCR/OIDC/Rekor path.
