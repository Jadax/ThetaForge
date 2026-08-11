<#
.SYNOPSIS
Builds the private terminal as a static export and deploys it to Cloudflare
Pages, so it's reachable from any browser without needing to run
Start-ThetaForge.cmd locally.

.DESCRIPTION
This deploys the SAME dashboard/app/page.tsx used locally - no app code
changes, no separate build. The only thing that makes a public URL safe here
is Cloudflare Access sitting in front of it at Cloudflare's edge, which this
script does not configure (Access policies are account-level and must be set
up once in the Cloudflare Zero Trust dashboard by you - see
docs/HOSTED_TERMINAL.md for the exact steps). Re-running this script only
redeploys the static files; it never touches your Access policy.

Once loaded, the terminal itself still needs the Advisor token entered once
(same as the local version) before it can do anything - Access controls who
can reach the page at all, the app-level token controls what a logged-in
session can do against the Advisor API.

Run `wrangler login` once before this (opens a browser OAuth flow you
complete yourself - not something this script or I can do on your behalf).

.PARAMETER ProjectName
The Cloudflare Pages project name. Determines the default URL:
https://<ProjectName>.pages.dev. Must be unique to your Cloudflare account,
not globally - no need to check availability elsewhere first.
#>
param(
    [string]$ProjectName = "thetaforge-terminal"
)

$ErrorActionPreference = "Stop"
$dashboardRoot = Join-Path $PSScriptRoot ".." "dashboard"

Push-Location $dashboardRoot
try {
    Write-Host "== Building the static export (npm run build) =="
    # No GITHUB_PAGES env var here: next.config.ts only applies the
    # /ThetaForge basePath when that's set, and Cloudflare Pages serves this
    # project from its own domain root, not a subpath.
    npm run build

    if (-not (Test-Path "out/index.html")) {
        throw "Build did not produce out/index.html - check the build output above."
    }

    Write-Host "== Deploying ./out to Cloudflare Pages project '$ProjectName' =="
    npx wrangler pages deploy out --project-name $ProjectName
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "== Done =="
Write-Host "If this is the first deploy, wrangler just created the Pages project."
Write-Host "Next steps (one-time, in the Cloudflare dashboard - see docs/HOSTED_TERMINAL.md):"
Write-Host "  1. Set up a Cloudflare Access application in front of this project"
Write-Host "     so only you can load the page at all."
Write-Host "  2. Add https://$ProjectName.pages.dev to DASHBOARD_ORIGINS on the"
Write-Host "     Render Advisor's environment variables (Advisor CORS otherwise"
Write-Host "     blocks calls from this new origin)."
Write-Host "  3. Open the URL, pass the Access login, then enter your Advisor"
Write-Host "     token in the terminal as usual."
