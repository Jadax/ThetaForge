#!/bin/bash
# Starts ibgateway -> thetaforge-bridge -> thetaforge-auto-executor ->
# thetaforge-auto-manager when the market opens, and stops them in reverse
# order when it closes. Run frequently (every 5 minutes, via the paired
# systemd timer) rather than relying on exact-time triggers, so this is
# correct regardless of the VM's local timezone/DST setting and self-heals
# after any missed tick.
#
# Deliberately consults the Advisor's own /api/advisor/scanner/status
# market_open flag (real NYSE calendar, holidays and half-days included --
# see agents/trade_engine/background_scanner.py is_market_hours()) instead
# of re-implementing that logic here, so there is exactly one source of
# truth for "is the market open" across the whole system.
set -uo pipefail

ADVISOR_URL="https://thetaforge-advisor.onrender.com"
ADVISOR_TOKEN="aqMnE8q5WFeGCevbHW1ru-zt7bguZyFCdrsyhBJ4ioQ"
LOG_TAG="market-hours-supervisor"

market_open() {
    local response
    response=$(curl -s --max-time 15 -H "X-ThetaForge-Advisor-Token: $ADVISOR_TOKEN" \
        "$ADVISOR_URL/api/advisor/scanner/status" 2>/dev/null)
    [[ "$response" == *'"market_open":true'* ]]
}

is_active() {
    systemctl is-active --quiet "$1"
}

# This script's own systemd service runs as root (see
# thetaforge-market-supervisor.service), so systemctl needs no sudo here --
# unlike the interactive setup commands elsewhere in this deployment, which
# run over SSH as the ubuntu user.
if market_open; then
    if ! is_active thetaforge-auto-executor.service; then
        logger -t "$LOG_TAG" "market open -- starting ibgateway, bridge, auto-executor, auto-manager, market-data"
        systemctl start ibgateway.service
        # IB Gateway + IBC's login flow takes real time (Java startup, IBC's
        # automated dialog handling, IBKR auth). Starting the Bridge before
        # it's ready just means the Bridge's own ensure_connected() retries
        # on first use, which is already how it behaves locally -- but give
        # it a head start rather than hammering a cold gateway immediately.
        sleep 45
        systemctl start thetaforge-bridge.service
        sleep 5
        systemctl start thetaforge-auto-executor.service
        systemctl start thetaforge-auto-manager.service
        systemctl start thetaforge-market-data.service
    fi
else
    if is_active thetaforge-auto-executor.service || is_active thetaforge-auto-manager.service || is_active thetaforge-bridge.service || is_active ibgateway.service || is_active thetaforge-market-data.service; then
        logger -t "$LOG_TAG" "market closed -- stopping auto-executor, auto-manager, bridge, ibgateway, market-data"
        systemctl stop thetaforge-auto-executor.service
        systemctl stop thetaforge-auto-manager.service
        systemctl stop thetaforge-market-data.service
        systemctl stop thetaforge-bridge.service
        systemctl stop ibgateway.service
    fi
fi
