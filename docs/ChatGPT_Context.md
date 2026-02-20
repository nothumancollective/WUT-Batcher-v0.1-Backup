# WUT Batcher Context (Current)

Canonical context for this repository.  
Last updated: 2026-02-20

## TL;DR
- Effective trunk is `wut-batcher/rebuild`.
- Runtime entry surface is `app/` (CLI + GUI) plus a small set of `scripts/` helpers.
- Primary flow is orchestration of ATH/AKABAK/VACS plus dataset and report pipelines.
- Legacy references to `Runner/`, `run_full_batch_v5.py`, and `start_gui.ps1` are archived in `docs/legacy/ChatGPT_Context_OLD.md`.

## Runtime Entry Points
- Module launcher: `app/__main__.py` -> `app.cli.main()`.
- CLI root: `app/cli.py` (`build_parser`, `main`).
- GUI direct entry: `app/gui.py` (`main`).
- UI preview utility: `ui/theme_preview.py` (`main`).
- Script entrypoints:
  - `scripts/vacs_export_save_all.py`
  - `scripts/vacs_export_dialog_rounds.py`
  - `scripts/vacs_interim_reimport.py`

## High-Value CLI Commands
- Health and environment:
  - `python -m app --help`
  - `python -m app doctor --report-path <path>`
- Fast sample orchestration:
  - `python -m app run-sample --dry-run --library-root <dir>`
  - `python -m app run-sample --real --library-root <dir>`
- Runner test harness:
  - `python -m app runner-test run --case smoke_fast --dry-run ...`
  - `python -m app runner-test run --case smoke_fast ... --ath-exe ... --akabak-exe ... --vacs-exe ...`
- ATH experiment surfaces:
  - `python -m app projectpage-ath-experiment ...`
  - `python -m app ath-experiments backfill-subkeys ...`
  - `python -m app ath-experiments split-unknown ...`
  - `python -m app ath-experiments refined-reports ...`
  - `python -m app ath-experiments analyze-compare-mismatch ...`
  - `python -m app ath-experiments contextual-ranges ...`
- Compatibility verification:
  - `python -m app compat verify --mode quick ...`
- UI automation helpers:
  - `python -m app ui inspect-akabak ...`
  - `python -m app ui inspect-vacs ...`
  - `python -m app vacs discover-graphs --dry-run ...`

## Core Runtime Modules
- CLI orchestration: `app/cli.py`
- Service/application layer: `app/services.py`
- Runtime pipeline orchestration: `app/runtime_orchestrator.py`
- Project/batch persistence: `app/project_storage.py`, `app/sql_dataset_store.py`
- Runner-test harness: `app/runner_test_harness.py`, `app/runner_test_db.py`
- ATH experiment pipeline: `app/projectpage_ath_experiment.py`

## Database Families
- Project/global DB (`projects`, `batches`, `versions`, `runs`, `graphs`, federation tables): `app/sql_dataset_store.py`
- Runner-test DB (`test_runs`, `test_cases`, `test_run_steps`, artifacts): `app/runner_test_db.py`
- ATH experiments DB (`experiment_runs`, params, metrics, compare): `app/projectpage_ath_experiment.py`

## Working Rules
- Keep external tool orchestration guarded by timeouts and explicit diagnostics.
- Treat UI automation paths as optional operational surfaces; avoid coupling them to basic CLI flows.
- Prefer bounded test execution tooling (`tools/audit/run_tests_bounded.py`) for local validation loops.
