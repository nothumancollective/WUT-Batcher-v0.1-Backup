# P2.2 Production Flow External Verification (Manual Single-Shot)

## Scope
- Command under test: `python -m app run pipeline` (`app/cli.py:160`, parser at `app/cli.py:934`).
- This is a manual operator procedure only. Do not automate loops/retries.
- External tools are expected (`ATH`, `AKABAK`, `VACS`), but this document does not execute them.

## Preconditions
- Branch: `wut-batcher/rebuild` (includes PR #7).
- You have one minimal `project.json` and `batch.json` pair prepared.
- Tool paths are valid:
  - `C:\Tools\ATH\ath.exe`
  - `C:\Program Files (x86)\RDTeam\AKABAK\AKABAK.exe`
  - `C:\Program Files (x86)\RDTeam\VACSVIEWER_32\VACSVIEWER_32.exe`

## Required Environment (isolated)
Set these for the run shell:

```powershell
$env:HOME = "C:\path\to\cleanup\runtime\p2_2\home"
$env:USERPROFILE = $env:HOME
$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"
```

## Single-Shot Command Template
Use exactly one execution with strict outer timeout.

```powershell
$projectsRoot = "C:\path\to\cleanup\runtime\p2_2\projects"
$projectJson = "C:\path\to\cleanup\runtime\p2_2\inputs\project.json"
$batchJson = "C:\path\to\cleanup\runtime\p2_2\inputs\batch.json"
$templateCfg = "runner_test_cases\templates\smoke_fast_min.cfg"

$stdout = "C:\path\to\cleanup\runtime\p2_2\run_pipeline.stdout.txt"
$stderr = "C:\path\to\cleanup\runtime\p2_2\run_pipeline.stderr.txt"

$argList = @(
  "-m", "app", "run", "pipeline",
  "--project-json", $projectJson,
  "--batch-json", $batchJson,
  "--projects-root", $projectsRoot,
  "--template-cfg", $templateCfg,
  "--ath-exe", "C:\Tools\ATH\ath.exe",
  "--akabak-exe", "C:\Program Files (x86)\RDTeam\AKABAK\AKABAK.exe",
  "--vacs-exe", "C:\Program Files (x86)\RDTeam\VACSVIEWER_32\VACSVIEWER_32.exe"
)

$start = Get-Date
$proc = Start-Process -FilePath "python" -ArgumentList $argList -PassThru -NoNewWindow -RedirectStandardOutput $stdout -RedirectStandardError $stderr

# Hard outer timeout (15 min). Single-shot only.
$finished = Wait-Process -Id $proc.Id -Timeout 900 -ErrorAction SilentlyContinue
if (-not $finished) {
  taskkill /PID $proc.Id /T /F | Out-Null
  $exitCode = 124
} else {
  $proc.Refresh()
  $exitCode = $proc.ExitCode
}
$end = Get-Date

"start=$start"
"end=$end"
"duration_s=$([int](($end - $start).TotalSeconds))"
"exit_code=$exitCode"
```

## Timeout Semantics to Expect
- Inner stage watchdogs are enforced in runners:
  - ATH default timeout: 180s (`app/runners.py:285`)
  - AKABAK default timeout: 600s (`app/runners.py:310`)
  - VACS default timeout: 600s (`app/runners.py:335`)
- Outer process timeout in this procedure: 900s with process-tree kill (`taskkill /T /F`).

## Success Criteria
All must be true:
1. Process exit code is `0` (CLI maps failed `run_status` to non-zero at `app/cli.py:175`).
2. CLI JSON summary contains `"run_status": "succeeded"`.
3. Persisted DB state confirms success in `project.sqlite`:
   - `runs.status = 'succeeded'` (`app/sql_dataset_store.py:207`, `app/sql_dataset_store.py:1810`)
   - all relevant `versions.status = 'success'` (`app/sql_dataset_store.py:159`, `app/sql_dataset_store.py:1618`)
   - all relevant `run_versions.status = 'success'` (`app/sql_dataset_store.py:222`, `app/sql_dataset_store.py:749`)

## Failure Criteria
Any of the following is a failed verification:
1. Process exit code is non-zero (`3` for failed run semantics, `124` for outer timeout kill).
2. CLI JSON summary has `"run_status": "failed"`.
3. Persisted status shows failed pipeline state in one or more of:
   - `runs.status = 'failed'`
   - `versions.status = 'failed'` (or stage-failed states like `ath_failed`, `akabak_failed`, `vacs_failed`)
   - `run_versions.status != 'success'`

## Post-Run DB Check Template
Use Python (no external sqlite CLI dependency):

```powershell
python - <<'PY'
import sqlite3
from pathlib import Path

db = Path(r"C:\path\to\cleanup\runtime\p2_2\projects\<PROJECT_ID>\dataset\project.sqlite")
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row

print("runs:")
for r in conn.execute("SELECT run_id, status, started_at, finished_at, error_summary FROM runs ORDER BY started_at DESC LIMIT 3"):
    print(dict(r))

print("versions:")
for r in conn.execute("SELECT version_id, status, finished_at FROM versions ORDER BY version_id"):
    print(dict(r))

print("run_versions:")
for r in conn.execute("SELECT run_id, version_id, status, error_summary FROM run_versions ORDER BY run_id DESC, version_id LIMIT 20"):
    print(dict(r))

conn.close()
PY
```

## Evidence to Record (single report block)
- exact command line
- start/end timestamps
- duration
- exit code
- whether outer timeout fired
- last 100 lines of stdout/stderr logs
- DB status snapshot (`runs`, `versions`, `run_versions`)
