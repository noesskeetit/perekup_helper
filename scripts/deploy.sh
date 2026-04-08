#!/bin/bash
# Deploy PerekupHelper to Cloud.ru VPS
# Usage: ./scripts/deploy.sh [--build]
#
# Prerequisites:
# - SSH access to VPS (87.242.86.121)
# - Docker + docker-compose on VPS
# - .env.prod file with production secrets

set -euo pipefail

VPS_HOST="87.242.86.121"
VPS_USER="root"
APP_DIR="/opt/perekup_helper"
REPO="noesskeetit/perekup_helper"

echo "=== PerekupHelper Deploy ==="

# Step 1: Build and test locally
echo "[1/5] Running tests..."
cd "$(dirname "$0")/.."
python -m pytest tests/ -x -q --tb=short || {
    echo "Tests failed! Aborting deploy."
    exit 1
}

# Step 2: Push to GitHub
echo "[2/5] Pushing to GitHub..."
git push origin main

# Step 3: SSH to VPS and pull
echo "[3/5] Pulling on VPS..."
ssh "${VPS_USER}@${VPS_HOST}" << 'REMOTE'
    set -e
    cd /opt/perekup_helper || {
        echo "First deploy: cloning repo..."
        git clone https://github.com/noesskeetit/perekup_helper.git /opt/perekup_helper
        cd /opt/perekup_helper
    }
    git pull origin main
REMOTE

# Step 4: Copy .env.prod if it doesn't exist on VPS
echo "[4/5] Checking .env..."
ssh "${VPS_USER}@${VPS_HOST}" "test -f ${APP_DIR}/.env || echo 'WARNING: .env missing on VPS!'"

# Step 5: Build and restart
echo "[5/5] Building and restarting..."
if [[ "${1:-}" == "--build" ]]; then
    ssh "${VPS_USER}@${VPS_HOST}" "cd ${APP_DIR} && docker compose -f docker-compose.prod.yml build && docker compose -f docker-compose.prod.yml up -d"
else
    ssh "${VPS_USER}@${VPS_HOST}" "cd ${APP_DIR} && docker compose -f docker-compose.prod.yml pull 2>/dev/null; docker compose -f docker-compose.prod.yml up -d"
fi

echo ""
echo "=== Deploy complete ==="
echo "App: http://${VPS_HOST}:8000"
echo "Bot: @perekup_helper1_bot"
echo ""
echo "Check logs: ssh ${VPS_USER}@${VPS_HOST} 'cd ${APP_DIR} && docker compose -f docker-compose.prod.yml logs -f'"
