#!/bin/bash
# ThetaForge Advisor provisioning on GCP e2-micro (Ubuntu 24.04, 1GB RAM).
# Direct Python + systemd (no Docker) to conserve the 1GB memory budget.
# Usage: scp deploy key + this script to VM, then: bash provision_gcp.sh
set -euo pipefail

REPO_URL="git@github.com:Jadax/ThetaForge.git"
APP_DIR="/opt/thetaforge"
RUN_USER="thetaforge"
ADVISOR_TOKEN="${ADVISOR_TOKEN:-aqMnE8q5WFeGCevbHW1ru-zt7bguZyFCdrsyhBJ4ioQ}"
# BRIDGE_ACCESS_TOKEN is a live credential (the bridge is the only order path).
# Deliberately no default here: pass it via env at deploy time, or the .env will
# carry an empty bridge token (bridge universe discovery simply fails open).
BRIDGE_TOKEN="${BRIDGE_TOKEN:-}"
BRIDGE_URL="${BRIDGE_URL:-http://92.4.132.188:8002}"
DASHBOARD_ORIGINS="${DASHBOARD_ORIGINS:-https://thetaforge-terminal.pages.dev,http://localhost:3000,http://127.0.0.1:3000}"

echo "=== ThetaForge GCP provisioning ==="

# --- System packages + non-root run user ---
sudo apt-get update -qq
sudo apt-get install -y -qq git ca-certificates python3-venv python3-pip curl >/dev/null
if ! id "$RUN_USER" &>/dev/null; then
    sudo useradd --system --home "$APP_DIR" --shell /bin/bash "$RUN_USER"
    echo "created user $RUN_USER"
fi

# --- SSH deploy key for GitHub (deploy key copied in ahead) ---
sudo mkdir -p /root/.ssh /home/ubuntu/.ssh
# copy the deploy key onto the ubuntu user (it was scp'd to /tmp already)
if [ -f /tmp/tf_deploy_key ]; then
    mkdir -p /home/ubuntu/.ssh
    chmod 700 /home/ubuntu/.ssh
    install -o ubuntu -g ubuntu -m 600 /tmp/tf_deploy_key /home/ubuntu/.ssh/thetaforge_repo_deploy_key
    test -f /tmp/tf_deploy_key.pub && install -o ubuntu -g ubuntu -m 644 /tmp/tf_deploy_key.pub /home/ubuntu/.ssh/thetaforge_repo_deploy_key.pub
    cat > /home/ubuntu/.ssh/config <<'CFG'
Host github-thetaforge
  HostName github.com
  User git
  IdentityFile ~/.ssh/thetaforge_repo_deploy_key
  IdentitiesOnly yes
CFG
    chmod 600 /home/ubuntu/.ssh/config
    echo "deploy key installed"
fi
# make github.com known host
timeout 20 ssh-keyscan -t ed25519 github.com >> /home/ubuntu/.ssh/known_hosts 2>/dev/null || true
chown -R ubuntu:ubuntu /home/ubuntu/.ssh

# --- App directory ---
sudo mkdir -p "$APP_DIR"
sudo chown "$RUN_USER:$RUN_USER" "$APP_DIR"

# Clone as ubuntu (has the key), then hand to the run user
if [ ! -d "$APP_DIR/.git" ]; then
    sudo -u ubuntu git clone -q "$REPO_URL" /tmp/tfclone 2>/dev/null || sudo -u ubuntu git clone -q git@github-thetaforge:Jadax/ThetaForge.git /tmp/tfclone
    sudo cp -a /tmp/tfclone/. "$APP_DIR/"
    sudo rm -rf /tmp/tfclone
    echo "repo cloned"
else
    echo "repo exists; skipping clone"
fi
sudo chown -R "$RUN_USER:$RUN_USER" "$APP_DIR"

# --- venv + deps ---
if [ ! -d "$APP_DIR/venv" ]; then
    sudo -u "$RUN_USER" python3 -m venv "$APP_DIR/venv"
fi
sudo -u "$RUN_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip -q
sudo -u "$RUN_USER" "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
echo "deps installed"

# --- data dir (gitignored state) ---
sudo mkdir -p "$APP_DIR/data"
sudo chown -R "$RUN_USER:$RUN_USER" "$APP_DIR/data"

# --- .env ---
cat > "$APP_DIR/.env" <<EOF
ADVISOR_API_TOKEN=${ADVISOR_TOKEN}
BROKER_ACCESS_TOKEN=${ADVISOR_TOKEN}
BRIDGE_ACCESS_TOKEN=${BRIDGE_TOKEN}
BRIDGE_URL=${BRIDGE_URL}
DASHBOARD_ORIGINS=${DASHBOARD_ORIGINS}
EOF
chown "$RUN_USER:$RUN_USER" "$APP_DIR/.env"
chmod 600 "$APP_DIR/.env"
echo ".env written"

# --- systemd unit ---
sudo tee /etc/systemd/system/thetaforge-advisor.service >/dev/null <<'UNIT'
[Unit]
Description=ThetaForge Advisor (FastAPI, direct uvicorn)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=thetaforge
Group=thetaforge
WorkingDirectory=/opt/thetaforge
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=/opt/thetaforge/.env
ExecStart=/opt/thetaforge/venv/bin/uvicorn orchestrator.main:app --host 0.0.0.0 --port 8000 --workers 1 --timeout-keep-alive 30
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable thetaforge-advisor
sudo systemctl restart thetaforge-advisor
echo "systemd unit started"

echo ""
echo "=== Setup Complete ==="
echo "Advisor at: http://35.212.227.109:8000"
echo "Health:     http://35.212.227.109:8000/health"
