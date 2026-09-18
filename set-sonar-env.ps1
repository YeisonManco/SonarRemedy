<#
.SYNOPSIS
    Set SONAR_URL, SONAR_PROJECT_KEY and SONAR_TOKEN for the current session.

.DESCRIPTION
    Prepares the environment the Sonar Debt agent expects. The token is
    never echoed: interactive input is masked, the summary shows only
    "[set]", and nothing is written to disk unless -Persist is used.

    Two input modes:
      (a) -SonarUrl with the COMPLETE URL including the project key; the
          project key is detected (query id= parameter first, then the last
          path segment) and -ProjectKey is not prompted. The stored SONAR_URL
          is normalized to the API base (query/fragment removed; a dashboard
          URL reduces to scheme://host:port).
      (b) -SonarUrl base URL + -ProjectKey separately.

    Token precedence: -Token parameter, then the masked interactive prompt,
    then the existing SONAR_TOKEN environment variable. If none is available,
    validation fails and asks you to provide it. Run this BEFORE invoking the
    agent so SONAR_URL, SONAR_PROJECT_KEY and SONAR_TOKEN exist in the
    environment.

.PARAMETER SonarUrl
    SonarQube URL: base URL, or complete URL including the project key.
    Defaults to an interactive prompt.

.PARAMETER ProjectKey
    SonarQube project key. Defaults to detection from the URL, then to an
    interactive prompt.

.PARAMETER Token
    SonarQube token. Never printed, never stored by default. Defaults to a
    masked interactive prompt.

.PARAMETER Persist
    Also persist the three variables at the user level so future sessions
    inherit them.

.EXAMPLE
    .\set-sonar-env.ps1

.EXAMPLE
    .\set-sonar-env.ps1 -SonarUrl https://sonar.example.com/dashboard?id=my-project

.EXAMPLE
    .\set-sonar-env.ps1 -SonarUrl https://sonar.example.com/my-project

.EXAMPLE
    .\set-sonar-env.ps1 -SonarUrl https://sonar.example.com -ProjectKey my-project -Persist

.NOTES
    Exit 0 means the environment was set. Exit 2 means validation failed
    (empty URL, embedded token in the URL, empty project key or empty token).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$SonarUrl,

    [Parameter(Mandatory = $false)]
    [string]$ProjectKey,

    [Parameter(Mandatory = $false)]
    [string]$Token,

    [Parameter(Mandatory = $false)]
    [switch]$Persist
)

$ErrorActionPreference = "Stop"

function Fail-Validation {
    param([string]$Message)
    Write-Error $Message
    exit 2
}

function Read-MaskedSecret {
    $secure = Read-Host -Prompt "SonarQube token" -AsSecureString
    if ($null -eq $secure -or $secure.Length -eq 0) { return "" }
    return [System.Net.NetworkCredential]::new("", $secure).Password
}

# 1. URL: parameter or interactive prompt.
if ([string]::IsNullOrWhiteSpace($SonarUrl)) {
    $SonarUrl = Read-Host -Prompt "SonarQube URL (base URL, or complete URL including the project key)"
}
$SonarUrl = ($SonarUrl.Trim()).TrimEnd("/")

if ([string]::IsNullOrWhiteSpace($SonarUrl)) {
    Fail-Validation "Sonar URL must not be empty."
}

$uri = [System.Uri]::new($SonarUrl, [System.UriKind]::Absolute)
if (-not $uri.IsAbsoluteUri -or ($uri.Scheme -ne "http" -and $uri.Scheme -ne "https")) {
    Fail-Validation "Sonar URL must be an absolute http:// or https:// URL."
}

# Reject URLs with an embedded token (sqa_ tokens or explicit token= params).
if ($SonarUrl -match "sqa_[A-Za-z0-9]+" -or $SonarUrl -match "[?&]token=") {
    Fail-Validation "The URL must not contain an embedded token. Supply the token separately."
}

# 2. Project key: parameter, detection, or interactive prompt.
$hadIdQuery = $false
if ([string]::IsNullOrWhiteSpace($ProjectKey)) {
    # Complete URL mode: prefer the id= query parameter (e.g. /dashboard?id=KEY),
    # otherwise derive from the last path segment (e.g. /my-project).
    if ($uri.Query -match "[?&]id=([^&]+)") {
        $hadIdQuery = $true
        $ProjectKey = [System.Uri]::UnescapeDataString($Matches[1])
    } else {
        $urlPath = $uri.AbsolutePath.Trim("/")
        if (-not [string]::IsNullOrWhiteSpace($urlPath)) {
            $ProjectKey = ($urlPath -split "/")[-1]
        }
    }
}
if ([string]::IsNullOrWhiteSpace($ProjectKey)) {
    $ProjectKey = Read-Host -Prompt "SonarQube project key"
}
$ProjectKey = $ProjectKey.Trim()

if ([string]::IsNullOrWhiteSpace($ProjectKey)) {
    Fail-Validation "Project key must not be empty."
}

# Normalize to the API base URL: Sonar endpoints reject queries/fragments.
# A dashboard URL (?id=KEY) reduces to scheme://host:port; other URLs keep
# their path (reverse-proxy subpaths) with query/fragment removed.
if ($uri.Query -or $uri.Fragment) {
    $SonarUrl = if ($hadIdQuery) {
        $uri.GetLeftPart([System.UriPartial]::Authority)
    } else {
        $uri.GetLeftPart([System.UriPartial]::Path).TrimEnd("/")
    }
    $uri = [System.Uri]::new($SonarUrl, [System.UriKind]::Absolute)
}

# 3. Token: parameter, environment fallback, or masked interactive prompt. Never echoed.
if ([string]::IsNullOrWhiteSpace($Token)) {
    $Token = Read-MaskedSecret
}
if ([string]::IsNullOrWhiteSpace($Token) -and -not [string]::IsNullOrWhiteSpace($env:SONAR_TOKEN)) {
    # Reuse the token already present in the environment (e.g. persisted by -Persist).
    $Token = $env:SONAR_TOKEN
}
if ([string]::IsNullOrWhiteSpace($Token)) {
    Fail-Validation "Token must not be empty. Set the SONAR_TOKEN environment variable or provide it at the prompt."
}

# 4. Set the current session.
$env:SONAR_URL = $SonarUrl
$env:SONAR_PROJECT_KEY = $ProjectKey
$env:SONAR_TOKEN = $Token

# 5. Optional user-level persistence via the OS user environment store.
#    The token is stored there like any Windows credential-backed setting;
#    it is never printed by this script.
if ($Persist) {
    [Environment]::SetEnvironmentVariable("SONAR_URL", $SonarUrl, "User")
    [Environment]::SetEnvironmentVariable("SONAR_PROJECT_KEY", $ProjectKey, "User")
    [Environment]::SetEnvironmentVariable("SONAR_TOKEN", $Token, "User")
}

Write-Host "SONAR_URL: $SonarUrl"
Write-Host "SONAR_PROJECT_KEY: $ProjectKey"
Write-Host "SONAR_TOKEN: [set]"
exit 0