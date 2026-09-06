#!/bin/bash
# Point the AMD VM's executor/manager/supervisor at the GCP Advisor instead of Render.
# Run as root (sudo bash swap_advisor_url.sh). Idempotent + reversible.
set -uo pipefail

OLD="https://thetaforge-advisor.onrender.com"
NEW="http://35.212.227.109:8000"
STAMP=$(date -u +%Y%m%d%H%M%S)
LOG_TAG="advisor-swap"

say() { echo "[$(date -u +%FT%TZ)] $*"; logger -t "$LOG_TAG" "$*"; }

# --- 1. Disable the Render keepalive (GCP doesn't spin down) ---
if systemctl is-active --quiet render-keepalive.timer; then
    systemctl stop render-keepalive.timer
    systemctl disable render-keepalive.timer 2>/dev/null
    say "render-keepalive.timer stopped+disabled"
else
    say "render-keepalive.timer not active"
fi

# --- 2. Patch executor + manager unit env ---
for unit in thetaforge-auto-executor.service thetaforge-auto-manager.service; do
    f="/etc/systemd/system/$unit"
    if ! systemctl is-failed --quiet "$unit"; then :; fi
    if grep -q "$OLD" "$f"; then
        cp "$f" "${f}.bak-${STAMP}"
        sed -i "s|$OLD|$NEW|g" "$f"
        say "$unit patched (${f}.bak-${STAMP})"
    else
        say "$unit already patched or no match"
    fi
done

# --- 3. Patch supervisor script ---
sf="/opt/thetaforge-bridge/market_hours_supervisor.sh"
if grep -q "$OLD" "$sf"; then
    cp "$sf" "${sf}.bak-${STAMP}"
    sed -i "s|$OLD|$NEW|g" "$sf"
    say "$sf patched (${sf}.bak-${STAMP})"
else
    say "$sf already patched or no match"
fi

systemctl daemon-reload
systemctl restart thetaforge-market-supervisor.timer 2>/dev/null || true
systemctl restart render-keepalive.timer 2>/dev/null || true

# --- 4. Verify ---
say "=== VERIFY ==="
for unit in thetaforge-auto-executor.service thetaforge-auto-manager.service; do
    envline=$(systemctl show "$unit" -p Environment --value 2>/dev/null)
    say "$unit: $envline"
done
say "supervisor: $(grep 'ADVISOR_URL=' "$sf")"
say "render-keepalive active? $(systemctl is-active render-keepalive.timer 2>/dev/null)"