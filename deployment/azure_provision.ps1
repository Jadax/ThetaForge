# Azure free-tier provisioning for the ThetaForge executor host.
# Creates everything in one resource group: NSG (SSH + market-data port),
# VNet/subnet, static Standard public IP, and a Standard_B1s Ubuntu 24.04
# VM (12-month free tier).
#
# Run from Windows PowerShell 5.1 AFTER `az login` (browser flow) shows a
# subscription:  powershell -File deployment\azure_provision.ps1
#
# B1s = 1 vCPU / 1 GiB RAM -- the same profile as the Oracle E2.1.Micro that
# ran this stack, and free for 750 hrs/month (a 31-day month is 744 hrs, so
# 24/7 fits inside the free allowance).
$ErrorActionPreference = 'Stop'

$Loc  = 'eastus'
$Rg   = 'thetaforge-rg'
$Vm   = 'thetaforge-bridge-vm'
$Nsg  = "$Vm-nsg"
$Vnet = "$Vm-vnet"
$Sub  = 'default'
$SshPriv = Join-Path $HOME '.ssh\thetaforge_vm'
$SshPub  = "$SshPriv.pub"

# Native commands (az/ssh-keygen) must not be judged by ErrorActionPreference:
# PowerShell 5.1 turns their stderr into terminating NativeCommandErrors under
# 'Stop'. All az calls go through here and are judged by exit code only.
# Note: plain [string[]] -- ValueFromRemainingArguments collapses an array
# literal argument into one space-joined string under PS 5.1.
function Invoke-Az {
    param([string[]]$CliArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & az @CliArgs | Out-Default
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
}
function Assert-Az {
    param([string[]]$CliArgs)
    $rc = Invoke-Az $CliArgs
    if ($rc -ne 0) { throw "az $($CliArgs -join ' ') failed ($rc)" }
}

# --- 1. Azure CLI session (free-signup done in the browser first) ---
if ((Invoke-Az @('account', 'show', '-o', 'none')) -ne 0) {
    Write-Host 'No Azure session yet. Complete https://azure.microsoft.com/free first,'
    Write-Host 'then run:  az login   (browser flow) and re-run this script.'
    exit 1
}
$prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
$subName = (& az account show --query name -o tsv) -join ''
$ErrorActionPreference = $prev
Write-Host "Azure subscription: $subName"

# --- 2. SSH key (key-only auth; password auth stays disabled) ---
if (-not (Test-Path $SshPriv)) {
    New-Item -ItemType Directory -Force -Path (Join-Path $HOME '.ssh') | Out-Null
    # cmd is used so the empty -N passphrase survives PowerShell 5.1's
    # empty-string argument mangling.
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    cmd /c "ssh-keygen -t ed25519 -f `"$SshPriv`" -N `"`" -C thetaforge-vm -q" | Out-Default
    $rc = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($rc -ne 0 -or -not (Test-Path $SshPriv)) { throw 'ssh-keygen failed' }
    Write-Host "generated $SshPriv"
}
if (-not (Test-Path $SshPub)) {
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    cmd /c "ssh-keygen -y -f `"$SshPriv`" > `"$SshPub`"" | Out-Default
    $rc = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($rc -ne 0 -or -not (Test-Path $SshPub)) { throw 'deriving public key failed' }
}
Write-Host "SSH public key: $SshPub"

# --- 3. Free-tier sanity: B1s capacity in the chosen region ---
$prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
$skusJson = (& az vm list-skus --location $Loc --size Standard_B1s -o json) -join "`n"
$rc = $LASTEXITCODE
$ErrorActionPreference = $prev
if ($rc -ne 0 -or [string]::IsNullOrWhiteSpace($skusJson)) {
    throw "could not verify Standard_B1s availability in $Loc"
}
$skus = $skusJson | ConvertFrom-Json
if (@($skus).Count -eq 0) {
    throw "Standard_B1s is not offered in $Loc -- rerun with another free-tier region before continuing."
}
Write-Host "Standard_B1s available in $Loc"

# --- 4. Resource group ---
Assert-Az @('group', 'create', '--name', $Rg, '--location', $Loc, '--output', 'none')

# --- 5. The VM (az vm create also creates the VNet/subnet/NSG/public IP;
#        VNet traffic stays allowed by NSG defaults, the rules below only
#        govern inbound Internet) ---
if ((Invoke-Az @('vm', 'show', '--resource-group', $Rg, '--name', $Vm, '-o', 'none')) -eq 0) {
    Write-Host "VM $Vm already exists -- skipping create."
} else {
    Assert-Az @('vm', 'create', '--resource-group', $Rg, '--name', $Vm,
                 '--location', $Loc,
                 '--image', 'Ubuntu2404', '--size', 'Standard_B1s',
                 '--admin-username', 'ubuntu', '--ssh-key-values', $SshPub,
                 '--vnet-name', $Vnet, '--subnet', $Sub,
                 '--address-prefixes', '10.0.0.0/16', '--subnet-prefixes', '10.0.1.0/24',
                 '--nsg', $Nsg, '--nsg-rule', 'SSH',
                 '--public-ip-sku', 'Standard',
                 '--os-disk-size-gb', '64', '--storage-sku', 'PremiumSSD_LRS',
                 '--output', 'none')
    Write-Host "created $Vm"
}

# --- 6. NSG: open the market-data proxy port (the only intentionally public
#        service -- own token, read-only endpoints, see
#        deployment/vm_market_data_service.py docstring) ---
if ((Invoke-Az @('network', 'nsg', 'rule', 'show', '--resource-group', $Rg,
                  '--nsg-name', $Nsg, '--name', 'AllowMarketData', '-o', 'none')) -ne 0) {
    Assert-Az @('network', 'nsg', 'rule', 'create', '--resource-group', $Rg, '--nsg-name', $Nsg,
                 '--name', 'AllowMarketData', '--priority', '110', '--direction', 'Inbound',
                 '--access', 'Allow', '--protocol', 'Tcp',
                 '--source-address-prefixes', '*', '--source-port-ranges', '*',
                 '--destination-address-prefixes', '*', '--destination-port-ranges', '8003',
                 '--output', 'none')
    Write-Host 'NSG rule AllowMarketData (8003) created'
}

$prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
$ip = ((& az vm show --resource-group $Rg --name $Vm --show-details --query publicIps -o tsv) -join '').Trim()
$ErrorActionPreference = $prev
Write-Host ''
Write-Host '=== Provisioned ==='
Write-Host "VM:    $Vm  ($Loc, Standard_B1s, Ubuntu 24.04)"
Write-Host "IP:    $ip"
Write-Host "SSH:   ssh -i `"$SshPriv`" ubuntu@$ip"
Write-Host ''
Write-Host 'Next (see deployment/azure_vm_setup.sh):'
Write-Host "  1. scp deployment/azure_vm_setup.sh ubuntu@${ip}:/tmp/"
Write-Host "  2. scp the repo deploy key (from the GCP VM) to ubuntu@${ip}:/tmp/tf_deploy_key"
Write-Host "  3. ssh in and run:"
Write-Host '       BRIDGE_TOKEN=3t0M...<full value from advisor .env> bash /tmp/azure_vm_setup.sh'
Write-Host '  4. edit /opt/ibc/config.ini with the IBKR paper credentials, then'
Write-Host '     sudo systemctl start ibgateway (smoke test), enable the supervisor timer.'
Write-Host "  5. point the GCP advisor at this VM (BRIDGE_URL=http://${ip}:8002,"
Write-Host '     IBKR_MARKET_DATA_URL/TOKEN) and restart thetaforge-advisor.'
