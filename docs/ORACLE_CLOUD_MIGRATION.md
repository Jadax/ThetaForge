# Oracle Cloud Migration Guide

Migrate the Advisor from Render (512 MB, 0.1 vCPU, exit 137) to an Oracle
Cloud Always Free ARM instance (24 GB RAM, 4 OCPUs, $0 forever).

## Architecture After Migration

```
ARM VM (new)                          AMD VM (92.4.132.188, unchanged)
┌──────────────────────┐              ┌──────────────────────────────────┐
│ nginx (:80)          │              │ IB Gateway                       │
│   └─ advisor (:8000) │◄─────────────│ Bridge (localhost:8002)           │
│     (Docker)         │  HTTP from   │ Executor  (ADVISOR_URL → ARM)    │
│                      │  AMD VM      │ Manager   (ADVISOR_URL → ARM)    │
│ 24GB RAM / 4 OCPU   │              │ Market Data (port 8003)          │
│ $0 forever           │              │ Supervisor (checks ARM /health)  │
└──────────────────────┘              └──────────────────────────────────┘
```

## Step 1: Create the ARM Instance (Manual, ~10 min)

1. Go to [cloud.oracle.com](https://cloud.oracle.com) → sign in
2. **Compute → Instances → Create Instance**
3. Settings:
   - **Name:** `thetaforge-advisor`
   - **Image:** Ubuntu 24.04 (or 22.04)
   - **Shape:** `VM.Standard.A1.Flex`
     - OCPUs: **4**
     - Memory: **24 GB**
   - **Networking:** select your existing VCN (the one with `92.4.132.188`)
   - **SSH Keys:** paste your public key (`~/.ssh/thetaforge_vm.pub`)
   - **Boot Volume:** 100 GB (plenty)
4. Click **Create** and wait 2-3 min for the instance to boot
5. Note the **Public IP** assigned to the new instance

If the ARM shape is "Out of Capacity", try a different availability domain
or region. Capacity is released frequently — retry in 10-15 min.

## Step 2: Open Port 80

1. **Networking → Virtual Cloud Networks →** click your VCN
2. **Security Lists →** click the default security list
3. **Add Ingress Rules:**
   - Source CIDR: `0.0.0.0/0`
   - Destination Port: `80`
   - Description: `HTTP for advisor API`

## Step 3: Run the Setup Script

From your Windows machine:

```powershell
scp -i ~/.ssh/thetaforge_vm deployment/oracle_arm_setup.sh ubuntu@<ARM_IP>:/tmp/
ssh -i ~/.ssh/thetaforge_vm ubuntu@<ARM_IP> 'chmod +x /tmp/oracle_arm_setup.sh && bash /tmp/oracle_arm_setup.sh'
```

Replace `<ARM_IP>` with the public IP from Step 1.

The script will:
- Install Docker, nginx
- Clone the repo to `/opt/thetaforge`
- Build and start the advisor in a Docker container
- Configure nginx as a reverse proxy on port 80
- Print the advisor URL when done

## Step 4: Verify the Advisor

```powershell
# Health check (should return {"status":"healthy"})
curl http://<ARM_IP>/health

# Version check
curl http://<ARM_IP>/openapi.json | python -c "import sys,json; print(json.load(sys.stdin)['info']['version'])"
```

## Step 5: Update the AMD VM Services

Three files on `92.4.132.188` need `ADVISOR_URL` changed from Render to the ARM VM. Run this from your Windows machine (replace `<ARM_IP>`):

```powershell
$ARM_IP = "<ARM_IP>"

# 1. Executor service
ssh -i ~/.ssh/thetaforge_vm ubuntu@92.4.132.188 "sudo sed -i 's|ADVISOR_URL=https://thetaforge-advisor.onrender.com|ADVISOR_URL=http://$ARM_IP|' /etc/systemd/system/thetaforge-auto-executor.service && sudo systemctl daemon-reload"

# 2. Manager service
ssh -i ~/.ssh/thetaforge_vm ubuntu@92.4.132.188 "sudo sed -i 's|ADVISOR_URL=https://thetaforge-advisor.onrender.com|ADVISOR_URL=http://$ARM_IP|' /etc/systemd/system/thetaforge-auto-manager.service && sudo systemctl daemon-reload"

# 3. Supervisor script
ssh -i ~/.ssh/thetaforge_vm ubuntu@92.4.132.188 "sudo sed -i 's|ADVISOR_URL=https://thetaforge-advisor.onrender.com|ADVISOR_URL=http://$ARM_IP|' /opt/thetaforge-bridge/market_hours_supervisor.sh 2>/dev/null || true"

# 4. Also update deployment/market_hours_supervisor.sh in the repo
```

Then restart the executor (the supervisor will restart it at next market open):

```powershell
ssh -i ~/.ssh/thetaforge_vm ubuntu@92.4.132.188 "sudo systemctl restart thetaforge-auto-executor.service thetaforge-auto-manager.service"
```

## Step 6: Verify End-to-End

1. Check the ARM VM advisor is serving the scanner status:
   ```powershell
   curl -H "X-ThetaForge-Advisor-Token: aqMnE8q5WFeGCevbHW1ru-zt7bguZyFCdrsyhBJ4ioQ" http://<ARM_IP>/api/advisor/scanner/status
   ```

2. Check the AMD VM executor is reaching the ARM VM:
   ```powershell
   ssh -i ~/.ssh/thetaforge_vm ubuntu@92.4.132.188 "journalctl -u thetaforge-auto-executor.service --no-pager -n 10"
   ```
   You should see `HTTP Request: GET http://<ARM_IP>/api/advisor/scanner/status "HTTP/1.1 200 OK"`

## Step 7: Render Cleanup

Once everything works:
- The Render free tier auto-deploys from GitHub, so it stays "on" but you can ignore it
- Or delete the Render service entirely — no charges either way

## Step 8: Update Dashboard (Optional)

The dashboard currently points to `https://thetaforge-advisor.onrender.com`.
If you want the terminal to use the ARM VM directly, update the
`ADVISOR_URL` in `dashboard/app/page.tsx`. If you set up a domain + Cloudflare
proxy in front of the ARM VM, use that URL instead.

## Rollback

If anything breaks, revert the three `ADVISOR_URL` lines on the AMD VM back to
`https://thetaforge-advisor.onrender.com`, `daemon-reload`, and restart the
executor. The Render instance is still running and serving the old version.
