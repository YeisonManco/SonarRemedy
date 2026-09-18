<#
.SYNOPSIS
  Preview or run bounded manual or explicitly selected Claude profile batches.
.DESCRIPTION
  Dry-run is the default and launches no process or filesystem operation.
  -Execute opts in to the Python queue CLI; -Integrate additionally requests
  authorized target edits and exact configured serial checks. -ProviderProfile
  selects an explicit supported native profile. -Resume resumes queue state, never a model.
  No commits, pushes, scanner calls, installs, or authentication changes.
#>
[CmdletBinding()]
param(
    [ValidateSet('manual', 'opencode', 'codex', 'claude', 'copilot')]
    [string]$Provider = 'manual',
    [string]$State = '',
    [string]$Inbox = '',
    [string]$ProviderProfile = '',
    [ValidateRange(1, 8)][int]$Limit = 4,
    [ValidateRange(1, 1000)][int]$MaxBatches = 10,
    [ValidateRange(1, 86400)][int]$WallSeconds = 3600,
    [switch]$Resume,
    [switch]$Integrate,
    [switch]$Execute,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($Execute -and $DryRun) {
    @{ status = 'blocked'; reason = 'conflicting_execution_flags' } | ConvertTo-Json -Compress
    exit 2
}
if (-not $Execute) {
    @{ status = 'dry-run'; provider = $Provider; state = $State; limit = $Limit;
       max_batches = $MaxBatches; wall_seconds = $WallSeconds; integrate = [bool]$Integrate;
       notice = 'No files or processes. Claude native execution requires an explicitly selected bare API-key profile.'
    } | ConvertTo-Json -Compress
    exit 0
}
if (-not $State) {
    @{ status = 'blocked'; reason = 'queue_state_required' } | ConvertTo-Json -Compress
    exit 2
}
try {
    $entry = Join-Path $PSScriptRoot 'debt_work.py'
    $arguments = @('-B', $entry, '--state', $State, 'run', '--provider', $Provider,
                   '--limit', "$Limit", '--max-batches', "$MaxBatches", '--wall-seconds', "$WallSeconds", '--execute')
    if ($Resume) { $arguments += '--resume' }
    if ($Integrate) { $arguments += '--integrate' }
    if ($Inbox) { $arguments += @('--inbox', $Inbox) }
    if ($ProviderProfile) { $arguments += @('--profile', $ProviderProfile) }
    & python @arguments
    exit $LASTEXITCODE
}
catch {
    @{ status = 'unavailable'; reason = 'queue_cli_unavailable' } | ConvertTo-Json -Compress
    exit 2
}
