param(
    [ValidateSet("Status", "Expand", "Pilot", "Full", "RetryFailed")]
    [string]$Action = "Status",
    [string]$ProfileDir = "C:\Users\sheq1\Pictures\netsuite_crawler\selenium_profiles\netsuite_chrome",
    [string]$OutputDir = "H:\file_cabinet_downloads",
    [string]$StateDb = "file_cabinet_state.sqlite3"
)

$ErrorActionPreference = "Stop"
$ScriptPath = Join-Path $PSScriptRoot "file_cabinet_downloader.py"
$ManifestPath = Join-Path $PSScriptRoot "projects_folder_manifest.csv"
$FailedPath = Join-Path $PSScriptRoot "projects_failed_downloads.csv"

if (-not (Test-Path $ScriptPath)) {
    throw "Downloader not found: $ScriptPath"
}

$reportArgs = @(
    "--state-db", $StateDb,
    "--manifest-csv", $ManifestPath,
    "--failed-csv", $FailedPath,
    "--root-folder-id", "6225"
)

$browserArgs = @(
    "--profile-dir", $ProfileDir,
    "--output-dir", $OutputDir,
    "--state-db", $StateDb,
    "--manifest-csv", $ManifestPath,
    "--failed-csv", $FailedPath
)

switch ($Action) {
    "Status" {
        & python $ScriptPath status @reportArgs
    }
    "Expand" {
        & python $ScriptPath expand @browserArgs `
            --folder-id 6225 `
            --folder-name Projects `
            --page-timeout 90
    }
    "Pilot" {
        & python $ScriptPath download @browserArgs `
            --root-folder-id 6225 `
            --workers 1 `
            --max-downloads 10 `
            --read-timeout 1800 `
            --connect-timeout 60 `
            --folder-retries 3 `
            --retry-backoff 60 `
            --in-flight-gib 1.5 `
            --large-threshold-gib 0.75 `
            --reserve-disk-gib 20
    }
    "Full" {
        & python $ScriptPath download @browserArgs `
            --root-folder-id 6225 `
            --workers 2 `
            --read-timeout 1800 `
            --connect-timeout 60 `
            --folder-retries 3 `
            --retry-backoff 60 `
            --in-flight-gib 1.5 `
            --large-threshold-gib 0.75 `
            --reserve-disk-gib 20
    }
    "RetryFailed" {
        & python $ScriptPath download @browserArgs `
            --root-folder-id 6225 `
            --retry-failed `
            --workers 1 `
            --read-timeout 1800 `
            --connect-timeout 60 `
            --folder-retries 3 `
            --retry-backoff 60 `
            --in-flight-gib 1.5 `
            --large-threshold-gib 0.75 `
            --reserve-disk-gib 20
    }
}

if ($LASTEXITCODE -ne 0) {
    throw "Downloader exited with code $LASTEXITCODE"
}
