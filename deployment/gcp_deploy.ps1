<#
.SYNOPSIS
Deploys the ThetaForge Advisor to Cloud Run and wires up the periodic scan
trigger, entirely on Google Cloud's Always Free tier.

.DESCRIPTION
Run this from the repository root after `gcloud auth login` and after
creating a GCP project with billing enabled (required by Cloud Run even for
free-tier usage; nothing here should incur a charge if usage stays within the
Always Free quotas documented in docs/HANDOVER.md -> Deployment Requirements).

This script never embeds ADVISOR_API_TOKEN in a file. If the Secret Manager
secret doesn't exist yet, it prompts for the value once (masked input) and
stores it in Secret Manager; every later run reuses the stored secret. The
Cloud Scheduler job reads that same secret at setup time to build its request
header — the value passes through gcloud on this machine only, never through
any file this script writes or any conversation transcript.

Safe to re-run: every step below is idempotent (enabling an already-enabled
API, or updating an existing Cloud Run service / Scheduler job, is a no-op or
a plain update, not a duplicate).

.PARAMETER ProjectId
Your GCP project ID (required). Create one at https://console.cloud.google.com
if you don't have one yet — that step is account-level and must be done by you.

.PARAMETER Region
Defaults to us-central1, a low-latency, broadly available region.

.PARAMETER Schedule
Cron expression for the periodic scan trigger. Defaults to every 20 minutes.
This number is not arbitrary — see the comment above SCAN_CONCURRENCY in
agents/trade_engine/background_scanner.py for the measured timing it's based
on. Going much tighter risks exceeding the free vCPU-second quota; see
docs/HANDOVER.md -> Deployment Requirements for the math.
#>
param(
    [Parameter(Mandatory = $true)][string]$ProjectId,
    [string]$Region = "us-central1",
    [string]$ServiceName = "thetaforge-advisor",
    [string]$SecretName = "thetaforge-advisor-token",
    [string]$SchedulerJobName = "thetaforge-scan-trigger",
    [string]$Schedule = "*/20 * * * *",
    [string]$DashboardOrigins = "https://jadax.github.io,http://localhost:3000,http://127.0.0.1:3000"
)

$ErrorActionPreference = "Stop"

function Read-PlainSecret([string]$Prompt) {
    $secure = Read-Host -Prompt $Prompt -AsSecureString
    $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    } finally {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

Write-Host "== Configuring project: $ProjectId =="
gcloud config set project $ProjectId | Out-Null

Write-Host "== Enabling required APIs (no-op if already enabled) =="
$apis = @(
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "cloudscheduler.googleapis.com"
)
foreach ($api in $apis) {
    gcloud services enable $api | Out-Null
}

Write-Host "== Checking for existing secret '$SecretName' =="
$secretExists = $true
try {
    gcloud secrets describe $SecretName --format "value(name)" 2>$null | Out-Null
} catch {
    $secretExists = $false
}

if (-not $secretExists) {
    Write-Host "No existing secret found. This becomes ADVISOR_API_TOKEN on the Advisor;"
    Write-Host "every /api/advisor/* route requires it, and the dashboard needs the same"
    Write-Host "value entered once per browser session."
    $tokenValue = Read-PlainSecret "Paste a long random token (input hidden)"
    if ([string]::IsNullOrWhiteSpace($tokenValue)) {
        throw "A non-empty token is required."
    }
    $tokenValue | gcloud secrets create $SecretName --data-file=- | Out-Null
    $tokenValue = $null
    Write-Host "Secret '$SecretName' created."
} else {
    Write-Host "Secret '$SecretName' already exists; reusing it."
}

Write-Host "== Granting the Cloud Run runtime service account access to the secret =="
$projectNumber = gcloud projects describe $ProjectId --format "value(projectNumber)"
$runtimeServiceAccount = "$projectNumber-compute@developer.gserviceaccount.com"
gcloud secrets add-iam-policy-binding $SecretName `
    --member "serviceAccount:$runtimeServiceAccount" `
    --role "roles/secretmanager.secretAccessor" | Out-Null

Write-Host "== Deploying $ServiceName to Cloud Run (builds the existing Dockerfile) =="
# min-instances=0 keeps this on the Always Free tier: the container is billed
# only while it's actually handling a request. max-instances=1 avoids two
# instances racing to write the same local data/*.json state.
gcloud run deploy $ServiceName `
    --source . `
    --region $Region `
    --allow-unauthenticated `
    --min-instances 0 `
    --max-instances 1 `
    --set-env-vars "DASHBOARD_ORIGINS=$DashboardOrigins" `
    --set-secrets "ADVISOR_API_TOKEN=${SecretName}:latest"

$serviceUrl = gcloud run services describe $ServiceName --region $Region --format "value(status.url)"
Write-Host "Deployed: $serviceUrl"

Write-Host "== Setting up the periodic scan trigger (Cloud Scheduler) =="
$tokenForHeader = gcloud secrets versions access latest --secret $SecretName
$targetUri = "$serviceUrl/api/advisor/scanner/trigger"
$headerArg = "X-ThetaForge-Advisor-Token=$tokenForHeader"

$jobExists = $true
try {
    gcloud scheduler jobs describe $SchedulerJobName --location $Region --format "value(name)" 2>$null | Out-Null
} catch {
    $jobExists = $false
}

if ($jobExists) {
    gcloud scheduler jobs update http $SchedulerJobName `
        --location $Region `
        --schedule $Schedule `
        --uri $targetUri `
        --http-method POST `
        --headers $headerArg | Out-Null
    Write-Host "Updated existing Scheduler job '$SchedulerJobName'."
} else {
    gcloud scheduler jobs create http $SchedulerJobName `
        --location $Region `
        --schedule $Schedule `
        --uri $targetUri `
        --http-method POST `
        --headers $headerArg | Out-Null
    Write-Host "Created Scheduler job '$SchedulerJobName'."
}
$tokenForHeader = $null

Write-Host ""
Write-Host "== Done =="
Write-Host "Advisor URL: $serviceUrl"
Write-Host "Scan trigger: $targetUri every ($Schedule)"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. If this URL differs from https://thetaforge-advisor.onrender.com-style"
Write-Host "     expectations, update DEFAULT_ADVISOR_API in dashboard/app/page.tsx."
Write-Host "  2. Paste the same token into the dashboard's 'Advisor API address and"
Write-Host "     token' panel."
Write-Host "  3. Watch Cloud Run's Metrics tab for a few days and compare actual"
Write-Host "     vCPU-second usage against the free 180,000/month quota before"
Write-Host "     tightening -Schedule below 20 minutes."
