# NetSuite Projects folder split-download package

This package extends the existing resumable File Cabinet downloader. NetSuite reports 2,405 total entries under folder `6225` (`Projects`); the script inventories the downloadable child-folder entries and downloads each child as a separate ZIP. It keeps the existing SQLite status, retry, partial-file resume, ZIP validation, disk-reserve, and authenticated-session architecture.

## Files

- `file_cabinet_downloader.py` — drop-in downloader, version `2026.08.01-project-hierarchy-v4.1`.
- `projects_commands.ps1` — safe PowerShell shortcuts. Its default action is status only.
- `requirements.txt` — Python packages.
- `README_PROJECTS.md` — this runbook.

The script creates these reports when commands run:

- `projects_folder_manifest.csv` — every inventoried child and its status.
- `projects_failed_downloads.csv` — failed Projects children only.
- `file_cabinet_state.sqlite3` — existing persistent state database, upgraded in place.

Downloads are written to:

`H:\file_cabinet_downloads\6225__Projects\<internal-id>__<folder-name>.zip`

## 1. Install and back up

Close any other crawler using the SQLite database or Chrome profile. In the `netsuite_crawler` folder, activate the existing virtual environment:

```powershell
.\venv\Scripts\Activate.ps1
```

Back up the working script and state database:

```powershell
Copy-Item .\file_cabinet_downloader.py .\file_cabinet_downloader.before-projects.py
Copy-Item .\file_cabinet_state.sqlite3 .\file_cabinet_state.before-projects.sqlite3
```

Copy all package files into `C:\Users\sheq1\Pictures\netsuite_crawler`. Replace the existing `file_cabinet_downloader.py` with the supplied one. Install or confirm dependencies:

```powershell
python -m pip install -r .\requirements.txt
python .\file_cabinet_downloader.py --version
```

Expected version:

```text
file_cabinet_downloader.py 2026.08.01-project-hierarchy-v4.1
```

## 2. Inventory Projects children (no downloads)

```powershell
.\projects_commands.ps1 -Action Expand
```

Chrome opens. Log in if NetSuite requests it, return to PowerShell, and press Enter. The downloader opens the main File Cabinet first and invokes NetSuite's `showFolderContents(6225)` UI function. It then scans all 49 Projects pages.

Expected result from the supplied page structure:

- 49 page checkpoints under folder ID `6225`.
- About 2,256 direct child folder rows in SQLite based on the observed run. NetSuite's 2,405 total also includes ordinary files on the final pages.
- `projects_folder_manifest.csv` contains those children.
- Parent `6225` becomes `split_ready`, so normal failed retries no longer try the 14.77 GiB parent ZIP.
- No ZIP is downloaded during `Expand`.

If scanning is interrupted, run the same command again. Completed Projects pages are skipped. Use `--rescan-all` only if the NetSuite folder listing changed and you intentionally want to revisit every page.

## 3. Confirm inventory

```powershell
.\projects_commands.ps1 -Action Status
```

Initially the Projects-only result should be similar to:

```json
{
  "pending": 2256
}
```

The exact count can change if NetSuite's contents change. Pages containing ordinary files but no downloadable folders are valid and are checkpointed with zero folder rows. The report is Projects-only because the command filters on `root_internal_id=6225`.

## 4. Ten-folder pilot

```powershell
.\projects_commands.ps1 -Action Pilot
```

Expected result:

- Up to 10 pending child folders are attempted with one worker.
- Valid ZIPs appear under `H:\file_cabinet_downloads\6225__Projects`.
- Successful rows become `complete`; failures become `failed` and remain retryable.
- `.zip.part` files are retained for safe HTTP Range resume where NetSuite supports it.

Then inspect:

```powershell
.\projects_commands.ps1 -Action Status
Get-ChildItem "H:\file_cabinet_downloads\6225__Projects" | Select-Object -First 10
```

## 5. Full Projects run

After the pilot ZIPs open successfully:

```powershell
.\projects_commands.ps1 -Action Full
```

This queues pending Projects children only. It does not re-download completed folders and does not include unrelated File Cabinet failures. Keep the external drive mounted at `H:` for the entire run.

## 6. Retry failures only

```powershell
.\projects_commands.ps1 -Action RetryFailed
```

This includes both pending and failed Projects children while still excluding completed ones. One worker is intentional for difficult or larger child ZIPs.

## Useful direct commands

Force a complete Projects re-inventory:

```powershell
python .\file_cabinet_downloader.py expand `
  --profile-dir "C:\Users\sheq1\Pictures\netsuite_crawler\selenium_profiles\netsuite_chrome" `
  --output-dir "H:\file_cabinet_downloads" `
  --folder-id 6225 `
  --folder-name Projects `
  --rescan-all `
  --page-timeout 90 `
  --manifest-csv .\projects_folder_manifest.csv
```

Retry at most five failed Projects children:

```powershell
python .\file_cabinet_downloader.py download `
  --profile-dir "C:\Users\sheq1\Pictures\netsuite_crawler\selenium_profiles\netsuite_chrome" `
  --output-dir "H:\file_cabinet_downloads" `
  --root-folder-id 6225 `
  --retry-failed `
  --max-downloads 5 `
  --workers 1 `
  --read-timeout 1800 `
  --folder-retries 3 `
  --retry-backoff 60 `
  --reserve-disk-gib 20 `
  --manifest-csv .\projects_folder_manifest.csv `
  --failed-csv .\projects_failed_downloads.csv
```

## Recovery and cautions

- Do not run two instances against the same SQLite database or persistent Chrome profile.
- Do not delete `.part` files unless you deliberately want a failed large child to restart from zero.
- The script checks free space on `H:` before each transfer and preserves 20 GiB in the supplied commands.
- `0 B` is NetSuite's displayed estimate. A folder can still contain nested folders; this version attempts its download normally and validates the returned ZIP.
- If login expires, authenticate again and rerun the same action. SQLite and partial downloads make the process resumable.
- To roll back the code and database migration, stop the crawler and restore both `.before-projects` backup files together.
