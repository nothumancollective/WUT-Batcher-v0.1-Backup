# P2 External Verification: S07 (gmsh hang)

- verification_date_utc: `2026-02-21`
- branch: `wut-batcher/rebuild`
- goal: single-shot manual verification that timeout + process-tree kill prevents indefinite external hang in S07 workflow.

## 1) Trunk and Merge Check

- checkout/pull completed on `wut-batcher/rebuild`
- latest merge confirmed:
  - `e44e813` (`Merge pull request #6 from nothumancollective/fix/2026-02-21-preview-tests`)

## 2) Minimal S07 Reproduction Setup

- existing prior-scenario artifact used as reference config:
  - `audit/runtime/ath_cfg/ProjectPageATHTest1.cfg`
- copied to:
  - `cleanup/runtime/p2_s07_manual/repro_cfg/ProjectPageATHTest1.cfg`
- runtime roots prepared:
  - `cleanup/runtime/p2_s07_manual/ath_cfg`
  - `cleanup/runtime/p2_s07_manual/ath_exports`
  - `cleanup/runtime/p2_s07_manual/reports`

## 3) Single S07 Run (One Command Execution)

- command line:
  - `python -m app projectpage-ath-experiment --cases 1 --seed 20260220 --run-group p2_s07_manual_20260221 --reports-root C:\Users\maximilianheinze\Desktop\WUT Batcher v0.1\cleanup\runtime\p2_s07_manual\reports --cfg-dir C:\Users\maximilianheinze\Desktop\WUT Batcher v0.1\cleanup\runtime\p2_s07_manual\ath_cfg --export-root C:\Users\maximilianheinze\Desktop\WUT Batcher v0.1\cleanup\runtime\p2_s07_manual\ath_exports --cleanup-files false --preclean-files true --cleanup-cases never --cleanup-log never --history-snapshots false --ath-exe C:\Tools\ATH\ath.exe --template-cfg runner_test_cases/templates/smoke_fast_min.cfg`
- started_utc: `2026-02-21T01:25:51.0144955Z`
- ended_utc: `2026-02-21T01:31:52.0823119Z`
- duration_s: `361.068`
- top-level command exit code: `0`

Note:
- the command was executed once manually (single-shot).
- internal ATH runner retries are built-in (`AthRunner` defaults) and produced two timeout attempts in this single run execution.

## 4) Timeout / Kill-Tree / Exit Evidence

- timeout occurred: `yes`
  - evidence:
    - `cleanup/runtime/p2_s07_manual/reports/cases/run_0001/runner_logs/ath.runner.log`
    - contains:
      - `[2026-02-21T01:25:51+00:00] attempt=1 timeout after 180s`
      - `[2026-02-21T01:28:51+00:00] attempt=2 timeout after 180s`
  - persisted run payload also reports timeout:
    - `cleanup/runtime/p2_s07_manual/reports/summary.json`
    - `reports_preview[0].ath_result.timed_out = true`

- kill-tree executed: `yes (inferred from timeout path + process state)`
  - timeout handler path used by ATH runner:
    - `app/runners.py:227` (communicate timeout)
    - `app/runners.py:229` (`_terminate_process_tree(proc.pid)`)
    - `app/runners.py:264` (`taskkill /PID <pid> /T /F`)
  - process check before/after run (captured):
    - `before_gmsh: []`, `after_gmsh: []`
    - `before_ath: []`, `after_ath: []`
    - source: `cleanup/runtime/p2_s07_manual/manual_run_capture.json`

- resulting run failure recorded with non-zero run-level code: `yes`
  - persisted DB row:
    - `status = ath_error`
    - `ath_exit_code = -1`
    - `ath_error_message` contains timeout marker
    - source: `cleanup/runtime/p2_s07_manual/reports/ath_experiments.sqlite` (`experiment_runs`)
  - summary mirror:
    - `cleanup/runtime/p2_s07_manual/reports/summary.json`
    - `status_counts.ath_error = 1`
    - `reports_preview[0].ath_result.exit_code = -1`

## 5) Persisted Status Snapshot

- DB:
  - `cleanup/runtime/p2_s07_manual/reports/ath_experiments.sqlite`
  - latest run_group row:
    - `run_group_id: p2_s07_manual_20260221`
    - `status: ath_error`
    - `ath_exit_code: -1`
    - `ath_error_kind: ath_runtime_unknown`
    - `error_pattern_refined: ath_runtime_unknown`
- report files:
  - `cleanup/runtime/p2_s07_manual/reports/summary.json`
  - `cleanup/runtime/p2_s07_manual/reports/cases/run_0001/report.json`
  - `cleanup/runtime/p2_s07_manual/reports/log/run_0001_stdout.txt`
  - `cleanup/runtime/p2_s07_manual/reports/log/run_0001_stderr.txt`

## 6) Next Steps

1. Align CLI exit semantics for `projectpage-ath-experiment` so timeout-driven `ath_error` returns non-zero at process level (currently top-level command returned `0` while run-level exit was `-1`).
2. Optionally add an explicit CLI option to control ATH retry count (`--ath-retries`) for stricter single-attempt operational verification runs.
