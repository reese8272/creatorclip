# CreatorClip — Production Deployment

Target platform: **GKE Autopilot** (recommended, see DECISIONS.md).
All commands assume `gcloud`, `kubectl`, and `helm` are installed and authenticated.

---

## 1. Prerequisites

```bash
# GKE cluster (Autopilot — no node management required)
gcloud container clusters create-auto creatorclip-prod \
  --region us-central1 \
  --project YOUR_GCP_PROJECT_ID

gcloud container clusters get-credentials creatorclip-prod \
  --region us-central1 --project YOUR_GCP_PROJECT_ID

# Install KEDA (Celery worker autoscaling)
helm repo add kedacore https://kedacore.github.io/charts
helm repo update
helm install keda kedacore/keda --namespace keda --create-namespace

# Install nginx-ingress
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm install nginx-ingress ingress-nginx/ingress-nginx --namespace ingress-nginx --create-namespace
```

---

## 2. Managed Dependencies (provision before deploying the app)

| Dependency | GCP Service | Notes |
|---|---|---|
| PostgreSQL + pgvector | Cloud SQL for PostgreSQL 16 | Enable pgvector extension after provisioning |
| Redis | Cloud Memorystore for Redis | Use redis:// private IP |
| Object storage | Cloudflare R2 | Already S3-compatible; no GCP equivalent needed |
| Secrets | GCP Secret Manager | See step 3 |

### Enable pgvector on Cloud SQL
```sql
-- Run once after Cloud SQL instance is ready
CREATE EXTENSION IF NOT EXISTS vector;
```

---

## 3. Secrets

All secrets are stored in GCP Secret Manager and synced to Kubernetes via the
External Secrets Operator.

```bash
# Install External Secrets Operator
helm repo add external-secrets https://charts.external-secrets.io
helm install external-secrets external-secrets/external-secrets \
  --namespace external-secrets --create-namespace

# Create the GCP secret with all required env vars (see .env.example)
gcloud secrets create creatorclip-prod-env --data-file=.env.prod \
  --project YOUR_GCP_PROJECT_ID
```

For a quick staging deploy without External Secrets:
```bash
kubectl create secret generic creatorclip-env --from-env-file=.env.prod -n creatorclip
```

---

## 4. Build and Push the Container Image

```bash
# From project root
IMAGE=gcr.io/YOUR_PROJECT_ID/creatorclip
TAG=$(git rev-parse --short HEAD)

docker build -t $IMAGE:$TAG .
docker push $IMAGE:$TAG
```

---

## 5. Deploy

```bash
# First deploy
helm install creatorclip ./deploy/charts/creatorclip \
  -f ./deploy/charts/creatorclip/values.prod.yaml \
  --set image.tag=$TAG \
  --set image.repository=$IMAGE \
  --namespace creatorclip \
  --create-namespace

# Subsequent deploys (rolling update)
helm upgrade creatorclip ./deploy/charts/creatorclip \
  -f ./deploy/charts/creatorclip/values.prod.yaml \
  --set image.tag=$TAG \
  --set image.repository=$IMAGE \
  --namespace creatorclip
```

---

## 6. Run Alembic Migrations

Run as a one-off Job before (or immediately after) a deploy that includes schema changes:

```bash
kubectl run alembic-migrate \
  --image=$IMAGE:$TAG \
  --restart=Never \
  --env-from=secret/creatorclip-env \
  --namespace=creatorclip \
  -- alembic upgrade head

kubectl logs alembic-migrate -n creatorclip
kubectl delete pod alembic-migrate -n creatorclip
```

---

## 7. Verify

```bash
# All pods healthy
kubectl get pods -n creatorclip

# App health endpoint
kubectl port-forward svc/creatorclip-app 8080:80 -n creatorclip
curl http://localhost:8080/health

# KEDA is watching the worker
kubectl get scaledobject -n creatorclip

# Tail app logs
kubectl logs -l app.kubernetes.io/component=app -n creatorclip --tail=100 -f
```

---

## 8. Rollback

```bash
helm rollback creatorclip -n creatorclip
```

---

## Scaling Notes

- **App**: HPA scales 2→10 replicas on CPU utilization (70%).
- **Worker**: KEDA scales 1→20 (prod: 1→50) replicas based on Celery Redis queue depth.
  Scale trigger: >5 tasks per replica in the `celery` queue.
- **Beat**: Always exactly 1 replica with `Recreate` strategy to prevent duplicate scheduling.
- **PgBouncer**: Sidecar in the app pod, transaction-mode pooling, 25 connections per pod
  (→ 750 upstream connections at 30 app pods, well within Cloud SQL's 1,000 connection limit).
