# P1.3 Preview Tests (Bounded, No External Tools)

- branch: `fix/2026-02-21-preview-tests`
- mode: headless + isolated home
- external executables used: `none`

## Runtime Environment

- `QT_QPA_PLATFORM=offscreen`
- `HOME=cleanup/preview_runtime/home`
- `USERPROFILE=cleanup/preview_runtime/home`

## Bounded Preview Command

- command:
  - `python tools/audit/run_tests_bounded.py --pattern test_preview_pipeline.py --include preview --max-tests 20 --chunk-size 3 --timeout-s 120 --include-strict-timeout-s 30 --audit-dir cleanup/preview_runtime`
- exit_code: `0`

## Result

- discovered_total: `16`
- selected_total: `12`
- skipped_default_total: `4`
- subprocess_runs_total: `4`
- timeout_runs: `0`
- observed_failures_total: `0`
- observed_errors_total: `0`

## Failure Triage / Fixes

- no failing tests were observed in this bounded preview subset.
- no per-test isolation repro was required.
- no production or test code fix commits were required for failures.

## Artifacts (runtime, ignored)

- `cleanup/preview_runtime/tests_discovered.txt`
- `cleanup/preview_runtime/tests_summary.md`
- `cleanup/preview_runtime/flaky_or_hanging_tests.md`
- `cleanup/preview_runtime/data/bounded_runner_results.json`
- `cleanup/preview_runtime/data/bounded_chunk_logs/*.log`

## Repo Changes for This Task

- added ignore entry to keep preview runtime logs untracked:
  - `cleanup/.gitignore`
