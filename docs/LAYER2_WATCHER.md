# Layer-2 lock observer (P-46)

Build artifact only. No watcher, scheduled task, live landing or daemon restart was started by this change. Deploy only after Anh's separate approval.

Entry points:
- bin/watch_layer2.ps1: independent PowerShell process running the Python observer.
- bin/lock_holder_layer2.ps1: manual read-only capture using the same scanner.
- bin/layer2_watch.py: durable watermark/episode handling and out-of-process scanner.
- layer2_watch.json: paths resolve relative to this config file.
- bin/calibrate_layer2.py: reproducible log calibration; no invented threshold.

## Measurement

docs/LAYER2_CALIBRATION_20261102.json records input paths, captured byte lengths and SHA-256 hashes. Measured 2026-10-15 storm (19,183 matches) versus 2026-10-31 calm (44 matches in a full day). The original plan's Oct-13 log was not available; the verified Oct-15 incident archive is the explicit substitute.

Use trailing inclusive 30-second windows at matching lines, at whole-second timestamp resolution. Calm max = 11; storm median = 227 (max = 405). N = ceil((11 + 227) / 2) = **119**. The earlier discovery estimate used a different timestamp/window calculation; the shipped calibration and watcher use the same whole-second calculation. This is a measured initial setting, not a claim about all future workloads. Recalibrate on new representative days before changing N.

Example (from the checkout, supply archived/current log paths explicitly):
`python bin/calibrate_layer2.py --storm-day 2026-10-15 --calm-day 2026-10-31 --output calibration.json LOG1 LOG2 ...`

## Runtime behavior

- Poll interval 5 seconds; capture runs off the polling thread. Heartbeat every 60 seconds, including while a capture runs.
- First start skips existing bytes by default. Persisted watermark resumes; incomplete lines wait for their newline.
- A lock_hold> cue OR at least N contention/busy-budget lines in 30 seconds starts one capture per episode. Matching lines extend the episode; 60 seconds quiet rearms. Rotation is not a new episode.
- Watermark and capture intent are persisted atomically before capture starts. After a crash, an interrupted attempt is logged and not blindly replayed. Failed capture has a failed artifact and is not retried within the episode.
- One OS file lock per watermark prevents duplicate watchers. Corrupt state fails loud instead of replaying the log.
- Rotation uses file identity, size, prefix fingerprint and backward mtime. A renamed source is drained and archived. In-place truncate/rewrite preserves the observed prefix from a mirror. A first start at EOF cannot recover pre-start bytes already deleted. An unobserved truncate/regrow preserving prefix, length and identity cannot be proven from polling alone.
- Archive: .tmp/archive/layer2/. Own log/state: .tmp/layer2-watch/. At least 64 MiB remains free, plus copy size. Storage failure stops with a fatal log. No automatic deletion exists: evidence is retained indefinitely, hence at least 72 hours. Monitor disk space and arrange an explicitly approved cleanup.
- The observer and capture child do not depend on ToolA HTTP; this is necessary to observe a hung daemon. No source DB writes, kills, Defender changes or daemon calls.

## What a capture proves

The scanner uses psutil.open_files to list observed DB/WAL/SHM/journal and cache file holders, reads Defender exclusion paths, and lists sync/backup process names. It never records command lines or locals. Process scanning has a 12-second budget; Defender has 10 seconds; the parent enforces 30 seconds. Likely DB users are scanned first. Counts, denied processes and scan_truncated make partial visibility explicit. An empty list does NOT prove no external holder: psutil on Windows is best effort, not exhaustive kernel handle enumeration. Missing psutil, access denial and Defender timeout are unknown/error, not a clean result.

Use the project's venv312 Python with psutil. The interpreter is explicit rather than a fixed machine path. Before deployment verify configured log/DB paths match /api/config and environment overrides.

## Deployment review (not executed)

After approval, in the live checkout, the intended launch shape is:

```powershell
$observerRoot = (Get-Location).Path
$observerScript = Join-Path $observerRoot 'bin\watch_layer2.ps1'
$observerConfig = Join-Path $observerRoot 'layer2_watch.json'
$observerPython = Join-Path $observerRoot 'venv312\Scripts\python.exe'
$observerArgs = '-NoProfile -NonInteractive -File "{0}" -Config "{1}" -PythonExe "{2}"' -f $observerScript,$observerConfig,$observerPython
Start-Process -FilePath 'powershell.exe' -ArgumentList $observerArgs -WindowStyle Hidden
```

This creates an independent hidden PowerShell process. No task registration or automatic Windows reboot restart. Verify started/heartbeat records and matching state source after approved launch. A bounded rehearsal adds -MaxPolls and uses an isolated config/log copy, as in the build tests.
