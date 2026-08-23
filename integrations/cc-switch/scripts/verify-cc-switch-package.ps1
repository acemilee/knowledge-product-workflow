[CmdletBinding()]
param(
    [Parameter()]
    [string]$PackageRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
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
    $stream = [System.IO.File]::OpenRead($resourcePath)
    try {
        $actualHash = ([System.BitConverter]::ToString($sha256.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
    } finally {
        $stream.Dispose()
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
    if ([regex]::Matches($content, '(?m)^##\s+').Count -lt 6) {
        throw "Skill contract is incomplete: $skillPath"
    }
    foreach ($match in [regex]::Matches($content, '`(?<relative>(?:\.\./)+references/[^`]+)`')) {
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
