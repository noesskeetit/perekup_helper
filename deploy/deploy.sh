#!/usr/bin/env bash
#
# deploy.sh — Deploy PerekupHelper stack on a fresh VPS.
#
# Usage:
#   1. Copy .env to the server:  scp .env vps:/opt/perekup/.env
#   2. Run:  ssh vps "bash /opt/perekup/deploy/deploy.sh"
#
set -euo pipefail

DEPLOY_DIR="/opt/perekup"
REPO_URL="https://github.com/noesskeetit/perekup-helper.git"
BRANCH="main"

# ── 1. System dependencies ──────────────────────────────────────────
echo "==> Installing system packages..."
apt-get update -qq
apt-get install -y -qq docker.io docker-compose-plugin nginx git > /dev/null

systemctl enable --now docker

# ── 2. Clone / update repo ──────────────────────────────────────────
if [ -d "$DEPLOY_DIR/.git" ]; then
    echo "==> Updating existing repo..."
    git -C "$DEPLOY_DIR" fetch origin
    git -C "$DEPLOY_DIR" reset --hard "origin/$BRANCH"
else
    echo "==> Cloning repo..."
    git clone --branch "$BRANCH" "$REPO_URL" "$DEPLOY_DIR"
fi

# ── 3. Check .env ───────────────────────────────────────────────────
if [ ! -f "$DEPLOY_DIR/.env" ]; then
    echo "ERROR: $DEPLOY_DIR/.env not found."
    echo "Copy your .env file to the server before running this script:"
    echo "  scp .env vps:$DEPLOY_DIR/.env"
    exit 1
fi

# ── 4. Build and start Docker Compose ───────────────────────────────
echo "==> Building and starting services..."
cd "$DEPLOY_DIR"
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d --remove-orphans

# ── 5. Install systemd service ──────────────────────────────────────
echo "==> Installing systemd service..."
cp "$DEPLOY_DIR/deploy/perekup.service" /etc/systemd/system/perekup.service
systemctl daemon-reload
systemctl enable perekup.service

# ── 6. Configure nginx ──────────────────────────────────────────────
echo "==> Configuring nginx..."
cp "$DEPLOY_DIR/deploy/nginx.conf" /etc/nginx/sites-available/perekup
ln -sf /etc/nginx/sites-available/perekup /etc/nginx/sites-enabled/perekup
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

# ── 7. Health check ─────────────────────────────────────────────────
echo "==> Waiting for app to become healthy..."
for i in $(seq 1 30); do
    if curl -sf http://127.0.0.1:8000/health > /dev/null 2>&1; then
        echo "==> Health check passed!"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "WARNING: Health check did not pass within 30 attempts."
        echo "Check logs: docker compose -f $DEPLOY_DIR/docker-compose.prod.yml logs"
        exit 1
    fi
    sleep 2
done

echo ""
echo "==> Deployment complete!"
echo "    Dashboard: http://$(hostname -I | awk '{print $1}')"
echo "    Logs:      docker compose -f $DEPLOY_DIR/docker-compose.prod.yml logs -f"
echo "    Status:    docker compose -f $DEPLOY_DIR/docker-compose.prod.yml ps"
