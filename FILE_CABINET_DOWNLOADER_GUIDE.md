# NetSuite File Cabinet Downloader

This workflow inventories the File Cabinet through the authenticated NetSuite UI,
then downloads the exact folder links found in that UI through bounded,
authenticated streaming requests.

## Why it does not estimate completion with sleeps

The displayed folder size is useful for scheduling but not for deciding whether
a transfer is complete. A downloaded folder is a ZIP archive, so its transferred
size can differ from the uncompressed size displayed by NetSuite.

A folder is marked complete only when:

1. The HTTP response finishes.
2. `Content-Length` matches when NetSuite supplies it.
3. The ZIP central directory can be read.
4. The temporary `.part` file is atomically renamed to its final name.

Interrupted downloads remain `pending` or `failed` in SQLite and are safe to
retry.

## Recommended first run

Use the same Python virtual environment as the existing crawler:

```powershell
pip install -r requirements-file-cabinet.txt
python file_cabinet_downloader.py scan --profile-dir "C:\NetSuiteChromeProfile"
```

Review:

- `file_cabinet_manifest.csv`
- `file_cabinet_state.sqlite3`
- The reported count, which should be 4,432 folders.

Then run a conservative ten-folder download test:

```powershell
python file_cabinet_downloader.py download `
  --profile-dir "C:\NetSuiteChromeProfile" `
  --workers 1 `
  --max-downloads 10 `
  --in-flight-gib 1.5 `
  --large-threshold-gib 0.75 `
  --reserve-disk-gib 10
```

The persistent Chrome profile can be omitted when the existing `crawler.py`
module exposes its normal `is_logged_in(driver)` and `login(driver)` functions.

## Production recommendation

Start with:

- `--workers 2`
- `--in-flight-gib 1.5`
- `--large-threshold-gib 0.75`
- `--reserve-disk-gib 10`
- `--verify structure`

This permits two ordinary transfers when their combined displayed sizes fit
within approximately 1.5 GiB. A folder of 0.75 GiB or more runs alone.

Do not begin at six workers. Increase to three only after at least 100 folders
complete without NetSuite throttling, login expiry, increased server latency, or
local disk pressure.

## Commands

Inventory without downloading:

```powershell
python file_cabinet_downloader.py scan
```

Inventory and then download:

```powershell
python file_cabinet_downloader.py all
```

Resume pending folders:

```powershell
python file_cabinet_downloader.py download
```

Retry folders marked failed:

```powershell
python file_cabinet_downloader.py download --retry-failed
```

Show status and refresh the CSV manifest:

```powershell
python file_cabinet_downloader.py status
```

Perform full ZIP CRC verification:

```powershell
python file_cabinet_downloader.py download --verify crc
```

CRC verification reads all decompressed members and therefore increases local
CPU and disk work. The default structural validation is more appropriate during
the main 93 GB transfer. CRC can be run for high-value folders or during a later
off-hours pass.

## Output

Downloaded folders are named:

```text
<internal-id>__<sanitized-folder-name>.zip
```

The internal ID prevents collisions when NetSuite contains duplicate folder
names.

The SQLite state database records:

- Internal ID and folder name
- Displayed size
- Page index and download URL
- Status and attempt count
- Final path and actual downloaded bytes
- SHA-256 hash
- Last error

## Handling authentication expiry

If NetSuite redirects a download to a login page or returns HTML instead of a
ZIP, the run stops initiating useful work and records the affected transfer as
failed. Log in again and resume with:

```powershell
python file_cabinet_downloader.py download --retry-failed
```

The volatile `siaT`, `siaWhc`, and `siaNv` values are intentionally excluded.
After authentication, the stable File Cabinet route is:

```text
/app/common/media/mediaitemfolders.nl?sc=-63
```

## Existing crawler integration

The downloader automatically tries to import `crawler.py` and reuse:

```python
crawler.is_logged_in(driver)
crawler.login(driver)
```

If your function names differ, change only `ensure_authenticated()` in
`file_cabinet_downloader.py`; the scanning, checkpoint, scheduling, and download
logic can remain unchanged.

To expose this through the existing `main.py`, import the module and call its
`main()` through a new `file-cabinet` command. Keep the downloader in the same
project directory as `crawler.py` so the existing login module is importable.

## Safe pilot checklist

1. Run `scan`.
2. Confirm the manifest reports 4,432 unique IDs.
3. Confirm the displayed total size is close to 93 GB.
4. Back up the SQLite state file.
5. Run ten to twenty downloads with `--workers 1 --max-downloads 10`.
6. Open several ZIPs manually.
7. Resume with `--workers 2`.
8. Check `python file_cabinet_downloader.py status` periodically.
9. Do not delete `.part` files while the process is running.
10. Preserve the SQLite database until the entire migration is verified.
