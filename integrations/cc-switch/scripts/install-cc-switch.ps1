[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter()]
    [string]$PackageRoot = (Split-Path -Parent $PSScriptRoot),
    [Parameter()]
    [string]$DestinationRoot = (Join-Path $env:USERPROFILE '.cc-switch\skills')
)

$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'verify-cc-switch-package.ps1') -PackageRoot $PackageRoot
$manifest = Get-Content -LiteralPath (Join-Path $PackageRoot 'manifest.json') -Raw | ConvertFrom-Json
$skillNames = @($manifest.skills | ForEach-Object { [string]$_.name })
$referenceRelativePath = Join-Path 'references' 'orchestration-contract.md'
$referenceSource = Join-Path $PackageRoot $referenceRelativePath

if ($WhatIfPreference) {
    foreach ($name in $skillNames) {
        Write-Output "What if: install $name to $(Join-Path $DestinationRoot $name)"
    }
    Write-Output "What if: install KP orchestration contract to $(Join-Path (Split-Path -Parent $DestinationRoot) $referenceRelativePath)"
    return
}

New-Item -ItemType Directory -Path $DestinationRoot -Force | Out-Null
$parent = Split-Path -Parent $DestinationRoot
$backupRoot = Join-Path $parent 'skill-backups'
$referenceDestination = Join-Path $parent $referenceRelativePath
New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
$stageRoot = Join-Path $parent ('.kp-stage-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null

try {
    foreach ($name in $skillNames) {
        Copy-Item -LiteralPath (Join-Path $PackageRoot (Join-Path 'skills' $name)) -Destination (Join-Path $stageRoot $name) -Recurse -Force
    }
    $stagedReference = Join-Path $stageRoot $referenceRelativePath
    New-Item -ItemType Directory -Path (Split-Path -Parent $stagedReference) -Force | Out-Null
    Copy-Item -LiteralPath $referenceSource -Destination $stagedReference -Force
    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    foreach ($name in $skillNames) {
        $target = Join-Path $DestinationRoot $name
        $staged = Join-Path $stageRoot $name
        if ($PSCmdlet.ShouldProcess($target, "install KP skill $name")) {
            if (Test-Path -LiteralPath $target) {
                Move-Item -LiteralPath $target -Destination (Join-Path $backupRoot ("{0}_{1}" -f $timestamp, $name))
            }
            Move-Item -LiteralPath $staged -Destination $target
        }
    }
    if ($PSCmdlet.ShouldProcess($referenceDestination, 'install KP orchestration contract')) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $referenceDestination) -Force | Out-Null
        if (Test-Path -LiteralPath $referenceDestination) {
            Move-Item -LiteralPath $referenceDestination -Destination (Join-Path $backupRoot ("{0}_orchestration-contract.md" -f $timestamp))
        }
        Move-Item -LiteralPath $stagedReference -Destination $referenceDestination
    }
} finally {
    if (Test-Path -LiteralPath $stageRoot) {
        Remove-Item -LiteralPath $stageRoot -Recurse -Force
    }
}

Write-Output ("Installed KP skill package and orchestration contract to: $DestinationRoot. Refresh CC Switch skill sync before validation.")
