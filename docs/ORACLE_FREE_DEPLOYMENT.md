# Oracle Cloud Always Free Deployment (current host)

Live since 2026-10-27. Replaces the GCP pair (`docs/GCP_ADVISOR_DEPLOYMENT.md`,
now historical): GCP's Always Free tier covers **one** e2-micro per billing
account, ThetaForge ran two, and on 2026-10-27 the GCP billing account was
closed -- which stops every VM in the project, free ones included.

Oracle's Always Free tier allows **two** `VM.Standard.E2.1.Micro` instances
(plus up to 200 GB block storage) forever, at $0, and a Free Tier tenancy
cannot accrue charges: nothing here is paid capacity. Tenancy `Jadax`, home
region `af-johannesburg-1` (Always Free resources must live in the home
region), compartment = tenancy root.

## Topology

```
Oracle VCN rovyniq-vcn, public subnet 10.0.0.0/24
┌──────────────────────────────────┐      ┌──────────────────────────────────┐
│ thetaforge-advisor  E2.1.Micro   │      │ thetaforge-bridge-vm E2.1.Micro  │
│ public 84.12.87.139              │      │ public 92.4.132.188 (egress/SSH) │
│ private 10.0.0.161               │◄────►│ private 10.0.0.78                │
│ /opt/thetaforge (user thetaforge)│ VCN  │ /opt/thetaforge-bridge           │
│ thetaforge-advisor.service :8000 │      │ IB Gateway (IBC, paper) :4002    │
│ 1 GB RAM + 2 GB swap             │      │ Bridge :8002 · market data :8003 │
└──────────────────────────────────┘      │ auto-executor · auto-manager     │
                                          │ market-hours supervisor timer    │
                                          │ journal prices timer             │
                                          │ 1 GB RAM + 2 GB swap             │
                                          └──────────────────────────────────┘
```

- Advisor `.env`: `BRIDGE_URL=http://10.0.0.78:8002`,
  `IBKR_MARKET_DATA_URL=http://10.0.0.78:8003`, plus the matching
  `BRIDGE_ACCESS_TOKEN` / `IBKR_MARKET_DATA_TOKEN`.
- Executor/manager/supervisor units: `ADVISOR_URL=http://10.0.0.161:8000`.
- Bridge binds `0.0.0.0:8002` so the sibling Advisor can reach it.

## Network exposure (two layers, both enforced)

Subnet security list ingress:

| Port | Source | Purpose |
| --- | --- | --- |
| 22 | 0.0.0.0/0 | SSH (key only) |
| 80 | 0.0.0.0/0 | legacy, unused |
| 8000 | 0.0.0.0/0 | Advisor API (token-guarded; `/health/` public for the GitHub watchdog) |
| 8002 | 10.0.0.0/16 | Bridge -- VCN only, token-guarded |
| 8003 | 10.0.0.0/16 | read-only market-data proxy -- VCN only, own token |

Host iptables (Oracle Ubuntu images REJECT everything except 22 by default):
the Advisor VM accepts 8000; the executor VM accepts 8002/8003 from
10.0.0.0/16 only. Saved with `netfilter-persistent`.

## Rebuild runbook

1. Executor VM: `deployment/bridge_vm_setup.sh` with
   `BRIDGE_HOST=0.0.0.0 ADVISOR_URL=http://<advisor-private-ip>:8000`.
   **It replaces `/opt/ibc/config.ini` unless that file starts with its
   `# ThetaForge executor host` header** -- on a VM whose config already holds
   the paper credentials, upgrade in place instead (pull, `install` the files
   listed in the script's "redeploy" block, edit unit env).
   Clones must use the `github-thetaforge` SSH alias (deploy key) as origin.
2. Advisor VM: `deployment/provision_gcp.sh` (works on any 1 GB Ubuntu 24.04
   host) with `BRIDGE_TOKEN`, `BRIDGE_URL`, `IBKR_MARKET_DATA_TOKEN`; add the
   2 GB swap and the iptables 8000 rule first.
3. New/emptied Bridge ledger: run `scripts/sync_journal.py` against it (marks
   history `carried`), then `scripts/adopt_orphan_positions.py` (dry-run, then
   `--apply` with the Bridge stopped) so open IBKR positions are managed again,
   then `journal_sync_push.sh`.
4. Point `.github/workflows/keep-advisor-warm.yml` at the Advisor's public IP.

## Capacity caveat

Johannesburg Always Free capacity is oversubscribed: A1 (ARM) launches return
"Out of host capacity", and a *stopped* micro may not restart for days
(Oct 2026). Do not stop these VMs casually; reboot instead.
`deployment/oci_start_vm.py` retries a START until capacity frees up.
