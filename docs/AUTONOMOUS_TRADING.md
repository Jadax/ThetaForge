# Autonomous Paper Trading (Oracle Cloud VM)

This documents the always-on stack that runs the full pipeline —
IB Gateway login, the Bridge, and autonomous order placement — without
depending on any local machine being on. It exists because the Bridge (and
therefore TWS/IB Gateway) can only ever run on the machine actually holding
the IBKR session; moving that machine from a personal laptop to an
always-on VM is what makes unattended, multi-month operation possible at
all. See `docs/HOSTED_TERMINAL.md` for the (separate, optional) hosted
*dashboard* — this doc is about the trading pipeline itself.

## What's running, and where

```
Oracle Cloud "Always Free" VM (af-johannesburg-1, VM.Standard.E2.1.Micro)
  92.4.132.188 — 1 OCPU, 1GB RAM + 2GB swap, Ubuntu 24.04 LTS Minimal

  xvfb.service              — virtual display :1 (IB Gateway is a GUI app)
  ibgateway.service         — IB Gateway via IBC, auto-login, paper mode only
  thetaforge-bridge.service — bridge/main.py, localhost:8002 only
  thetaforge-auto-executor.service — deployment/vm_auto_executor.py (entries)
  thetaforge-auto-manager.service  — deployment/vm_auto_manager.py (exits, optional)
  thetaforge-market-supervisor.timer (every 5 min)
    -> market_hours_supervisor.sh starts/stops the four services above
       based on the Advisor's real NYSE-calendar market_open status
```

Everything above is orchestrated by systemd. None of the four core services
are `enabled` for boot-time autostart — the supervisor timer is the only
thing enabled at boot, and it starts/stops the rest based on live market
hours, so "opens at market open, closes at market close" holds regardless of
when the VM itself reboots.

## Why this shape

- **The Bridge is never exposed publicly.** It binds `127.0.0.1:8002` only.
  The auto-executor reaches it because it runs on the same VM — this
  preserves the exact security posture documented in `docs/PAPER_BRIDGE.md`
  for the local setup; moving the Bridge to a VM didn't change that.
- **The auto-executor adds no new trade-safety logic.** It polls
  `GET /api/advisor/notifications` (already quality-gated by the same
  composite/edge/POP/liquidity thresholds the dashboard uses), fetches a
  fully-specified structure via `POST /api/advisor/recommend`, and submits
  through the local Bridge's `POST /orders/submit-combo` — which
  independently re-verifies live IBKR quotes, defined-risk, and the weekly
  capital-limit ledger regardless of who's calling it. See the module
  docstring in `deployment/vm_auto_executor.py` for the full flow.
- **The auto-manager (exits) is a separate, optional service.** It runs the
  exit framework (50% take-profit, 21-DTE gamma window, 2x-credit stop,
  pre-earnings exit) the same way the dashboard does — asking the Advisor's
  `POST /api/advisor/positions/management` (the only exit-decision place)
  for open positions reported by the Bridge ledger, then optionally
  submitting the recommended closes through the Bridge's
  `POST /orders/close-combo` (the only exit-execution place). It builds no
  legs and makes no decisions itself. **It is advisory-only unless you set
  `AUTO_CLOSE_ENABLED=true`** in `thetaforge-auto-manager.service`; the
  `review_tested` action is never auto-submitted. Closing orders land in the
  same ledger as entries and the public journal's lifecycle sync folds them
  into the parent entry as a `closed` status — a close is a lifecycle event,
  never a phantom new position.
- **Market-hours source of truth lives in one place.** Both the Advisor's
  own background scan and this VM's supervisor consult
  `agents/trade_engine/background_scanner.py`'s `is_market_hours()` (via
  `GET /api/advisor/scanner/status`) rather than each re-implementing a
  calendar. Real NYSE holidays and half-days included.
- **The paper-only lock is intentional and should stay.** `bridge/main.py`'s
  `ensure_connected()` hard-rejects anything that isn't a paper port
  (4002/7497) or a `DU`-prefixed paper account. `TradingMode=paper` is also
  set in IBC's config as a second, independent layer. Do not weaken either
  to "make live easier later" — going live should be a deliberate, reviewed
  change made at the time it's actually wanted, with its own additional
  safeguards, not a standing unlock sitting in unattended automation.

## Setup gotchas (undocumented anywhere else — read before rebuilding)

If this VM is ever rebuilt from scratch, these are the non-obvious fixes
that were needed and are not mentioned in IBC's own documentation:

1. **`JAVA_PATH` in `gatewaystart.sh` must point at the `bin` directory**,
   not the JRE root. IBC appends `/java` directly to whatever you give it —
   pointing at the JRE root produces `Java installation at .../java does not
   exist` even though Java is actually installed one level down. The
   installer's bundled JRE lives at
   `~/.local/share/i4j_jres/<hash>/<version>/bin/java` — find the exact hash
   via `find ~/.local/share/i4j_jres -iname java -type f`.
2. **The Ubuntu Minimal image is missing X11 libraries IB Gateway's Swing
   UI needs even under Xvfb.** Without them it fails immediately with
   `libawt_xawt.so: libXtst.so.6: cannot open shared object file`. Install
   `libxtst6 libxrender1 libxi6 libxext6 libxrandr2 libxcursor1
   libxinerama1 libfontconfig1 libfreetype6` before first launch.
3. **`Windows PowerShell 5.1`'s `Join-Path` only takes two segments** — see
   the equivalent note already in `deployment/cloudflare_deploy_terminal.ps1`
   if writing similar tooling for this stack.
4. **Git commits on the VM need an identity set once per clone**
   (`git config user.name`/`user.email`) — reused the existing
   `ThetaForge <thetaforge@users.noreply.github.com>` identity already
   present in this repo's history.
5. **`nano` isn't on the Minimal image.** `sudo apt-get install -y nano` if
   you need to hand-edit `/opt/ibc/config.ini` again (e.g. to update
   credentials) — the alternative, `unminimize`, pulls back far more than
   needed.

## Credentials and secrets on this VM

| What | Where | How it got there |
| --- | --- | --- |
| IBKR paper login | `/opt/ibc/config.ini` (`IbLoginId`/`IbPassword`), mode 600, owned by `ubuntu` | Typed directly by the account owner via `nano` over SSH — never seen by any automation or this repo |
| `BRIDGE_ACCESS_TOKEN` | `thetaforge-bridge.service` and `thetaforge-auto-executor.service` systemd unit files, mode 600 | Generated locally, matches the value already used for local Bridge access |
| `ADVISOR_API_TOKEN` | `thetaforge-auto-executor.service` and `market_hours_supervisor.sh` | Matches the value set in Render's environment variables |
| Git deploy key (journal push) | `~/.ssh/thetaforge_repo_deploy_key`, write access to this repo only | Public half added as a GitHub Deploy Key by the account owner |
| SSH access to the VM | `~/.ssh/thetaforge_vm` (this repo owner's machine only) | Provisioned at VM creation |

## Capital limit

The auto-executor's weekly capital limit is `CAPITAL_LIMIT` in
`thetaforge-auto-executor.service` (currently `5000`) — it does not share the
dashboard's browser-stored "Maximum options capital" setting, since it's a
headless service with no browser. To change it:

```bash
ssh -i ~/.ssh/thetaforge_vm ubuntu@92.4.132.188
sudo nano /etc/systemd/system/thetaforge-auto-executor.service   # edit CAPITAL_LIMIT=
sudo systemctl daemon-reload
sudo systemctl restart thetaforge-auto-executor.service
```

## Checking status

```bash
ssh -i ~/.ssh/thetaforge_vm ubuntu@92.4.132.188

sudo systemctl status ibgateway.service thetaforge-bridge.service thetaforge-auto-executor.service
sudo journalctl -u thetaforge-auto-executor.service -n 50 --no-pager
sudo journalctl -u thetaforge-market-supervisor.service -n 20 --no-pager
curl -s http://127.0.0.1:8002/health
curl -s -H "X-ThetaForge-Bridge-Token: <token>" http://127.0.0.1:8002/orders
```

## Redeploying Bridge code changes

The VM runs its own copy of `bridge/main.py`, not a live checkout — after
changing it in this repo:

```bash
scp -i ~/.ssh/thetaforge_vm bridge/main.py ubuntu@92.4.132.188:/opt/thetaforge-bridge/bridge/main.py
ssh -i ~/.ssh/thetaforge_vm ubuntu@92.4.132.188 'sudo systemctl restart thetaforge-bridge.service'
```

`deployment/vm_auto_executor.py`, `deployment/vm_auto_manager.py`,
`market_hours_supervisor.sh`, and `journal_sync_push.sh` are copied the same
way and restarted via their respective service names.

## Known limitation

CBOE's free delayed-quotes feed rate-limits Render's outbound IP under
concurrent load — see the `v1.2.4` changelog entry and the comment above
`SCAN_CONCURRENCY` in `background_scanner.py`. This affects the Advisor's
scanning, not this VM directly, but it's the reason the auto-executor's
notification polling can occasionally see a quiet cycle even during market
hours — the background scan itself may have degraded some symbols to
`option_chain_unavailable` for that pass.
