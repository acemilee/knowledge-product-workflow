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
$sharedResources = @($manifest.shared_resources)

if ($WhatIfPreference) {
    foreach ($name in $skillNames) {
        Write-Output "What if: install $name to $(Join-Path $DestinationRoot $name)"
    }
    foreach ($resource in $sharedResources) {
        $relativePath = [string]$resource.path
        Write-Output "What if: install KP shared resource $relativePath to $(Join-Path (Split-Path -Parent $DestinationRoot) $relativePath)"
    }
    return
}

New-Item -ItemType Directory -Path $DestinationRoot -Force | Out-Null
$parent = Split-Path -Parent $DestinationRoot
$backupRoot = Join-Path $parent 'skill-backups'
New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
$stageRoot = Join-Path $parent ('.kp-stage-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null

try {
    $stagedSkillsRoot = Join-Path $stageRoot 'skills'
    New-Item -ItemType Directory -Path $stagedSkillsRoot -Force | Out-Null
    foreach ($name in $skillNames) {
        Copy-Item -LiteralPath (Join-Path $PackageRoot (Join-Path 'skills' $name)) -Destination (Join-Path $stagedSkillsRoot $name) -Recurse -Force
    }
    Copy-Item -LiteralPath (Join-Path $PackageRoot 'manifest.json') -Destination (Join-Path $stageRoot 'manifest.json') -Force
    foreach ($resource in $sharedResources) {
        $relativePath = [string]$resource.path
        $source = Join-Path $PackageRoot $relativePath
        $staged = Join-Path $stageRoot $relativePath
        New-Item -ItemType Directory -Path (Split-Path -Parent $staged) -Force | Out-Null
        Copy-Item -LiteralPath $source -Destination $staged -Force
    }
    & (Join-Path $PSScriptRoot 'verify-cc-switch-package.ps1') -PackageRoot $stageRoot
    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    foreach ($name in $skillNames) {
        $target = Join-Path $DestinationRoot $name
        $staged = Join-Path $stagedSkillsRoot $name
        if ($PSCmdlet.ShouldProcess($target, "install KP skill $name")) {
            if (Test-Path -LiteralPath $target) {
                Move-Item -LiteralPath $target -Destination (Join-Path $backupRoot ("{0}_{1}" -f $timestamp, $name))
            }
            Move-Item -LiteralPath $staged -Destination $target
        }
    }
    foreach ($resource in $sharedResources) {
        $relativePath = [string]$resource.path
        $destination = Join-Path $parent $relativePath
        $staged = Join-Path $stageRoot $relativePath
        if ($PSCmdlet.ShouldProcess($destination, "install KP shared resource $relativePath")) {
            New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
            if (Test-Path -LiteralPath $destination) {
                $backupName = "{0}_{1}" -f $timestamp, ([System.IO.Path]::GetFileName($relativePath))
                Move-Item -LiteralPath $destination -Destination (Join-Path $backupRoot $backupName)
            }
            Move-Item -LiteralPath $staged -Destination $destination
        }
    }
} finally {
    if (Test-Path -LiteralPath $stageRoot) {
        Remove-Item -LiteralPath $stageRoot -Recurse -Force
    }
}

Write-Output ("Installed KP skill package and orchestration contract to: $DestinationRoot. Refresh CC Switch skill sync before validation.")
