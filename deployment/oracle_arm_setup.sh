#!/bin/bash
# Oracle Cloud ARM VM setup for ThetaForge Advisor.
# Run this ONCE on a fresh Ubuntu ARM instance:
#   ssh -i key ubuntu@<ARM_IP> 'bash -s' < deployment/oracle_arm_setup.sh
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/ThetaForge/ThetaForge.git}"
ADVISOR_TOKEN="${ADVISOR_TOKEN:-aqMnE8q5WFeGCevbHW1ru-zt7bguZyFCdrsyhBJ4ioQ}"
BRIDGE_TOKEN="${BRIDGE_TOKEN:-}"
BRIDGE_URL="${BRIDGE_URL:-http://92.4.132.188:8002}"

echo "=== ThetaForge ARM Setup ==="

# --- System packages ---
sudo apt-get update -qq
sudo apt-get install -y -qq ca-certificates curl gnupg git

# --- Docker ---
if ! command -v docker &>/dev/null; then
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
        sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
    sudo apt-get update -qq
    sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
    sudo usermod -aG docker ubuntu
    echo "Docker installed"
else
    echo "Docker already installed"
fi

# --- App directory ---
sudo mkdir -p /opt/thetaforge
sudo chown ubuntu:ubuntu /opt/thetaforge

if [ ! -d /opt/thetaforge/.git ]; then
    git clone "$REPO_URL" /opt/thetaforge
else
    cd /opt/thetaforge && git pull
fi
cd /opt/thetaforge

# --- Environment file ---
cat > /opt/thetaforge/.env.docker <<EOF
ADVISOR_API_TOKEN=${ADVISOR_TOKEN}
BROKER_ACCESS_TOKEN=${ADVISOR_TOKEN}
EOF
chmod 600 /opt/thetaforge/.env.docker

# --- docker-compose.yml for the advisor ---
cat > /opt/thetaforge/docker-compose.advisor.yml <<'COMPOSE'
services:
  advisor:
    build: .
    container_name: thetaforge-advisor
    restart: always
    ports:
      - "127.0.0.1:8000:8000"
    env_file:
      - .env.docker
    volumes:
      - advisor-data:/app/data
    healthcheck:
      test: ["CMD", "python", "-c", "import httpx; httpx.get('http://localhost:8000/health', timeout=5)"]
      interval: 60s
      timeout: 10s
      retries: 3
      start_period: 30s
    deploy:
      resources:
        limits:
          memory: 2g
volumes:
  advisor-data:
COMPOSE

# --- Build and start ---
docker compose -f docker-compose.advisor.yml build
docker compose -f docker-compose.advisor.yml up -d

# --- Nginx ---
sudo apt-get install -y -qq nginx
sudo cp deployment/nginx/thetaforge-advisor.conf /etc/nginx/sites-available/thetaforge
sudo ln -sf /etc/nginx/sites-available/thetaforge /etc/nginx/sites-enabled/thetaforge
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
sudo systemctl enable nginx

# --- Systemd (optional, for docker-compose restart on boot) ---
sudo cp deployment/systemd/thetaforge-advisor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable thetaforge-advisor

# --- Verify ---
echo ""
echo "=== Setup Complete ==="
echo "Advisor running at: http://$(curl -s http://checkip.amazonaws.com || echo '<YOUR_ARM_IP>'):8000"
echo "Health check: http://$(curl -s http://checkip.amazonaws.com || echo '<YOUR_ARM_IP>'):8000/health"
echo ""
echo "Update your AMD VM executor/manager environment:"
echo "  ADVISOR_URL=http://$(curl -s http://checkip.amazonaws.com || echo '<YOUR_ARM_IP>'):8000"
echo ""
echo "Next steps:"
echo "  1. Update deployment/market_hours_supervisor.sh ADVISOR_URL on the AMD VM"
echo "  2. Update thetaforge-auto-executor.service ADVISOR_URL on the AMD VM"
echo "  3. Update thetaforge-auto-manager.service ADVISOR_URL on the AMD VM"
echo "  4. Restart services on the AMD VM"
