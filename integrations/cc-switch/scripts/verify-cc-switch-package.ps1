[CmdletBinding()]
param(
    [Parameter()]
    [string]$PackageRoot = ''
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($PackageRoot)) {
    $PackageRoot = Split-Path -Parent $PSScriptRoot
}
$manifestPath = Join-Path $PackageRoot 'manifest.json'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Missing package manifest: $manifestPath"
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.package -ne 'knowledge-product-workflow' -or -not $manifest.skills) {
    throw 'Invalid skill package manifest.'
}
$sharedResources = @($manifest.shared_resources)
if ($sharedResources.Count -eq 0) {
    throw 'Package manifest must declare at least one shared resource.'
}
foreach ($resource in $sharedResources) {
    $relativePath = [string]$resource.path
    $expectedHash = [string]$resource.sha256
    if ([string]::IsNullOrWhiteSpace($relativePath) -or $relativePath -match '(^|[\\/])\.\.([\\/]|$)') {
        throw "Invalid shared resource path: $relativePath"
    }
    if ($expectedHash -notmatch '^[0-9a-f]{64}$') {
        throw "Invalid shared resource hash: $relativePath"
    }
    $resourcePath = Join-Path $PackageRoot $relativePath
    if (-not (Test-Path -LiteralPath $resourcePath -PathType Leaf)) {
        throw "Missing shared resource: $relativePath"
    }
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        # Hash canonical UTF-8/LF bytes so the manifest is stable across Windows and Unix checkouts.
        if ([System.IO.Path]::GetExtension($resourcePath) -eq '.png') {
            $bytes = [System.IO.File]::ReadAllBytes($resourcePath)
        } else {
            $content = [System.IO.File]::ReadAllText($resourcePath, [System.Text.Encoding]::UTF8)
            $canonical = $content.Replace("`r`n", "`n").Replace("`r", "`n")
            $bytes = [System.Text.Encoding]::UTF8.GetBytes($canonical)
        }
        $actualHash = ([System.BitConverter]::ToString($sha256.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha256.Dispose()
    }
    if ($actualHash -ne $expectedHash.ToLowerInvariant()) {
        throw "Shared resource hash mismatch: $relativePath"
    }
}

$requiredRootPhrases = @('kp init', 'kp brief', 'kp render-pdf', 'network_timeout')
foreach ($skill in @($manifest.skills)) {
    $name = [string]$skill.name
    if ($name -notmatch '^kp-[a-z0-9-]+$') {
        throw "Invalid skill name: $name"
    }
    $skillPath = Join-Path $PackageRoot (Join-Path 'skills' (Join-Path $name 'SKILL.md'))
    if (-not (Test-Path -LiteralPath $skillPath -PathType Leaf)) {
        throw "Missing skill file: $skillPath"
    }
    $content = Get-Content -LiteralPath $skillPath -Raw
    if ($content -notmatch "(?m)^name:\s*$([regex]::Escape($name))\s*$") {
        throw "Skill frontmatter name mismatch: $skillPath"
    }
    if ($content -notmatch ('(?m)^\s+version:\s*"' + [regex]::Escape([string]$manifest.version) + '"\s*$')) {
        throw "Skill version mismatch: $name"
    }
    if (@($skill.resources).Count -eq 0) {
        throw "Skill must include independent resources: $name"
    }
    foreach ($resource in @($skill.resources)) {
        $relativePath = [string]$resource.path
        if ($relativePath -match '(^|[\\/])\.\.([\\/]|$)' -or [System.IO.Path]::IsPathRooted($relativePath)) {
            throw "Invalid Skill resource path: $name -> $relativePath"
        }
        $resourcePath = Join-Path (Split-Path -Parent $skillPath) $relativePath
        if (-not (Test-Path -LiteralPath $resourcePath -PathType Leaf)) {
            throw "Missing Skill resource: $name -> $relativePath"
        }
        if ([System.IO.Path]::GetExtension($resourcePath) -eq '.png') {
            $contentBytes = [System.IO.File]::ReadAllBytes($resourcePath)
        } else {
            $contentBytes = [System.Text.Encoding]::UTF8.GetBytes(([System.IO.File]::ReadAllText($resourcePath, [System.Text.Encoding]::UTF8)).Replace("`r`n", "`n").Replace("`r", "`n"))
        }
        $hasher = [System.Security.Cryptography.SHA256]::Create()
        try {
            $resourceHash = ([System.BitConverter]::ToString($hasher.ComputeHash($contentBytes))).Replace('-', '').ToLowerInvariant()
        } finally {
            $hasher.Dispose()
        }
        $declaredResource = @($manifest.shared_resources | Where-Object { $_.path -eq $relativePath })
        if ($declaredResource.Count -ne 1 -or $resourceHash -ne [string]$resource.sha256 -or $resourceHash -ne [string]$declaredResource[0].sha256) {
            throw "Skill resource hash mismatch: $name -> $relativePath"
        }
    }
    if ([regex]::Matches($content, '(?m)^##\s+').Count -lt 6) {
        throw "Skill contract is incomplete: $skillPath"
    }
    foreach ($match in [regex]::Matches($content, '`(?<relative>(?:\.\./)*references/[^`]+)`')) {
        $relativeReference = $match.Groups['relative'].Value
        $referencePath = [System.IO.Path]::GetFullPath((Join-Path (Split-Path -Parent $skillPath) $relativeReference))
        if (-not (Test-Path -LiteralPath $referencePath -PathType Leaf)) {
            throw "Missing Skill relative reference: $name -> $relativeReference"
        }
    }
}

$rootSkill = Get-Content -LiteralPath (Join-Path $PackageRoot 'skills\kp-create\SKILL.md') -Raw
foreach ($phrase in $requiredRootPhrases) {
    if ($rootSkill -notmatch [regex]::Escape($phrase)) {
        throw "Root skill is missing a fixed constraint: $phrase"
    }
}

Write-Output ("CC Switch skill package passed: {0} skills, version {1}" -f @($manifest.skills).Count, $manifest.version)
