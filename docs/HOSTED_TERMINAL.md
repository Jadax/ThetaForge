# Hosted Private Terminal

The private terminal (`dashboard/app/page.tsx`) can run two ways:

- **Local** (`Start-ThetaForge.cmd`): only reachable from the trading
  computer at `http://localhost:3000`.
- **Hosted**: the same app, deployed as a static site to Cloudflare Pages and
  gated by Cloudflare Access, so it's reachable from any browser on any
  computer but nobody can load it without passing your login first.

Nothing about the app changes between the two. The Advisor token, Bridge
address/token, and every API call work identically either way.

## Why Cloudflare Access, not a password field in the app

The dashboard builds to a static export (`next.config.ts` already has
`output: "export"` — no server, no Next.js middleware support in that mode).
A login check written in the app's own JavaScript would not be real security:
the page's HTML/JS is still fully downloadable and inspectable by anyone who
requests the URL, login prompt or not — a static export has nothing to
enforce a check against. Cloudflare Access sits in front of the site at
Cloudflare's edge instead, so a request is authenticated *before* Cloudflare
Pages serves a single byte of the page. That's what actually makes "public
URL, but only I can open it" true rather than cosmetic.

This needs no application code: Access is a platform-level gate you configure
once in the Cloudflare dashboard, completely separate from
`dashboard/app/page.tsx`.

## One-time setup

These are Cloudflare account-level steps — sign-up, enabling Zero Trust, and
building the Access policy — done in Cloudflare's own dashboard, by you.

1. Create a free Cloudflare account at
   [dash.cloudflare.com](https://dash.cloudflare.com) if you don't have one.
2. Install Wrangler's login locally (from the repo root):
   ```powershell
   cd dashboard
   npx wrangler login
   ```
   This opens a browser OAuth flow you complete yourself.
3. Deploy the terminal for the first time:
   ```powershell
   deployment\cloudflare_deploy_terminal.ps1
   ```
   The first run creates the Cloudflare Pages project (default name
   `thetaforge-terminal`, so the URL is
   `https://thetaforge-terminal.pages.dev`) and uploads the static build.
   At this point the URL is **live and unprotected** — the next steps close
   that gap.
4. In the Cloudflare dashboard, open **Zero Trust** (left sidebar) and
   complete the one-time Zero Trust onboarding if prompted (just picks a team
   name, no cost on the free plan).
5. **Zero Trust → Access → Applications → Add an application → Self-hosted.**
   - Application domain: the Pages URL from step 3
     (`thetaforge-terminal.pages.dev`), path left as default so the whole
     site is covered, not just `/`.
   - Session duration: your choice (24 hours is a reasonable default for a
     personal single-user app).
6. Add a policy on that application: **Allow**, rule type **Emails**, and
   list only your own email address. This is the actual login gate — anyone
   without access to that inbox cannot get in, regardless of the URL.
   One-Time PIN (emailed code) is enabled by default and needs no further
   setup; you don't need a full SSO/identity provider for a single-user app.
7. Save. Open the Pages URL in a private/incognito window to confirm it now
   prompts for the email code before showing anything.

## Required: allow the new origin on the Advisor

The Advisor's CORS allowlist (`DASHBOARD_ORIGINS` on Render) only permits
requests from origins it knows about. Add the Pages URL there — in Render's
dashboard, edit the `DASHBOARD_ORIGINS` environment variable to include
`https://thetaforge-terminal.pages.dev` alongside the existing entries, comma
separated, then redeploy. Without this step, the hosted terminal loads fine
(Access only gates the page itself) but every Advisor API call fails with a
CORS error in the browser console.

## Redeploying after a code change

```powershell
deployment\cloudflare_deploy_terminal.ps1
```

Re-running only pushes the new static build; it never touches the Access
policy from steps 4-6 above; you don't need to redo those.

## Using it from another computer

Once logged in through Access, the hosted terminal works exactly like the
local one for anything that only talks to the Advisor (analysis, the
opportunity scan, notifications) — enter the same `ADVISOR_API_TOKEN` you use
locally, saved to that browser's `localStorage`.

Placing orders or viewing live IBKR positions additionally needs the Paper
Bridge, which is deliberately never hosted anywhere — it must run beside TWS
on your trading computer (`docs/PAPER_BRIDGE.md`). To reach it from another
computer, set up [Tailscale](https://tailscale.com) between the trading
computer and the other computer, then use the trading computer's Tailscale
address as the Bridge address in the terminal, same as the existing local
setup. If you haven't set that up yet, the hosted terminal still works fully
for analysis from anywhere — only order placement needs the Bridge reachable.
