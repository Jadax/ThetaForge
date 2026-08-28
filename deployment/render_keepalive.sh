#!/usr/bin/env bash
# Keep the Render free-tier advisor warm so its 15-min idle spin-down never
# causes a cold-boot /health timeout (the 5s probe can't survive the slow
# pandas/yfinance import on a 0.1 vCPU). Runs every minute from this always-on
# VM, independent of market hours. Logs failures only, to stay quiet.
URL="https://thetaforge-advisor.onrender.com/health"
LOG="/opt/thetaforge-bridge/data/render_keepalive.log"
# -L follows the /health -> /health/ 307 trailing-slash redirect; only non-2xx
# is a real failure worth logging (Render returns 2xx once the app is warm).
code=$(curl -s -L -o /dev/null -w '%{http_code}' -m 25 "$URL" 2>/dev/null)
if [ "${code:0:1}" != "2" ]; then
  echo "$(date -u +%FT%TZ) keepalive: HTTP ${code:-none}" >> "$LOG"
fi
