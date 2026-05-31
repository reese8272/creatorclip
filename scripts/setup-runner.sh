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
#   After running this script:
#     - docker-publish.yml (build + push to GHCR) runs on this VM
#     - deploy.yml (pull + migrate + roll out) runs on this VM
#     - CI / Quality / Integration workflows stay on GitHub-hosted (they're
#       informational only and don't gate deploys — deploy.yml depends on
#       Docker publish, not on CI)

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
echo "    The 'Deploy to production' workflow will now run on this VM"
echo "    instead of consuming GitHub-hosted minutes."
