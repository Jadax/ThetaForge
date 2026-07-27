# Remote Paper Bridge

The IBKR Bridge is deliberately **not** a Railway service. It must run on the
computer where TWS or IB Gateway is running, because it connects to that local
IBKR session.

For personal access from other computers, use a private network such as
Tailscale. Install it on the trading computer and each computer you use, then
access the Bridge through the trading computer's private Tailscale address. Do
not expose port 8002 directly to the public internet.

## Website-first setup (recommended)

The dashboard can be opened as a normal website, but a browser cannot launch a
local IBKR API connection by itself. On the trading computer, double-click
`Install-ThetaForge-Autostart.cmd` once. It registers a hidden task for the
current Windows user that starts the paper-only Bridge automatically at sign-in.

After that, open the ThetaForge dashboard website, sign in to Paper TWS when
needed, and use **Connect paper Bridge**. No terminal window is required.

## Start the Bridge

Set a long, random `BRIDGE_ACCESS_TOKEN` in the local `.env`, log into IBKR
**Paper Trading**, then run:

```powershell
uvicorn bridge.main:app --env-file .env --host 0.0.0.0 --port 8002
```

Keep the Bridge in paper mode. It has no live-order route.

If you use **IB Gateway**, leave `IBKR_PAPER_PORT=4002`. If you use the full
**Trader Workstation**, set `IBKR_PAPER_PORT=7497` instead. In TWS, open
`File/Edit → Global Configuration → API → Settings`, enable socket clients,
keep localhost-only access enabled, and clear read-only mode only when you are
ready to submit paper orders.

## Verify locally

Open `http://127.0.0.1:8002/`. A JSON service message is the expected result;
the dashboard is the user interface. `http://127.0.0.1:8002/health` reports
whether it is connected to TWS/IB Gateway.

## Security

- Keep TWS/IB Gateway and the Bridge on a private network.
- Set `BRIDGE_ACCESS_TOKEN` before allowing remote access.
- Never forward port 8002 on your router.
- Every order is staged and requires explicit `confirm_paper_order=true`.
