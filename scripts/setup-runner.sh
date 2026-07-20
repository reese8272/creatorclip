#!/usr/bin/env bash
# One-time setup: install a GitHub Actions self-hosted runner on the production VM.
#
# Run this on 147.182.136.107 (not locally):
#   ssh root@147.182.136.107
#   bash /tmp/setup-runner.sh
#
# Before running, get your runner registration token from:
#   https://github.com/reese8272/<REPO>/settings/actions/runners/new
#   (select Linux / x64 — copy only the token, not the full configure command)
#
# Why self-hosted?
#   The deploy pipeline runs (1) docker-publish (build + push to GHCR) and
#   (2) deploy (pull + migrate + restart). Both consume GitHub-hosted runner
#   minutes. Hitting the spending limit silently fast-fails every push, which
#   is what live-blocked Wave 6's deploy on 2026-05-31. A self-hosted runner
#   on the same VM that already runs the app costs nothing and removes the
#   billing dependency for both workflows permanently. (Issue 101)
#
#   After running this script, ONLY the deploy-track workflows run on this VM:
#     - docker-publish.yml (build + push to GHCR)
#     - deploy.yml (pull + migrate + roll out)
#     - staging-drills.yml (dispatch-only drills against the ccstage stack)
#
#   SECURITY BOUNDARY (Issue 360, 2026-07-20): this runner is in the `docker`
#   group (root-equivalent host control) and owns /opt/autoclip including the
#   prod .env — so it must NEVER execute pull_request-triggered code. ci.yml and
#   mutation.yml moved to GitHub-hosted ubuntu-latest; any future workflow that
#   targets `runs-on: self-hosted` must trigger only from trusted refs
#   (push to main / workflow_dispatch / schedule), never pull_request.

set -euo pipefail

RUNNER_VERSION="2.317.0"
RUNNER_USER="github-runner"
RUNNER_DIR="/opt/github-runner"
# Repo was renamed Youtube-Video-AI-Editor → creatorclip; the registration
# token from `gh api repos/<owner>/<repo>/actions/runners/registration-token`
# is scoped to a specific repo URL and returns 404 on any mismatch.
REPO_URL="https://github.com/reese8272/creatorclip"

if [[ $EUID -ne 0 ]]; then
  echo "ERROR: run as root on the production VM." >&2
  exit 1
fi

read -rp "Paste your runner registration token: " RUNNER_TOKEN
if [[ -z "$RUNNER_TOKEN" ]]; then
  echo "ERROR: token cannot be empty." >&2
  exit 1
fi

echo "==> Creating runner user and directory..."
id -u "$RUNNER_USER" &>/dev/null || useradd -r -m -s /bin/bash "$RUNNER_USER"
usermod -aG docker "$RUNNER_USER"   # runner needs docker without sudo
mkdir -p "$RUNNER_DIR"
chown "$RUNNER_USER:$RUNNER_USER" "$RUNNER_DIR"

echo "==> Downloading runner package..."
cd "$RUNNER_DIR"
TARBALL="actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
sudo -u "$RUNNER_USER" curl -fsSL \
  "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${TARBALL}" \
  -o "$TARBALL"
sudo -u "$RUNNER_USER" tar xzf "$TARBALL"
rm "$TARBALL"

echo "==> Configuring runner..."
sudo -u "$RUNNER_USER" ./config.sh \
  --url "$REPO_URL" \
  --token "$RUNNER_TOKEN" \
  --name "autoclip-prod-vm" \
  --labels "self-hosted,linux,x64,prod" \
  --work "_work" \
  --unattended \
  --replace

echo "==> Pre-installing host dependencies (python3.12)..."
# The deploy-track workflows run everything inside containers; python3.12 on the
# host is kept for operator tooling (backup scripts' helpers, ad-hoc debugging).
# The former ffmpeg/libpq-dev/gcc pre-install existed only for ci.yml, which
# moved to GitHub-hosted runners (Issue 360).
apt-get update -q
apt-get install -y --no-install-recommends python3.12 python3.12-venv

echo "==> Granting the runner write access to the deploy directory..."
# deploy.yml runs `cp` into /opt/autoclip and `sed -i` on /opt/autoclip/.env;
# the runner user needs to own that directory. Pre-install it was root:root
# which failed the first deploy run after switching docker-publish to
# self-hosted (Issue 101). .env stays 600 — only the runner can read secrets.
if [[ -d /opt/autoclip ]]; then
  chown -R "$RUNNER_USER:$RUNNER_USER" /opt/autoclip
  [[ -f /opt/autoclip/.env ]] && chmod 600 /opt/autoclip/.env
fi

echo "==> Installing as systemd service..."
./svc.sh install "$RUNNER_USER"
./svc.sh start

echo ""
echo "==> Runner installed and running."
echo "    Verify at: $REPO_URL/settings/actions/runners"
echo ""
echo "    The deploy-track workflows ('Docker publish', 'Deploy to production',"
echo "    'Staging drills') will now run on this VM."
echo ""
echo "    CI runs on GitHub-hosted runners (Issue 360) — never point a"
echo "    pull_request-triggered workflow at this runner: it can read the prod"
echo "    .env and controls the prod containers via the docker group."
