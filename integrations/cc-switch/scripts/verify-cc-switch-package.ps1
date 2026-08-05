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
$orchestrationContract = Join-Path $PackageRoot 'references\orchestration-contract.md'
if (-not (Test-Path -LiteralPath $orchestrationContract -PathType Leaf)) {
    throw "Missing orchestration contract: $orchestrationContract"
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
}

$rootSkill = Get-Content -LiteralPath (Join-Path $PackageRoot 'skills\kp-create\SKILL.md') -Raw
foreach ($phrase in $requiredRootPhrases) {
    if ($rootSkill -notmatch [regex]::Escape($phrase)) {
        throw "Root skill is missing a fixed constraint: $phrase"
    }
}

Write-Output ("CC Switch skill package passed: {0} skills, version {1}" -f @($manifest.skills).Count, $manifest.version)
