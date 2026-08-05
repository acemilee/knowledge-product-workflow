[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter()]
    [string]$PackageRoot = (Split-Path -Parent $PSScriptRoot),
    [Parameter()]
    [string]$DestinationRoot = (Join-Path $env:USERPROFILE '.cc-switch\skills')
)

$ErrorActionPreference = 'Stop'
$manifest = Get-Content -LiteralPath (Join-Path $PackageRoot 'manifest.json') -Raw | ConvertFrom-Json
$parent = Split-Path -Parent $DestinationRoot
$backupRoot = Join-Path $parent 'skill-backups'
$referenceTarget = Join-Path $parent (Join-Path 'references' 'orchestration-contract.md')
New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'

foreach ($entry in @($manifest.skills)) {
    $name = [string]$entry.name
    $target = Join-Path $DestinationRoot $name
    if ((Test-Path -LiteralPath $target) -and $PSCmdlet.ShouldProcess($target, "remove KP skill $name by moving it to backup")) {
        Move-Item -LiteralPath $target -Destination (Join-Path $backupRoot ("{0}_uninstalled_{1}" -f $timestamp, $name))
    }
}

if ((Test-Path -LiteralPath $referenceTarget) -and $PSCmdlet.ShouldProcess($referenceTarget, 'remove KP orchestration contract by moving it to backup')) {
    Move-Item -LiteralPath $referenceTarget -Destination (Join-Path $backupRoot ("{0}_uninstalled_orchestration-contract.md" -f $timestamp))
}

Write-Output 'Removed KP Skill installation copies and orchestration contract; backups remain in the CC Switch skill-backups directory.'
