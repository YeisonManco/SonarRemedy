<#
.SYNOPSIS
    Run a SonarQube scan and emit ONLY a compact JSON state object.

.DESCRIPTION
    Minimal-token wrapper around sonar-scanner plus the SonarQube Web API.
    It validates the worktree, optionally pulls, runs npx sonar-scanner,
    queries the quality gate, measures, open issue count and unreviewed
    hotspot count, then prints ONE compressed JSON object with exactly
    nine keys. No tables, banners, CSVs or issue lists are emitted, so the
    orchestrator can read the whole result as one small object.

    The token is ALWAYS read from $env:SONAR_TOKEN. It is never accepted
    as a parameter and never written to a file. Run set-sonar-env.ps1 first.

.PARAMETER SonarUrl
    SonarQube base URL. Defaults to $env:SONAR_URL.

.PARAMETER ProjectKey
    SonarQube project key. Defaults to $env:SONAR_PROJECT_KEY.

.PARAMETER BranchName
    Branch to scan and query. Required (no default).

.PARAMETER WorktreePath
    Absolute path of the target worktree to scan. Required (no default).

.PARAMETER SkipPull
    Skip the git pull before scanning.

.EXAMPLE
    .\sonar_compact.ps1 -BranchName main -WorktreePath C:\work\repo

.EXAMPLE
    .\sonar_compact.ps1 -BranchName main -WorktreePath C:\work\repo -SkipPull

.NOTES
    Exit 0 means the scan and the API queries succeeded (a failing quality
    gate is data, not a script failure). Exit 2 means something blocked:
    missing worktree, missing token, scanner failure or API failure.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$SonarUrl = $env:SONAR_URL,

    [Parameter(Mandatory = $false)]
    [string]$ProjectKey = $env:SONAR_PROJECT_KEY,

    [Parameter(Mandatory = $false)]
    [string]$BranchName,

    [Parameter(Mandatory = $false)]
    [string]$WorktreePath,

    [Parameter(Mandatory = $false)]
    [switch]$SkipPull
)

$ErrorActionPreference = "Stop"

function Exit-Blocked {
    param([string]$Message)
    Write-Error $Message
    exit 2
}

# The token comes exclusively from the environment. It is never accepted
# as a parameter, never prompted, and never written to a file.
if ([string]::IsNullOrWhiteSpace($env:SONAR_TOKEN)) {
    Exit-Blocked "SONAR_TOKEN is not set or empty. Run set-sonar-env.ps1 first; the token is never passed as an argument."
}
if ([string]::IsNullOrWhiteSpace($SonarUrl)) {
    Exit-Blocked "SonarUrl is empty. Set SONAR_URL or pass -SonarUrl."
}
if ([string]::IsNullOrWhiteSpace($ProjectKey)) {
    Exit-Blocked "ProjectKey is empty. Set SONAR_PROJECT_KEY or pass -ProjectKey."
}
if ([string]::IsNullOrWhiteSpace($BranchName)) {
    Exit-Blocked "BranchName is required. Pass -BranchName."
}
if ([string]::IsNullOrWhiteSpace($WorktreePath)) {
    Exit-Blocked "WorktreePath is required. Pass -WorktreePath."
}
if (-not (Test-Path -LiteralPath $WorktreePath)) {
    Exit-Blocked "Worktree path does not exist: $WorktreePath"
}

function Get-BasicAuthHeader {
    param([string]$Token)
    $pair = "${Token}:"
    $bytes = [System.Text.Encoding]::ASCII.GetBytes($pair)
    $base64 = [System.Convert]::ToBase64String($bytes)
    return @{ Authorization = "Basic $base64" }
}

function Invoke-SonarApi {
    param([string]$Url)
    $headers = Get-BasicAuthHeader -Token $env:SONAR_TOKEN
    return Invoke-RestMethod -Uri $Url -Headers $headers -Method Get
}

# Optional sync before scanning. A failed pull means the tree may be stale;
# block instead of scanning an unknown revision (use -SkipPull to override).
if (-not $SkipPull) {
    Push-Location $WorktreePath
    try {
        git pull 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Exit-Blocked "git pull failed with exit code $LASTEXITCODE. Use -SkipPull to scan the current tree."
        }
    } catch {
        Exit-Blocked "git pull failed: $_"
    } finally {
        Pop-Location
    }
}

# Run the scanner from the worktree. Output is suppressed: the orchestrator
# must never read full scan output, only this script's compact JSON.
Push-Location $WorktreePath
try {
    npx --registry=https://registry.npmjs.org/ sonar-scanner `
        "-Dsonar.host.url=$SonarUrl" `
        "-Dsonar.token=$env:SONAR_TOKEN" `
        "-Dsonar.projectKey=$ProjectKey" `
        "-Dsonar.branch.name=$BranchName" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Exit-Blocked "sonar-scanner failed with exit code $LASTEXITCODE"
    }
} catch {
    Exit-Blocked "sonar-scanner failed: $_"
} finally {
    Pop-Location
}

# Short delay so the server finishes processing the analysis before queries.
Start-Sleep -Seconds 3

try {
    # Quality gate status. The gate value is data, not a script failure:
    # a failing gate means debt remains, so the script still exits 0.
    $qgUrl = "$SonarUrl/api/qualitygates/project_status?projectKey=$ProjectKey&branch=$BranchName"
    $qg = Invoke-SonarApi -Url $qgUrl
    $gate = [string]$qg.projectStatus.status

    # Core measures used by the pack acceptance criteria.
    $metrics = "bugs,vulnerabilities,code_smells,coverage,duplicated_lines_density,ncloc,reliability_rating,security_rating,sqale_rating"
    $measuresUrl = "$SonarUrl/api/measures/component?component=$ProjectKey&branch=$BranchName&metricKeys=$metrics"
    $measuresResponse = Invoke-SonarApi -Url $measuresUrl

    $measureMap = @{}
    foreach ($m in $measuresResponse.component.measures) {
        $measureMap[$m.metric] = $m.value
    }

    # Open issue count: resolved=false, page size 1 so only the total comes
    # back. The issue list itself is never fetched into context.
    $issuesUrl = "$SonarUrl/api/issues/search?componentKeys=$ProjectKey&branch=$BranchName&resolved=false&ps=1"
    $issuesResponse = Invoke-SonarApi -Url $issuesUrl
    $issuesOpen = [int]$issuesResponse.total

    # Unreviewed hotspot count: status TO_REVIEW, page size 1, count only.
    $hotspotsUrl = "$SonarUrl/api/hotspots/search?projectKey=$ProjectKey&branch=$BranchName&status=TO_REVIEW&ps=1"
    $hotspotsResponse = Invoke-SonarApi -Url $hotspotsUrl
    $unreviewedHotspots = [int]$hotspotsResponse.paging.total
} catch {
    Exit-Blocked "SonarQube API query failed: $_"
}

# Missing or non-numeric measures become JSON null, never a fabricated zero.
function Get-NumericMeasure {
    param([string]$Metric)
    if (-not $measureMap.ContainsKey($Metric)) { return $null }
    $raw = [string]$measureMap[$Metric]
    if ([string]::IsNullOrWhiteSpace($raw)) { return $null }
    $parsed = 0.0
    if ([double]::TryParse($raw, [ref]$parsed)) { return $parsed }
    return $null
}

# The single output: exactly these nine keys, nothing else.
$state = [PSCustomObject]@{
    gate                = $gate
    coverage            = Get-NumericMeasure -Metric "coverage"
    duplication         = Get-NumericMeasure -Metric "duplicated_lines_density"
    bugs                = Get-NumericMeasure -Metric "bugs"
    vulnerabilities     = Get-NumericMeasure -Metric "vulnerabilities"
    code_smells         = Get-NumericMeasure -Metric "code_smells"
    issues_open         = $issuesOpen
    unreviewed_hotspots = $unreviewedHotspots
    ncloc               = Get-NumericMeasure -Metric "ncloc"
}

$state | ConvertTo-Json -Compress
exit 0