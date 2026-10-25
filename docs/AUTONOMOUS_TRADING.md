# Autonomous Paper Trading (GCP executor VM)

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
GCP Always-Free e2-micro (thetaforge-executor, us-west1-b)
  internal 10.138.0.3, external 8.231.58.224 (egress only; the project's
  firewall has no ingress rule for 8002/8003, so nothing is internet-exposed)
  1 vCPU / 1GB RAM + 2GB swap, Ubuntu 24.04 LTS (ubuntu-2404-lts-amd64)

  xvfb.service              — virtual display :1 (IB Gateway is a GUI app)
  ibgateway.service         — IB Gateway via IBC, auto-login, paper mode only
                              (Gateway 10.50 at ~/Jts/ibgateway/1050)
  thetaforge-bridge.service — bridge/main.py, uvicorn on 0.0.0.0:8002,
                              token-guarded, firewall-closed to the internet;
                              the advisor reaches it over the VPC-internal IP
  thetaforge-auto-executor.service — deployment/vm_auto_executor.py (entries)
  thetaforge-auto-manager.service  — deployment/vm_auto_manager.py (exits, optional)
  thetaforge-market-data.service   — deployment/vm_market_data_service.py, read-only
                                      option-chain/quote proxy, VPC-internal :8003,
                                      own token (see the module's own docstring)
  thetaforge-market-supervisor.timer (every 5 min, already started)
    -> market_hours_supervisor.sh starts/stops the five services above
       based on the Advisor's real NYSE-calendar market_open status
```

Everything above is orchestrated by systemd. None of the five core services
are `enabled` for boot-time autostart — the supervisor timer is the only
thing enabled at boot, and it starts/stops the rest based on live market
hours, so "opens at market open, closes at market close" holds regardless of
when the VM itself reboots.

The Advisor is the sibling VM `thetaforge-advisor` (10.138.0.2 /
35.212.227.109, same VPC and region); its `.env` points `BRIDGE_URL` and
`IBKR_MARKET_DATA_URL` at this VM's internal IP, and carries
`BRIDGE_ACCESS_TOKEN` + `IBKR_MARKET_DATA_TOKEN` so every hop is
authenticated. Bootstrap is `deployment/bridge_vm_setup.sh` (idempotent,
mirrors the proven Oracle-era `/opt/thetaforge-bridge` layout exactly —
`journal_sync_push.sh` and the supervisor hardcode paths under it). History:
the Oracle ARM VM lost its host capacity in 2026-09; Azure free tier was
blocked at signup time (Standard_B1s restricted subscription-wide across
10 regions), which is why this host is GCP instead — free forever, not
just 12 months, and no second signup.

## Why this shape

- **The Bridge is never reachable from the public internet.** It binds
  `0.0.0.0:8002` (the advisor is a sibling VM, so localhost-only would not
  work), but the GCP project firewall has **no ingress rule** for 8002 — an
  external probe fails to connect — and `BRIDGE_ACCESS_TOKEN` still guards
  every call. This preserves the security posture documented in
  `docs/PAPER_BRIDGE.md`; moving the Bridge between hosts didn't change it.
- **The auto-executor adds no new trade-safety logic.** It polls
  `GET /api/advisor/notifications` (already quality-gated by the same
  composite/edge/POP/liquidity thresholds the dashboard uses), fetches a
  fully-specified structure via `POST /api/advisor/recommend`, and submits
  through the Bridge's `POST /orders/submit-combo` — which
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
  set in IBC's config as a second, independent layer (plus
  `AcceptNonBrokerageAccountWarning=yes`, `ExistingSessionDetectedAction=
  primaryoverride`, `AllowBlindTrading=yes` for unattended login). Do not
  weaken any of it to "make live easier later" — going live should be a
  deliberate, reviewed change made at the time it's actually wanted, with
  its own additional safeguards, not a standing unlock sitting in unattended
  automation.
- **The market-data proxy is no longer exposed to the public internet at
  all** (an improvement over the Oracle-era direct port opening): only the
  advisor on the same VPC can reach it, still with its own port (8003, not
  8002), its own IB API client ID, its own access token, and read-only
  endpoints only (`/option-chain`, `/stock`, `/stock-history`) — nothing
  that can place an order or see account positions.
  `agents/data_ingestion/free_data.py` calls it as the priority-1 data
  source (`IBKR_MARKET_DATA_URL`/`TOKEN` env vars in the advisor's `.env`)
  and falls through to CBOE then yfinance on any failure, so a slow or down
  VM degrades the data source, never the scan itself.

## Setup gotchas (undocumented anywhere else — read before rebuilding)

If this VM is ever rebuilt from scratch, these are the non-obvious fixes
that were needed and are not mentioned in IBC's own documentation. Items
1–5 date from the original Oracle setup; 6–8 were hit on 2026-10-10 and are
already automated by `deployment/bridge_vm_setup.sh`:

1. **`JAVA_PATH` in `gatewaystart.sh` must point at the `bin` directory**,
   not the JRE root. IBC appends `/java` directly to whatever you give it —
   pointing at the JRE root produces `Java installation at .../java does not
   exist` even though Java is actually installed one level down.
2. **The Ubuntu Minimal image is missing X11 libraries IB Gateway's Swing
   UI needs even under Xvfb.** Without them it fails immediately with
   `libawt_xawt.so: libXtst.so.6: cannot open shared object file`. Install
   `libxtst6 libxrender1 libxi6 libxext6 libxrandr2 libxcursor1
   libxinerama1 libfontconfig1 libfreetype6` before first launch. (The
   bootstrap installs them.)
3. **`Windows PowerShell 5.1`'s `Join-Path` only takes two segments** — see
   the equivalent note already in `deployment/cloudflare_deploy_terminal.ps1`
   if writing similar tooling for this stack.
4. **Git commits on the VM need an identity set once per clone**
   (`git config user.name`/`user.email`) — reused the existing
   `ThetaForge <thetaforge@users.noreply.github.com>` identity already
   present in this repo's history.
5. **`nano` isn't on the Minimal image.** `sudo apt-get install -y nano` if
   you need to hand-edit `/opt/ibc/config.ini` again (e.g. to update
   credentials).
6. **The current standalone Gateway installer installs FLAT into
   `~/ibgateway` (no version subdir), but IBC's `ibcstart.sh` expects
   `~/Jts/ibgateway/<majorVer>/`.** The version is machine-readable in
   `~/ibgateway/.install4j/i4jparams.conf` (`majorVersion`, e.g. `1050`);
   the desktop file's `IB Gateway 10.50.desktop` name is the same number
   dotted. Relocate the flat tree into the versioned path (or re-run the
   installer with an explicit target dir: `installer.sh -q ~/Jts/ibgateway`).
7. **The installer exits nonzero *after a successful install* if it cannot
   `cd` back to its original working directory** — e.g. when started from
   an ssh login dir owned by a different user. Run it from `$HOME` (the
   bootstrap does) or the error is indistinguishable from a real failure.
8. **`~/.local/share/i4j_jres` is empty for this installer** — the JRE to
   point `JAVA_PATH` at is bundled inside the install dir itself
   (`~/Jts/ibgateway/<ver>/jre/bin`). (See gotcha 1: the `bin` dir, not the
   JRE root.)

## Credentials and secrets on this VM

| What | Where | How it got there |
| --- | --- | --- |
| IBKR paper login | `/opt/ibc/config.ini` (`IbLoginId`/`IbPassword`), mode 600, owned by `ubuntu` | Typed directly by the account owner via `nano` over SSH — never seen by any automation or this repo. Everything else in that file (TradingMode, accept-warning, session-primaryoverride, blind-trading) is pre-filled by the bootstrap |
| `BRIDGE_ACCESS_TOKEN` | `thetaforge-bridge.service`, `thetaforge-auto-executor.service`, `thetaforge-auto-manager.service` systemd units, mode 600 | Passed as `BRIDGE_TOKEN` to the bootstrap; matches the authoritative value in the advisor VM's `.env` |
| `ADVISOR_API_TOKEN` | `thetaforge-auto-executor.service`, `thetaforge-auto-manager.service`, `market_hours_supervisor.service` units | Matches the advisor's token (its `.env`) |
| `MARKET_DATA_ACCESS_TOKEN` | `thetaforge-market-data.service` unit + `/opt/thetaforge-bridge/.market_data_token`, mode 600 | Generated by the bootstrap; the same value is set as `IBKR_MARKET_DATA_TOKEN` in the advisor's `.env` |
| Git deploy key (journal push) | `~/.ssh/thetaforge_repo_deploy_key` (ubuntu), write access to this repo only | Copied from the advisor VM (`/home/ubuntu/.ssh/thetaforge_repo_deploy_key`); public half is a GitHub Deploy Key |
| SSH access to the VM | `gcloud compute ssh thetaforge-executor --zone=us-west1-b` (GCP OS Login; Windows user maps to `Tushant`, passwordless sudo; services run as `ubuntu`) | No local keypair — access flows through the account owner's gcloud login |

## Capital limit

The auto-executor's weekly capital limit is `CAPITAL_LIMIT` in
`thetaforge-auto-executor.service` (currently `5000`) — it does not share the
dashboard's browser-stored "Maximum options capital" setting, since it's a
headless service with no browser. To change it:

```bash
gcloud compute ssh thetaforge-executor --zone=us-west1-b
sudo nano /etc/systemd/system/thetaforge-auto-executor.service   # edit CAPITAL_LIMIT=
sudo systemctl daemon-reload
sudo systemctl restart thetaforge-auto-executor.service
```

## Checking status

```bash
gcloud compute ssh thetaforge-executor --zone=us-west1-b

systemctl is-active ibgateway thetaforge-bridge thetaforge-auto-executor thetaforge-auto-manager thetaforge-market-data
sudo journalctl -u thetaforge-auto-executor.service -n 50 --no-pager
sudo journalctl -u thetaforge-market-supervisor.service -n 20 --no-pager
curl -s http://127.0.0.1:8002/health
curl -s -H "X-ThetaForge-Bridge-Token: <token>" http://127.0.0.1:8002/orders
curl -s -H "X-Market-Data-Token: <token>" http://127.0.0.1:8003/health
```

From the advisor VM (cross-VPC-internal, what the scanners actually use):

```bash
gcloud compute ssh thetaforge-advisor --zone=us-west1-b
curl -s http://10.138.0.3:8002/health
curl -s -H "X-Market-Data-Token: <token>" http://10.138.0.3:8003/health
```

## Redeploying Bridge code changes

The VM runs its own copy of `bridge/main.py`, not a live checkout — after
changing it in this repo:

```bash
gcloud compute scp bridge/main.py thetaforge-executor:/tmp/main.py
gcloud compute ssh thetaforge-executor --zone=us-west1-b --command="sudo mv /tmp/main.py /opt/thetaforge-bridge/bridge/main.py && sudo systemctl restart thetaforge-bridge.service"
```

`deployment/vm_auto_executor.py`, `deployment/vm_auto_manager.py`,
`deployment/vm_market_data_service.py` (as `market_data_service.py` on the
VM), `market_hours_supervisor.sh`, and `journal_sync_push.sh` are copied the
same way and restarted via their respective service names.
`deployment/_vm_common.py` (shared `is_market_open`/`run_journal_sync`
helpers) must be copied alongside `vm_auto_executor.py` and
`vm_auto_manager.py` in the same directory — both import it as a bare,
same-directory module since neither has the full `agents/` package tree
available on the VM.

## Known limitations

- CBOE's free delayed-quotes feed rate-limits the advisor's outbound IP
  under concurrent load — see the `v1.2.4` changelog entry and the comment
  above `SCAN_CONCURRENCY` in `background_scanner.py`. This affects the
  Advisor's scanning, not this VM directly, but it's the reason the
  auto-executor's notification polling can occasionally see a quiet cycle
  even during market hours — the background scan itself may have degraded
  some symbols to `option_chain_unavailable` for that pass.
- The paper account has delayed (15-minute) stock quotes only. The Bridge
  therefore places stock DAY limits with a small marketable buffer when the
  snapshot isn't live (`STALE_QUOTE_LIMIT_BUFFER_PCT`, 0.5%: buys above the
  ask, sells below the bid) so entries and especially stop-loss exits fill;
  risk is reserved against the buffered price. (The IBKR option-chain proxy
  delivers OPRA quotes and has been the scanner's priority-1 chain source
  since 2026-08-13; CBOE then yfinance remain the automatic fallbacks.)

## Exits are automatic (all trade types)

`AUTO_CLOSE_ENABLED=true` in `thetaforge-auto-manager.service` since v1.17.28:
equity stops/targets/trails and option profit/time/loss/pre-earnings/
pre-macro/post-earnings exits are submitted through the Bridge's close
endpoints. `review_tested` stays advisory. To return to advisory mode, set it
to `false` and `sudo systemctl restart thetaforge-auto-manager`.

Option positions are marked as a whole (sold-leg mids minus bought-leg mids,
`agents/trade_engine/position_marks.py`) — never the short leg alone against
the net credit, which read a fresh spread as a 1.6x loss.

## CME micro futures options

/MES and /MNQ options trade as defined-risk spreads/condors only (the Bridge
refuses any single-leg futures option). The proxy's `/futures-option-chain`
serves delayed CME quotes (the account has no paid CME subscription; delayed
is entitled); the Bridge qualifies `FuturesOption` contracts on CME and sizes
risk with the qualified multiplier (MES 5, MNQ 2). Notifications ride the
options scanner's SPY/QQQ read.

## Order-state reconciliation

IBKR stops reporting a DAY order once its session ends, so the Bridge's
`_reconcile_lapsed_orders` (run on every ledger sync) resolves what IBKR will
never report again:

- a working (`Submitted`/`PreSubmitted`/…) record from an earlier New York
  session has lapsed → `Inactive` (or `Filled` at the filled quantity if it
  partially filled), so it stops reserving capital and blocking the
  executor's same-name guard;
- a closing order that ended with no fill un-links its parent
  (`closed_by` is set at submission), so the exit manager retries the exit
  instead of treating an unfilled stop-loss as done.

### Re-adopting positions after a ledger migration

If the executor ever moves to a fresh Bridge ledger again, the journal keeps
the old receipts as `carried`, but the exit manager can't see them. Reconcile
against IBKR itself (read-only; dry run unless `--apply`, which backs the
ledger up first). Stop the Bridge, start the Gateway, then:

```bash
sudo -u ubuntu /opt/thetaforge-bridge/venv/bin/python   /opt/thetaforge-bridge/ThetaForge/scripts/adopt_orphan_positions.py   --ledger /opt/thetaforge-bridge/data/paper_order_ledger.json   --journal /opt/thetaforge-bridge/ThetaForge/journal/trades.json [--apply]
```

It adopts carried entries (oldest first, original ids) only up to the shares
IBKR holds that no live ledger record accounts for. Done once on
2026-10-24: 14 Sept-29 entries (AEHR, AXTI, INTC, MARA, NOK, NVDA, QQQ, SPY,
XLK, XLV) re-adopted; every IBKR share is now tracked.
