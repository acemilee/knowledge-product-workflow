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
$replacements = New-Object 'System.Collections.Generic.List[object]'

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
    $timestamp = (Get-Date -Format 'yyyyMMdd_HHmmss') + '_' + [guid]::NewGuid().ToString('N').Substring(0, 8)
    foreach ($name in $skillNames) {
        $target = Join-Path $DestinationRoot $name
        $staged = Join-Path $stagedSkillsRoot $name
        if ($PSCmdlet.ShouldProcess($target, "install KP skill $name")) {
            $backup = $null
            if (Test-Path -LiteralPath $target) {
                $backup = Join-Path $backupRoot ("{0}_{1}" -f $timestamp, $name)
                Move-Item -LiteralPath $target -Destination $backup
            }
            $replacements.Add(@{Target = $target; Backup = $backup})
            Move-Item -LiteralPath $staged -Destination $target
        }
    }
    foreach ($resource in $sharedResources) {
        $relativePath = [string]$resource.path
        $destination = Join-Path $parent $relativePath
        $staged = Join-Path $stageRoot $relativePath
        if ($PSCmdlet.ShouldProcess($destination, "install KP shared resource $relativePath")) {
            New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
            $backup = $null
            if (Test-Path -LiteralPath $destination) {
                $backupName = "{0}_{1}" -f $timestamp, ([System.IO.Path]::GetFileName($relativePath))
                $backup = Join-Path $backupRoot $backupName
                Move-Item -LiteralPath $destination -Destination $backup
            }
            $replacements.Add(@{Target = $destination; Backup = $backup})
            Move-Item -LiteralPath $staged -Destination $destination
        }
    }
    foreach ($name in $skillNames) {
        foreach ($resource in @(($manifest.skills | Where-Object { $_.name -eq $name }).resources)) {
            $installed = Join-Path (Join-Path $DestinationRoot $name) ([string]$resource.path)
            if (-not (Test-Path -LiteralPath $installed -PathType Leaf)) {
                throw "Installed Skill resource missing: $name"
            }
            if ([System.IO.Path]::GetExtension($installed) -eq '.png') {
                $resourceBytes = [System.IO.File]::ReadAllBytes($installed)
            } else {
                $canonical = ([System.IO.File]::ReadAllText($installed, [System.Text.Encoding]::UTF8)).Replace("`r`n", "`n").Replace("`r", "`n")
                $resourceBytes = [System.Text.Encoding]::UTF8.GetBytes($canonical)
            }
            $hasher = [System.Security.Cryptography.SHA256]::Create()
            try {
                $actualHash = ([System.BitConverter]::ToString($hasher.ComputeHash($resourceBytes))).Replace('-', '').ToLowerInvariant()
            } finally { $hasher.Dispose() }
            if ($actualHash -ne [string]$resource.sha256) { throw "Installed Skill resource hash mismatch: $name" }
        }
    }
} catch {
    $failure = $_
    for ($index = $replacements.Count - 1; $index -ge 0; $index--) {
        $entry = $replacements[$index]
        $resolvedTarget = [System.IO.Path]::GetFullPath([string]$entry.Target)
        $resolvedParent = [System.IO.Path]::GetFullPath($parent).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
        if (-not $resolvedTarget.StartsWith($resolvedParent, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Rollback target escaped installation directory: $resolvedTarget"
        }
        if (Test-Path -LiteralPath $resolvedTarget) { Remove-Item -LiteralPath $resolvedTarget -Recurse -Force }
        if ($entry.Backup -and (Test-Path -LiteralPath $entry.Backup)) { Move-Item -LiteralPath $entry.Backup -Destination $resolvedTarget }
    }
    throw $failure
} finally {
    if (Test-Path -LiteralPath $stageRoot) {
        $resolvedStage = [System.IO.Path]::GetFullPath($stageRoot)
        $resolvedParent = [System.IO.Path]::GetFullPath($parent).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
        if (-not $resolvedStage.StartsWith($resolvedParent, [System.StringComparison]::OrdinalIgnoreCase) -or [System.IO.Path]::GetFileName($resolvedStage) -notlike '.kp-stage-*') {
            throw "Unsafe staging cleanup path: $resolvedStage"
        }
        Remove-Item -LiteralPath $resolvedStage -Recurse -Force
    }
}

Write-Output ("Installed KP skill package and orchestration contract to: $DestinationRoot. Refresh CC Switch skill sync before validation.")
