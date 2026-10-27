#!/bin/bash
# ThetaForge executor-host bootstrap for a fresh Ubuntu VM. Sized for the
# GCP Always-Free e2-micro (1 vCPU / 1 GiB RAM + 2G swap -- the same profile
# as the proven Oracle E2.1.Micro), works on any equivalent box. Mirrors the
# proven Oracle layout exactly, because journal_sync_push.sh and the
# supervisor script hardcode paths under /opt/thetaforge-bridge:
#
#   /opt/thetaforge-bridge/
#     ThetaForge/            main clone (git pull / journal auto-sync commits)
#     ThetaForge-gh-pages/   gh-pages clone (what journal.astraiva.app serves)
#     bridge/main.py         the ONLY order path, local copy, localhost:8002
#     data/                  paper_order_ledger.json lives here
#     venv/                  bridge + executors + market-data interpreter
#     vm_auto_executor.py vm_auto_manager.py _vm_common.py
#     market_data_service.py market_hours_supervisor.sh journal_sync_push.sh
#   /opt/ibc/                IBC 3.24.2 (auto-login, TradingMode=paper)
#   /home/ubuntu/Jts/        IB Gateway (offline/stable installer, silent)
#
# Run as ubuntu over SSH (passwordless sudo), BEFORE first start:
#   BRIDGE_TOKEN=<value from the advisor's .env> bash bridge_vm_setup.sh
#
# After it finishes: edit /opt/ibc/config.ini with the IBKR paper
# credentials (typed by the account owner -- never automated), smoke-test
# the gateway, then enable the supervisor timer.
set -euo pipefail

REPO_URL="https://github.com/Jadax/ThetaForge.git"   # public read; push uses the deploy key
SSH_REMOTE="git@github.com:Jadax/ThetaForge.git"     # set after the deploy key exists
BRIDGE_ROOT="/opt/thetaforge-bridge"
# Bind host for the bridge. 127.0.0.1 = localhost-only (matches PAPER_BRIDGE
# posture on a single machine). When the advisor lives on a sibling VM in the
# same VPC (the GCP layout), pass BRIDGE_HOST=0.0.0.0: the VPC firewall is the
# real boundary (no public ingress rule for 8002 exists), and BRIDGE_ACCESS_TOKEN
# still guards every call. 0.0.0.0 also keeps on-box 127.0.0.1 curls working.
BRIDGE_HOST="${BRIDGE_HOST:-127.0.0.1}"
ADVISOR_URL="${ADVISOR_URL:-http://10.0.0.161:8000}"
ADVISOR_TOKEN="${ADVISOR_TOKEN:-aqMnE8q5WFeGCevbHW1ru-zt7bguZyFCdrsyhBJ4ioQ}"
BRIDGE_TOKEN="${BRIDGE_TOKEN:-}"
IBC_VERSION="3.24.2"
GW_INSTALLER_URL="https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh"

if [ -z "$BRIDGE_TOKEN" ]; then
    echo "ERROR: BRIDGE_TOKEN is required (the authoritative value lives in the advisor's" >&2
    echo "       .env as BRIDGE_ACCESS_TOKEN; it must match on both sides)." >&2
    exit 1
fi
if [ "$(id -u)" -ne 0 ]; then SUDO="sudo"; else SUDO=""; fi

echo "=== ThetaForge executor-host setup ==="

# --- 1. Packages: X11 libs for the Gateway's Swing UI (needed even under
#        Xvfb -- they are missing on minimal images) + xterm for IBC ---
$SUDO apt-get update -qq
$SUDO apt-get install -y -qq git curl unzip xterm nano ca-certificates python3-venv \
    xvfb libxtst6 libxrender1 libxi6 libxext6 libxrandr2 libxcursor1 libxinerama1 \
    libfontconfig1 libfreetype6 >/dev/null

# --- 2. 2G swap (1 GiB RAM is not enough for Gateway JVM + services) ---
if ! swapon --show | grep -q swapfile; then
    $SUDO fallocate -l 2G /swapfile || $SUDO dd if=/dev/zero of=/swapfile bs=1M count=2048
    $SUDO chmod 600 /swapfile
    $SUDO mkswap /swapfile >/dev/null
    $SUDO swapon /swapfile
    echo '/swapfile none swap sw 0 0' | $SUDO tee -a /etc/fstab >/dev/null
    echo "swap enabled"
fi

# --- 3. Layout + clones ---
$SUDO mkdir -p "$BRIDGE_ROOT" "$BRIDGE_ROOT/data" "$BRIDGE_ROOT/bridge"
$SUDO chown -R ubuntu:ubuntu "$BRIDGE_ROOT"
if [ ! -d "$BRIDGE_ROOT/ThetaForge/.git" ]; then
    git clone -q "$REPO_URL" "$BRIDGE_ROOT/ThetaForge"
    echo "main clone created"
else
    git -C "$BRIDGE_ROOT/ThetaForge" pull --ff-only -q || true
fi
if [ ! -d "$BRIDGE_ROOT/ThetaForge-gh-pages/.git" ]; then
    git clone -q -b gh-pages --single-branch "$REPO_URL" "$BRIDGE_ROOT/ThetaForge-gh-pages"
    echo "gh-pages clone created"
fi
REPO="$BRIDGE_ROOT/ThetaForge"
for d in "$REPO" "$BRIDGE_ROOT/ThetaForge-gh-pages"; do
    git -C "$d" config user.name  "ThetaForge"
    git -C "$d" config user.email "thetaforge@users.noreply.github.com"
done

# --- 4. Deploy key (journal auto-publish push). Optional here; copied in
#        from the GCP VM if it is missing: needed only by journal_sync_push.sh ---
KEY=/home/ubuntu/.ssh/thetaforge_repo_deploy_key
if [ -f "$KEY" ]; then
    chmod 600 "$KEY"
    timeout 20 ssh-keyscan -t ed25519,rsa github.com >> /home/ubuntu/.ssh/known_hosts 2>/dev/null || true
    for d in "$REPO" "$BRIDGE_ROOT/ThetaForge-gh-pages"; do
        git -C "$d" remote set-url origin "$SSH_REMOTE"
    done
else
    echo "WARNING: deploy key $KEY not present yet -- clones stay on https and" >&2
    echo "         journal_sync_push.sh cannot publish until it is installed." >&2
fi

# --- 5. venv: what the VM services import, plus the journal live-mark
#        refresh (journal_sync_push.sh prices -> sync_journal.py imports
#        agents/data_ingestion/free_data.py: yfinance + pandas). Without
#        them the refresh fails soft and live marks silently never update
#        (found on the Oracle rebuild, 2026-10-27). ---
if [ ! -d "$BRIDGE_ROOT/venv" ]; then
    python3 -m venv "$BRIDGE_ROOT/venv"
fi
"$BRIDGE_ROOT/venv/bin/pip" install -q --upgrade pip
"$BRIDGE_ROOT/venv/bin/pip" install -q 'fastapi>=0.104' 'uvicorn[standard]>=0.24' \
    'pydantic>=2.5' 'ib_insync>=0.9.86' 'httpx>=0.25'     'yfinance>=0.2.31' 'pandas>=2.1.0' 'numpy>=1.26.0'
echo "venv ready"

# --- 6. Flat service copies (the VM runs copies, not a live checkout;
#        redeploy = scp + restart per docs/AUTONOMOUS_TRADING.md) ---
install -m 644 "$REPO/deployment/vm_auto_executor.py"     "$BRIDGE_ROOT/vm_auto_executor.py"
install -m 644 "$REPO/deployment/vm_auto_manager.py"      "$BRIDGE_ROOT/vm_auto_manager.py"
install -m 644 "$REPO/deployment/_vm_common.py"           "$BRIDGE_ROOT/_vm_common.py"
install -m 644 "$REPO/deployment/vm_market_data_service.py" "$BRIDGE_ROOT/market_data_service.py"
install -m 755 "$REPO/deployment/market_hours_supervisor.sh" "$BRIDGE_ROOT/market_hours_supervisor.sh"
install -m 755 "$REPO/deployment/journal_sync_push.sh"     "$BRIDGE_ROOT/journal_sync_push.sh"
install -m 644 "$REPO/bridge/main.py"                     "$BRIDGE_ROOT/bridge/main.py"
echo "service files installed"

# --- 7. IBC (flat zip -> /opt/ibc, scripts executable) ---
if [ ! -f /opt/ibc/gatewaystart.sh ]; then
    curl -fsSL --retry 3 -o /tmp/ibc.zip \
        "https://github.com/IbcAlpha/IBC/releases/download/${IBC_VERSION}/IBCLinux-${IBC_VERSION}.zip"
    $SUDO mkdir -p /opt/ibc
    $SUDO unzip -o -q /tmp/ibc.zip -d /opt/ibc
    rm -f /tmp/ibc.zip
fi
$SUDO chown -R ubuntu:ubuntu /opt/ibc
chmod +x /opt/ibc/*.sh /opt/ibc/scripts/*.sh
$SUDO mkdir -p /opt/ibc/logs && $SUDO chown ubuntu:ubuntu /opt/ibc/logs
# gatewaystart.sh defaults -> this layout:
#   IBC_INI lives beside the scripts, LOG_PATH inside /opt/ibc, and
#   TRADING_MODE=paper as one of two independent paper-only layers
#   (the other is TradingMode=paper in config.ini).
sed -i 's|^IBC_INI=~/ibc/config.ini|IBC_INI=/opt/ibc/config.ini|' /opt/ibc/gatewaystart.sh
sed -i 's|^LOG_PATH=~/ibc/logs|LOG_PATH=/opt/ibc/logs|' /opt/ibc/gatewaystart.sh
sed -i 's|^TRADING_MODE=$|TRADING_MODE=paper|' /opt/ibc/gatewaystart.sh
echo "IBC installed at /opt/ibc"

# --- 8. IB Gateway: offline/stable installer, silent (IBC cannot use the
#        self-updating build), then derive TWS_MAJOR_VRSN from what the
#        installer actually created under ~/Jts ---
GW_JARS=$(find "$HOME/Jts" -maxdepth 4 -type d -name jars 2>/dev/null | head -1 || true)
if [ -z "$GW_JARS" ] && [ -d "$HOME/ibgateway" ] && [ ! -d "$HOME/Jts/ibgateway" ]; then
    # Current standalone Gateway installs FLAT into ~/ibgateway (no version
    # subdir); IBC's ibcstart.sh wants ~/Jts/ibgateway/<majorVer>. The version
    # is machine-readable in the install4j params (majorVersion=1050-style).
    GW_VER=$(sed -n 's/.*name="majorVersion" value="\([^"]*\)".*/\1/p' \
        "$HOME/ibgateway/.install4j/i4jparams.conf" 2>/dev/null | head -1 || true)
    if [ -z "$GW_VER" ]; then
        GW_DESK=$(basename "$HOME/ibgateway"/*.desktop 2>/dev/null | head -1 || true)
        GW_VER=$(printf '%s' "$GW_DESK" | sed 's/^IB Gateway //; s/\.desktop$//; s/\.//g' || true)
    fi
    if [ -n "$GW_VER" ]; then
        mkdir -p "$HOME/Jts/ibgateway/$GW_VER"
        (shopt -s dotglob; mv "$HOME/ibgateway"/* "$HOME/Jts/ibgateway/$GW_VER"/)
        rmdir "$HOME/ibgateway" 2>/dev/null || rm -rf "$HOME/ibgateway"
        echo "Gateway install relocated to ~/Jts/ibgateway/$GW_VER"
    fi
    GW_JARS=$(find "$HOME/Jts" -maxdepth 4 -type d -name jars 2>/dev/null | head -1 || true)
fi
if [ -z "$GW_JARS" ]; then
    curl -fsSL --retry 3 -o /tmp/ibgw.sh "$GW_INSTALLER_URL"
    chmod +x /tmp/ibgw.sh
    # Explicit install dir (the installer's ~/ibgateway default predates the
    # Oracle layout) and run from $HOME (a cwd the installer cannot cd back
    # to -- like the ssh user's login dir -- makes install4j exit nonzero
    # AFTER a successful install).
    (cd "$HOME" && /tmp/ibgw.sh -q "$HOME/Jts/ibgateway")
    rm -f /tmp/ibgw.sh
    GW_JARS=$(find "$HOME/Jts" -maxdepth 4 -type d -name jars 2>/dev/null | head -1 || true)
fi
if [ -z "$GW_JARS" ]; then
    echo "ERROR: Gateway install not found under ~/Jts (installer failed?)." >&2
    exit 1
fi
GW_VER=$(basename "$(dirname "$GW_JARS")")
sed -i "s/^TWS_MAJOR_VRSN=.*/TWS_MAJOR_VRSN=${GW_VER}/" /opt/ibc/gatewaystart.sh
echo "Gateway installed (version dir: $GW_VER)"
# Gotcha #1 in docs/AUTONOMOUS_TRADING.md: JAVA_PATH must be the *bin* dir
# (IBC appends /java). Only set it when the installer's bundled JRE is
# locatable; otherwise leave it empty so ibcstart.sh auto-detects it.
JAVAF=$(find "$HOME/Jts/ibgateway" "$HOME/.local/share/i4j_jres" -type f -path '*/bin/java' 2>/dev/null | head -1 || true)
if [ -n "$JAVAF" ]; then
    sed -i "s|^JAVA_PATH=.*|JAVA_PATH=$(dirname "$JAVAF")|" /opt/ibc/gatewaystart.sh
    echo "JAVA_PATH -> $(dirname "$JAVAF")"
fi

# --- 9. config.ini: paper mode + unattended-friendly dialog handling.
#        Credentials stay EMPTY for the account owner to type via nano.
#        ExistingSessionDetectedAction=primaryoverride makes this VM's login
#        win over any stale session elsewhere (keep the old Oracle VM off).
#        AllowBlindTrading=yes: a stuck IBKR warning dialog would freeze an
#        unattended session; the real gate is bridge/main.py re-verifying
#        live quotes + defined risk before any order is submitted. ---
# Guarded by our own header line: the first run replaces IBC's extracted
# default (edemo/live), later runs never clobber typed credentials.
if ! grep -q '^# ThetaForge executor host' /opt/ibc/config.ini 2>/dev/null; then
    [ -f /opt/ibc/config.ini.orig ] || cp /opt/ibc/config.ini /opt/ibc/config.ini.orig
    cat > /opt/ibc/config.ini <<'CFG'
# ThetaForge executor host -- see docs/AUTONOMOUS_TRADING.md.
# Fill in the paper credentials below (owned by the account holder).
IbLoginId=
IbPassword=
TradingMode=paper
AcceptNonBrokerageAccountWarning=yes
ExistingSessionDetectedAction=primaryoverride
AcceptIncomingConnectionAction=accept
MinimizeMainWindow=yes
AllowBlindTrading=yes
CFG
chown ubuntu:ubuntu /opt/ibc/config.ini
chmod 600 /opt/ibc/config.ini
fi

# --- 10. systemd units. Only xvfb + the supervisor timer are enabled at
#         boot (per docs/AUTONOMOUS_TRADING.md: the timer starts/stops the
#         rest around real market hours, so boot timing never matters). ---
$SUDO tee /etc/systemd/system/xvfb.service >/dev/null <<'UNIT'
[Unit]
Description=Xvfb virtual display :1 (IB Gateway is a GUI app)
After=network.target

[Service]
User=ubuntu
ExecStart=/usr/bin/Xvfb :1 -screen 0 1280x1024x24 -ac
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

$SUDO tee /etc/systemd/system/ibgateway.service >/dev/null <<'UNIT'
[Unit]
Description=IB Gateway via IBC (paper mode, auto-login)
After=network-online.target xvfb.service
Wants=network-online.target xvfb.service

[Service]
Type=simple
User=ubuntu
Environment=DISPLAY=:1
# -inline: execs IBC in this process so systemd tracks (and can TERM) the
# real Java process tree instead of an xterm wrapper.
ExecStart=/opt/ibc/gatewaystart.sh -inline
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
UNIT

$SUDO tee /etc/systemd/system/thetaforge-bridge.service >/dev/null <<UNIT
[Unit]
Description=ThetaForge paper Bridge ($BRIDGE_HOST:8002, the only order path)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=$BRIDGE_ROOT/bridge
Environment=PYTHONUNBUFFERED=1
Environment=BRIDGE_ACCESS_TOKEN=$BRIDGE_TOKEN
ExecStart=$BRIDGE_ROOT/venv/bin/uvicorn main:app --host $BRIDGE_HOST --port 8002
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

$SUDO tee /etc/systemd/system/thetaforge-auto-executor.service >/dev/null <<UNIT
[Unit]
Description=ThetaForge autonomous paper executor (entries)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=$BRIDGE_ROOT
Environment=PYTHONUNBUFFERED=1
Environment=ADVISOR_URL=$ADVISOR_URL
Environment=ADVISOR_API_TOKEN=$ADVISOR_TOKEN
Environment=BRIDGE_URL=http://127.0.0.1:8002
Environment=BRIDGE_ACCESS_TOKEN=$BRIDGE_TOKEN
Environment=CAPITAL_LIMIT=5000
Environment=POLL_INTERVAL_SECONDS=60
ExecStart=$BRIDGE_ROOT/venv/bin/python vm_auto_executor.py
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
UNIT

$SUDO tee /etc/systemd/system/thetaforge-auto-manager.service >/dev/null <<UNIT
[Unit]
Description=ThetaForge autonomous paper position manager (exits, advisory)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=$BRIDGE_ROOT
Environment=PYTHONUNBUFFERED=1
Environment=ADVISOR_URL=$ADVISOR_URL
Environment=ADVISOR_API_TOKEN=$ADVISOR_TOKEN
Environment=BRIDGE_URL=http://127.0.0.1:8002
Environment=BRIDGE_ACCESS_TOKEN=$BRIDGE_TOKEN
Environment=CAPITAL_LIMIT=5000
Environment=AUTO_CLOSE_ENABLED=false
Environment=POLL_INTERVAL_SECONDS=60
ExecStart=$BRIDGE_ROOT/venv/bin/python vm_auto_manager.py
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
UNIT

# Fresh token: generate once per install so the proxy's credential is not a
# repo secret. The same value must be set as IBKR_MARKET_DATA_TOKEN on the
# advisor host (printed at the end of this script).
if [ -n "${MARKET_DATA_TOKEN:-}" ]; then
    MD_TOKEN="$MARKET_DATA_TOKEN"
else
    MD_TOKEN=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
    echo "$MD_TOKEN" > /opt/thetaforge-bridge/.market_data_token
    chown ubuntu:ubuntu /opt/thetaforge-bridge/.market_data_token
    chmod 600 /opt/thetaforge-bridge/.market_data_token
fi

$SUDO tee /etc/systemd/system/thetaforge-market-data.service >/dev/null <<UNIT
[Unit]
Description=ThetaForge read-only IBKR market-data proxy (:8003, own token)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=$BRIDGE_ROOT
Environment=PYTHONUNBUFFERED=1
Environment=MARKET_DATA_ACCESS_TOKEN=$MD_TOKEN
ExecStart=$BRIDGE_ROOT/venv/bin/uvicorn market_data_service:app --host 0.0.0.0 --port 8003
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

$SUDO tee /etc/systemd/system/thetaforge-market-supervisor.service >/dev/null <<UNIT
[Unit]
Description=Start/stop the trading stack around real NYSE market hours
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Environment=ADVISOR_URL=$ADVISOR_URL
Environment=ADVISOR_TOKEN=$ADVISOR_TOKEN
ExecStart=$BRIDGE_ROOT/market_hours_supervisor.sh
UNIT

$SUDO tee /etc/systemd/system/thetaforge-market-supervisor.timer >/dev/null <<'UNIT'
[Unit]
Description=Run the market-hours supervisor every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
AccuracySec=30s
Unit=thetaforge-market-supervisor.service

[Install]
WantedBy=timers.target
UNIT

# Units carrying tokens are root-only reads (systemd reads them as root);
# services still run as ubuntu per their User= lines.
$SUDO chmod 600 /etc/systemd/system/thetaforge-bridge.service \
    /etc/systemd/system/thetaforge-auto-executor.service \
    /etc/systemd/system/thetaforge-auto-manager.service \
    /etc/systemd/system/thetaforge-market-data.service
$SUDO systemctl daemon-reload
$SUDO systemctl enable xvfb.service >/dev/null 2>&1
$SUDO systemctl enable thetaforge-market-supervisor.timer >/dev/null 2>&1
$SUDO systemctl start xvfb.service

echo ""
echo "=== Setup complete ==="
echo "Do these next (in order):"
echo "  1. sudo nano /opt/ibc/config.ini    <- type the IBKR paper credentials"
echo "     (IbLoginId / IbPassword; paper only -- TradingMode=paper is enforced"
echo "      in both config.ini and gatewaystart.sh, plus bridge/main.py)"
echo "  2. Smoke test:  sudo systemctl start ibgateway.service"
echo "     then:  sudo journalctl -u ibgateway.service -n 50 --no-pager"
echo "     and:   /opt/ibc/logs/ibc-*Gateway-*.txt  (IBC's own log)"
echo "     Stop it again with sudo systemctl stop ibgateway.service if you want"
echo "     the supervisor to manage the cycle from here on."
if [ ! -f "$KEY" ]; then
    echo "  3. Install the journal push deploy key at $KEY"
    echo "     (scp it from the GCP VM: /home/ubuntu/.ssh/thetaforge_repo_deploy_key)"
    echo "     then re-run this script's step 4 remote-URL switch, or run:"
    echo "       git -C $REPO remote set-url origin $SSH_REMOTE"
fi
echo "  4. sudo systemctl enable --now thetaforge-market-supervisor.timer"
echo "     (only enabled service at boot; it starts/stops the five services)"
echo "  5. Advisor host env (its .env), then sudo systemctl restart thetaforge-advisor:"
echo "       BRIDGE_URL=http://<THIS_VM_IP>:8002"
echo "       IBKR_MARKET_DATA_URL=http://<THIS_VM_IP>:8003"
echo "       IBKR_MARKET_DATA_TOKEN=$MD_TOKEN"
echo "     (this market-data token was generated by this install:"
echo "      /opt/thetaforge-bridge/.market_data_token)"
echo "Status check:"
echo "  sudo systemctl status ibgateway.service thetaforge-bridge.service"
echo "  curl -s http://127.0.0.1:8002/health"
echo "  curl -s -H 'X-Market-Data-Token: $MD_TOKEN' http://127.0.0.1:8003/health"
