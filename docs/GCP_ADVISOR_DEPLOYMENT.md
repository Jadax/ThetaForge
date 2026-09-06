# Google Cloud (GCP) Advisor Deployment

Free, proven fallback for the Advisor while Oracle ARM capacity in
af-johannesburg-1 remains exhausted. Deployed 2026-09-06.

Provider: **Google Cloud e2-micro** (always-free, x86_64, 1 GB RAM, 2 shared
vCPU) in `us-west1-b`, project `horizon-ai-502610`, static external IP
`35.212.227.109`.

## Why GCP

Oracle A1 ARM capacity has been "Out of host capacity" in af-johannesburg-1
(the only free-tier region) since 2026-08-31. Render free (512 MB) OOMs on
the full scan. GCP's always-free e2-micro is the only proven-major-cloud,
always-free VM available immediately. At 1 GB RAM + 2 GB swap it is a
2×-memory upgrade over Render with always-on uptime and 4× the compute.

The ARM retry keeps running on the AMD VM (`thetaforge-arm-retry.service`)
as the eventual free upgrade; GCP is the live host today.

## Current Architecture

```
GCP VM (35.212.227.109)              AMD VM (92.4.132.188, unchanged)
┌──────────────────────┐              ┌──────────────────────────────────┐
│ uvicorn (systemd)    │              │ IB Gateway                       │
│   └─ advisor (:8000) │◄─────────────│ Bridge (localhost:8002)           │
│   (direct, no Docker)│  HTTP from   │ Executor  (ADVISOR_URL → GCP)    │
│                      │  AMD VM      │ Manager   (ADVISOR_URL → GCP)    │
│ 1 GB RAM + 2 GB swap│              │ Market Data (port 8003)          │
│ $0 forever           │              │ Supervisor (checks GCP /health)  │
└──────────────────────┘              └──────────────────────────────────┘
```

## GCP Resources

- **Instance:** `thetaforge-advisor` (us-west1-b, e2-micro, Ubuntu 24.04 LTS,
  pd-standard 20 GB boot disk, network tier STANDARD so egress stays free).
- **Firewall:** `allow-thetaforge-8000` and `allow-thetaforge-80` (ingress,
  source 0.0.0.0/0, target tag `thetaforge-advisor`).
- **App:** `/opt/thetaforge` (repo clone, `venv`, `.env`), run as user
  `thetaforge` via `thetaforge-advisor.service` (direct uvicorn, 1 worker,
  port 8000).
- **Env:** `ADVISOR_API_TOKEN`, `BROKER_ACCESS_TOKEN`, `BRIDGE_ACCESS_TOKEN`,
  `BRIDGE_URL`, `DASHBOARD_ORIGINS` in `/opt/thetaforge/.env`.
- **Swap:** 2 GB `/swapfile` (persisted in `/etc/fstab`) — the OOM safety net
  that made 1 GB viable.
- **Memory monitor:** `/opt/thetaforge/gcp_monitor_memory.sh` (every 5 min via
  ubuntu crontab) → `/var/log/thetaforge/memory_monitor.log`.
- **SSH:** `ssh -i ~/.ssh/thetaforge_vm ubuntu@35.212.227.109`
- **GitHub deploy key:** `/home/ubuntu/.ssh/thetaforge_repo_deploy_key`
  (copied from the AMD VM; clones `git@github.com:Jadax/ThetaForge.git`).

## AMD VM Changes (done 2026-09-06)

All `ADVISOR_URL` references swapped from `https://thetaforge-advisor.onrender.com`
to `http://35.212.227.109:8000`:

- `thetaforge-auto-executor.service` (backup: `.bak-20260906150154`)
- `thetaforge-auto-manager.service` (backup: `.bak-20260906150154`)
- `market_hours_supervisor.sh` (backup: `.bak-20260906150154`)
- `render-keepalive.timer` stopped+disabled (GCP never spins down; keepalive
  traffic to Render was unnecessary).

Render stays deployed as a warm-ish fallback (not polled, no keepalive).

## Verification

- `http://35.212.227.109:8000/health/` → `{"status":"healthy"}`
- `/api/advisor/scanner/status`, `/api/advisor/notifications`,
  `/api/advisor/equity/notifications` all return 200 with the token.
- Advisor RSS ~135 MB idle; 0 swap used at idle.
- Market-supervisor's `"market_open":true` grep logic verified against GCP.

## Rollback / Switch-back

Each patched file has a `.bak-20260906150154` beside it. To restore Render as
primary: copy the `.bak` files back, re-enable `render-keepalive.timer`, run
`systemctl daemon-reload`, and restart involved units.

## Redeploy Checklist (fresh GCP box / disaster)

1. `gcloud compute instances create` (free-tier-safe flags as above).
2. scp `deployment/provision_gcp.sh`, `~/.ssh/thetaforge_vm.pub`, and the
   GitHub deploy key to the VM; run the script as `ubuntu`.
3. Fix the `.env` write + systemd unit if `set -e` aborted the script
   (see `deployment/gcp_systemd.sh`).
4. Add 2 GB swap + memory monitor (this doc's commands).
5. Point AMD VM `ADVISOR_URL` at the new IP (script
   `deployment/swap_advisor_gcp.sh`).