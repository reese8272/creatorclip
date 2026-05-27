# CreatorClip — Access & Connectivity Runbook

Click-by-click guide to **SSH access**, the **CI/CD deploy key**, and the **Cloudflare
Tunnel** for the beta deployment. Tailored to the real infrastructure:

| Thing | Value |
|-------|-------|
| VM (DigitalOcean Droplet) | `147.182.136.107` |
| Public domain | `agenticlip.studio` |
| Deploy directory on VM | `/opt/autoclip` |
| Docker image | `ghcr.io/reese8272/creatorclip:latest` |

> Companion docs: [`docs/SECRETS.md`](SECRETS.md) (what every key is) ·
> [`docs/RUNBOOKS.md`](RUNBOOKS.md) (encryption-key rotation).

---

## The mental model (read once)

There are exactly **three** access paths into this system. Everything below is one of these:

1. **You → VM**, over SSH, authenticated by an **SSH keypair**.
2. **GitHub Actions → VM**, over SSH, authenticated by a **deploy keypair** (stored as the
   `VPS_SSH_KEY` GitHub secret) — plus a **GHCR token** so the VM can pull the private image.
3. **The public internet → app**, over HTTPS, through the **Cloudflare Tunnel** (no open ports).

The goal of this runbook is **one canonical key per path, in one known place.** No sprawl.

---

## Step 0 — Inventory what you actually have

You said the key situation is a mess. Before changing anything, find out the truth. Run these
**on your local machine**:

```bash
# 1. Every private/public key you have locally
ls -la ~/.ssh/
#    id_ed25519 / id_rsa = private keys (NEVER share). *.pub = public keys (safe to share).

# 2. Any per-host shortcuts you've set up
cat ~/.ssh/config 2>/dev/null

# 3. Which key (if any) currently gets you into the droplet — verbose shows the key tried
ssh -v root@147.182.136.107 'echo CONNECTED as $(whoami)' 2>&1 | grep -Ei 'offering|accepted|authenticated|denied|connected'
#    If root fails, try a user you might have created:
#    ssh -v youruser@147.182.136.107 'echo CONNECTED as $(whoami)'
```

Then check the two cloud dashboards:

- **GitHub** → your repo → **Settings** → **Secrets and variables** → **Actions**. Note which of
  `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`, `VPS_PORT`, `GHCR_TOKEN` exist. (You can't read secret
  values back — that's expected. You're just confirming what's set.)
- **DigitalOcean** → **Settings** → **Security** → **SSH Keys**: account-level keys (these are
  only injected at droplet *creation* time, not afterward).

Write down the answers to two questions — they drive everything else:

- **Which user am I?** (`root`, or a name like `reese`/`deploy`?) → this is `VPS_USER`.
- **Which local private key opened the connection?** → this is your canonical key. Everything
  else can eventually be retired.

---

## Step 1 — SSH access (you → VM)

### 1a. If you can already get in

Confirm who you are and where authorized keys live:

```bash
ssh <user>@147.182.136.107
whoami                      # root  → keys at /root/.ssh/authorized_keys
                            # other → keys at /home/<user>/.ssh/authorized_keys
cat ~/.ssh/authorized_keys  # every PUBLIC key currently allowed in
```

Each line in `authorized_keys` is one public key that can log in. If there are keys here you
don't recognize, that is exactly the sprawl to clean up (Step 1c).

### 1b. If you're locked out — the DigitalOcean recovery console

You never truly lose access to a Droplet; the web console bypasses SSH entirely.

1. Go to **cloud.digitalocean.com** → **Droplets** → click your droplet.
2. Top-right: **Access** tab → **Launch Droplet Console** (also called the Recovery Console).
3. This opens a browser terminal logged in as `root` (you may need to set/reset the root password
   first via **Access → Reset Root Password**, which emails a temporary password).
4. Once in, add your public key so normal SSH works again:
   ```bash
   mkdir -p ~/.ssh && chmod 700 ~/.ssh
   echo "ssh-ed25519 AAAA...your-public-key... you@machine" >> ~/.ssh/authorized_keys
   chmod 600 ~/.ssh/authorized_keys
   ```
   (Paste the contents of your **local** `~/.ssh/id_ed25519.pub` on the right-hand side.)

### 1c. Consolidate to ONE canonical key (recommended)

If you don't have a clean key, make one **locally**:

```bash
ssh-keygen -t ed25519 -C "reese-creatorclip" -f ~/.ssh/creatorclip_ed25519
#   creates ~/.ssh/creatorclip_ed25519       (private — keep secret)
#       and ~/.ssh/creatorclip_ed25519.pub   (public — install on the VM)
```

Install its **public** half on the VM (via 1a or 1b), then add a shortcut so you never juggle
paths again — append to `~/.ssh/config` locally:

```
Host creatorclip
    HostName 147.182.136.107
    User <root-or-your-user>
    IdentityFile ~/.ssh/creatorclip_ed25519
    IdentitiesOnly yes
```

Now `ssh creatorclip` just works. Once verified, remove stale lines from the VM's
`authorized_keys` and delete unused local keys — that ends the sprawl.

### 1d. Harden (Issue 23 acceptance: key-only, no passwords)

On the VM, edit `/etc/ssh/sshd_config` (use `sudo` if not root):

```
PasswordAuthentication no
PermitRootLogin prohibit-password   # key-only root; or "no" if you use a sudo user
```

Then `sudo systemctl restart ssh`. **Keep your recovery-console access (1b) confirmed working
before you do this**, so a typo can't lock you out permanently.

---

## Step 2 — CI/CD deploy key (GitHub Actions → VM)

`deploy.yml` SSHes into the VM using these secrets. Set them at **GitHub repo → Settings →
Secrets and variables → Actions**:

| Secret | Value | Notes |
|--------|-------|-------|
| `VPS_HOST` | `147.182.136.107` | |
| `VPS_USER` | the user from Step 0 | `root` or your sudo user |
| `VPS_SSH_KEY` | the **private** key the runner uses | see below — use a *dedicated* deploy key |
| `VPS_PORT` | `22` (or your custom port) | optional; defaults to 22 |
| `GHCR_TOKEN` | a GitHub PAT with `read:packages` | lets the VM `docker login ghcr.io` and pull |

**Best practice: give CI its own key**, separate from your personal one, so you can revoke CI
access without locking yourself out.

```bash
# locally
ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/cc_deploy -N ""
cat ~/.ssh/cc_deploy.pub     # → append to the VM's ~/.ssh/authorized_keys (Step 1a)
cat ~/.ssh/cc_deploy         # → paste the WHOLE private key into the VPS_SSH_KEY secret
```

Paste the private key **including** the `-----BEGIN OPENSSH PRIVATE KEY-----` /
`-----END …-----` lines. Then delete `~/.ssh/cc_deploy` locally (GitHub now holds it).

**Create `GHCR_TOKEN`:** github.com → your avatar → **Settings** → **Developer settings** →
**Personal access tokens** → **Tokens (classic)** → **Generate new token** → scope **`read:packages`**.

Test the whole path without waiting for a push: GitHub → repo → **Actions** → **Deploy to
production** → **Run workflow**. The new doctor preflight step will fail loudly (with redacted
output) if any secret is missing, *before* it migrates or cuts over.

---

## Step 3 — Cloudflare Tunnel (internet → app)

Your tunnel **already exists**. The important change from this work: `cloudflared` now runs
**inside Docker Compose** next to the app, and the app **no longer publishes a host port**. So the
tunnel must point at the app over the Compose network, not at `localhost`.

### 3a. Find the existing tunnel and its token

1. Go to **one.dash.cloudflare.com** (Cloudflare **Zero Trust**).
2. Left nav: **Networks** → **Tunnels**. Find the tunnel for `agenticlip.studio`.
3. Click it → **Configure**. Under the connector install instructions you'll see a command like
   `cloudflared service install eyJhIjoi...` — the long `eyJ…` string **is** the tunnel token.
   (If it isn't shown, use **Refresh token** to mint a new one — this does not break the tunnel,
   it just rotates the credential.)
4. Put that token on the VM in `/opt/autoclip/.env`:
   ```
   CLOUDFLARE_TUNNEL_TOKEN=eyJhIjoi...
   ```
   `docker-compose.prod.yml`'s `cloudflared` service reads it automatically.

### 3b. ⚠️ Fix the ingress rule to point at `app:8000`

This is the step that most likely fixes a tunnel that "connects but 502s":

1. Same tunnel → **Configure** → **Public Hostname** tab.
2. There should be a rule for **`agenticlip.studio`**. Edit it:
   - **Service → Type:** `HTTP`
   - **Service → URL:** `app:8000`  ← **not** `localhost:80` and **not** `localhost:8000`
3. Save. `app` resolves over the Compose network to the FastAPI container's port 8000.

> Why: the app container no longer maps a port to the VM host. Inside the Compose network the
> hostname is the service name, `app`, on its internal port `8000`.

### 3c. Verify end-to-end

```bash
ssh creatorclip
cd /opt/autoclip
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps          # cloudflared + app should be Up/healthy
docker compose -f docker-compose.prod.yml logs --tail 50 cloudflared   # look for "Registered tunnel connection"

# from anywhere:
curl -s https://agenticlip.studio/health               # → {"status":"ok",...}
```

---

## Where everything lives (the recap)

| Secret / key | Lives in | Backup / recovery |
|--------------|----------|-------------------|
| Your personal SSH **private** key | `~/.ssh/creatorclip_ed25519` (local only) | DigitalOcean recovery console (Step 1b) re-adds the public half |
| Your SSH **public** key | VM `~/.ssh/authorized_keys` | regenerate from private, or make a new pair |
| CI deploy **private** key | GitHub secret `VPS_SSH_KEY` | regenerate; re-add public half to VM; update secret |
| `GHCR_TOKEN` | GitHub secret | regenerate PAT in GitHub Developer settings |
| `CLOUDFLARE_TUNNEL_TOKEN` | VM `/opt/autoclip/.env` | **Refresh token** in the Cloudflare tunnel page |
| App secrets (Anthropic, R2, …) | VM `/opt/autoclip/.env` | each provider's dashboard ([`docs/SECRETS.md`](SECRETS.md)) |

---

## Rotation quick reference

- **SSH / deploy key:** generate a new pair → add the public key to the VM `authorized_keys` →
  update `VPS_SSH_KEY` (CI) or `~/.ssh/config` (you) → remove the old public key from the VM.
- **Cloudflare tunnel token:** tunnel page → **Refresh token** → update `CLOUDFLARE_TUNNEL_TOKEN`
  in `/opt/autoclip/.env` → `docker compose -f docker-compose.prod.yml up -d cloudflared`.
- **App encryption keys** (`TOKEN_ENCRYPTION_KEY`, `JWT_SECRET_KEY`): see [`docs/RUNBOOKS.md`](RUNBOOKS.md).

After any rotation, run `python scripts/doctor.py --full` to confirm everything is green.
