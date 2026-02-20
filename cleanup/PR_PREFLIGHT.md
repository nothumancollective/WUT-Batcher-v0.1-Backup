# PR Preflight

## A1) Branch Confirmation
- Current branch: `cleanup/2026-02-20-p0`

## A2) Clean Working Tree Check
- `git status --porcelain` is empty.
- Note: local untracked artifact paths were excluded via `.git/info/exclude` for preflight cleanliness (not committed).

## A3) cleanup/runtime Tracking Check
- `git ls-files cleanup/runtime` is empty (no tracked files).

## A4) Largest Changed Tracked Files (Top 20)
```text
    159919  app/gui.py
     98750  scripts/vacs_export_save_all.py
     78580  app/cli.py
     77973  app/services.py
     38884  scripts/vacs_interim_reimport.py
     23725  scripts/vacs_export_dialog_rounds.py
     21304  app/audit_mode.py
     19694  tools/audit/run_tests_bounded.py
     11314  tests/test_service_export.py
     11126  app/runners.py
      8059  docs/legacy/ChatGPT_Context_OLD.md
      6692  cleanup/STATUS.md
      5475  tests/test_audit_mode.py
      3899  tests/test_cli_exit_codes.py
      3653  ui/theme_preview.py
      3209  tests/test_runners.py
      2959  docs/ChatGPT_Context.md
      1277  cleanup/PR_CHECKLIST.md
```
- Binary-like files from `git diff --numstat` (`-	-`):
  - none

## A5) Changed Files and Commits vs Base
### `git diff --name-status wut-batcher/rebuild..cleanup/2026-02-20-p0`
```text
A	app/audit_mode.py
M	app/cli.py
M	app/gui.py
M	app/runners.py
M	app/services.py
A	cleanup/PR_CHECKLIST.md
A	cleanup/STATUS.md
M	docs/ChatGPT_Context.md
A	docs/legacy/ChatGPT_Context_OLD.md
M	scripts/vacs_export_dialog_rounds.py
M	scripts/vacs_export_save_all.py
M	scripts/vacs_interim_reimport.py
A	tests/test_audit_mode.py
A	tests/test_cli_exit_codes.py
M	tests/test_runners.py
M	tests/test_service_export.py
A	tools/audit/run_tests_bounded.py
M	ui/theme_preview.py
```
### `git log --oneline wut-batcher/rebuild..cleanup/2026-02-20-p0`
```text
45db5c5 docs: add cleanup status log and PR checklist
7b92ade p0.4: return non-zero exit codes for fail doctor/run payloads
5c3f774 p0.1: enforce process-tree kill on external tool timeouts
07ebc6b p0.2: return structured batch validation state when project is missing
0d058c9 p0.3: quarantine legacy ChatGPT context and publish current runtime context
213a299 chore: add bounded test runner for cleanup validation
40756a8 chore: add opt-in audit_mode instrumentation wiring
```

## A6) P0 Commit + Validation Summary (from `cleanup/STATUS.md`)
- P0 commits:
  - `0d058c9` (P0.3 docs quarantine)
  - `07ebc6b` (P0.2 structured missing-project validation state)
  - `5c3f774` (P0.1 process-tree timeout kill watchdog)
  - `7b92ade` (P0.4 exit code semantics)
- Validation snapshots:
  - `0d058c9` (P0.3)
```text
## Validation After Commit `0d058c9` (P0.3)
- Timestamp: `2026-02-20T20:32:31.536593+01:00`
- Bounded tests command: `python tools/audit/run_tests_bounded.py`
- Bounded tests result: `exit=0`, discovered=`322`, selected=`176`, skipped=`146`, subprocess_runs=`18`, timeouts=`0`.
- Fast non-external scenarios:
  - `python -m app --help` -> `exit=0`, `duration_s=0.144`
  - `python -m app doctor --report-path cleanup/runtime/doctor_report_p03.json` -> `exit=0`, `duration_s=0.332`
  - `python -m app run-sample --dry-run --library-root cleanup/runtime/lib_p03` -> `exit=0`, `duration_s=0.438`
- Evidence files:
  - `cleanup/runtime/validation_p03.json`
  - `cleanup/runtime/help.stdout.txt`
  - `cleanup/runtime/doctor.stdout.txt`
  - `cleanup/runtime/run_sample_dry.stdout.txt`
```
  - `07ebc6b` (P0.2)
```text
## Validation After Commit `07ebc6b` (P0.2)
- Timestamp: `2026-02-20T20:35:51.172908+01:00`
- Bounded tests command: `python tools/audit/run_tests_bounded.py`
- Bounded tests result: `exit=0`, discovered=`323`, selected=`177`, skipped=`146`, subprocess_runs=`18`, timeouts=`0`.
- Fast non-external scenarios:
  - `python -m app --help` -> `exit=0`, `duration_s=0.138`
  - `python -m app doctor --report-path cleanup/runtime/doctor_report_p02.json` -> `exit=0`, `duration_s=0.311`
  - `python -m app run-sample --dry-run --library-root cleanup/runtime/lib_p02` -> `exit=0`, `duration_s=0.369`
- Evidence files:
  - `cleanup/runtime/validation_p02.json`
  - `cleanup/runtime/help_p02.stdout.txt`
  - `cleanup/runtime/doctor_p02.stdout.txt`
  - `cleanup/runtime/run_sample_dry_p02.stdout.txt`
```
  - `5c3f774` (P0.1)
```text
## Validation After Commit `5c3f774` (P0.1)
- Timestamp: `2026-02-20T20:38:19.752288+01:00`
- Bounded tests command: `python tools/audit/run_tests_bounded.py`
- Bounded tests result: `exit=0`, discovered=`324`, selected=`178`, skipped=`146`, subprocess_runs=`18`, timeouts=`0`.
- Fast non-external scenarios:
  - `python -m app --help` -> `exit=0`, `duration_s=0.147`
  - `python -m app doctor --report-path cleanup/runtime/doctor_report_p01.json` -> `exit=0`, `duration_s=0.365`
  - `python -m app run-sample --dry-run --library-root cleanup/runtime/lib_p01` -> `exit=0`, `duration_s=0.475`
- Evidence files:
  - `cleanup/runtime/validation_p01.json`
  - `cleanup/runtime/help_p01.stdout.txt`
  - `cleanup/runtime/doctor_p01.stdout.txt`
  - `cleanup/runtime/run_sample_dry_p01.stdout.txt`
```
  - `7b92ade` (P0.4)
```text
## Validation After Commit `7b92ade` (P0.4)
- Timestamp: `2026-02-20T20:41:00.682354+01:00`
- Bounded tests command: `python tools/audit/run_tests_bounded.py`
- Bounded tests result: `exit=0`, discovered=`328`, selected=`182`, skipped=`146`, subprocess_runs=`19`, timeouts=`0`.
- Fast non-external scenarios:
  - `python -m app --help` -> `exit=0`, `duration_s=0.140`
  - `python -m app doctor --report-path cleanup/runtime/doctor_report_p04.json` -> `exit=3`, `duration_s=0.301` (expected after exit-code semantics hardening when doctor reports fail)
  - `python -m app run-sample --dry-run --library-root cleanup/runtime/lib_p04` -> `exit=0`, `duration_s=0.365`
- Evidence files:
  - `cleanup/runtime/validation_p04.json`
  - `cleanup/runtime/help_p04.stdout.txt`
  - `cleanup/runtime/doctor_p04.stdout.txt`
  - `cleanup/runtime/run_sample_dry_p04.stdout.txt`
```
