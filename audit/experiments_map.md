# Experiments Map

## Evidence Base
- CLI wiring: `app/cli.py:335`, `app/cli.py:371`, `app/cli.py:390`, `app/cli.py:425`, `app/cli.py:447`, `app/cli.py:459`, `app/cli.py:472`, `app/cli.py:502`.
- Runtime traces:
  - `audit/run_traces/S07_projectpage_ath_experiment_min_retry/*.coverage.json`
  - `audit/run_traces/S08_ath_experiments_admin/*.coverage.json`
  - `audit/run_traces/S09_compat_verify_quick/*.coverage.json`
- External timeout behavior evidence: `cleanup/P2_EXTERNAL_VERIFICATION_S07.md`.

## Experiment/Legacy Module Inventory
| Module | Primary role | Runtime evidence |
| --- | --- | --- |
| `app/projectpage_ath_experiment.py` | Mixed: large-run data collection + DB-backed analysis/reporting | Hot in `S07` and `S08` traces (`module_call_counts` in both scenarios). |
| `app/projectpage_ath_test.py` | Data-collection harness for project-page -> ATH consistency | Hot in `S07` traces. |
| `app/contextual_range_analysis.py` | Read-only analysis over `ath_experiments.sqlite` | Hit in `S08` (`contextual-ranges`). |
| `app/minimal_completion_search.py` | Mixed: DB seed analysis + optional ATH oracle execution | Wired via `ath-experiments minimal-completion-search`; classified experimental. |
| `app/compat_verification.py` | Verification harness (can execute ATH per case) | Hit in `S09` traces. |

## Valuable Analysis vs Data Collection Instrumentation

### Valuable analysis (keep, isolate as analysis surface)
- `app/projectpage_ath_experiment.py:1937` (`_compute_range_suggestions`) and `app/projectpage_ath_experiment.py:2342` (`_reports_from_db`) are DB/query/report transforms.
- `app/projectpage_ath_experiment.py:2657` (`run_ath_experiments_refined_reports`) and `app/projectpage_ath_experiment.py:2823` (`run_ath_experiments_analyze_compare_mismatch`) are report-generation/admin analytics.
- `app/contextual_range_analysis.py:308` (`run_contextual_range_analysis`) is read-only range synthesis over SQLite.
- `app/minimal_completion_search.py:1360` (`run_minimal_completion_search`) in `verify_with_ath=False` mode is DB-driven search/analysis.

### Data collection / external-exec instrumentation (gate harder)
- `app/projectpage_ath_experiment.py:3562` (`run_projectpage_ath_experiment`) executes ATH runs, persists rows, and manages cleanup.
- `app/projectpage_ath_experiment.py:1134` (`_ensure_db_schema`) + `app/projectpage_ath_experiment.py:1313` (`_persist_experiment_row`) are storage plumbing tied to collection path.
- `app/projectpage_ath_test.py:806` (`run_projectpage_ath_test_suite`) executes runner path with external tools.
- `app/compat_verification.py:256` (`run_compat_verification`) can invoke ATH with timeout/gmsh path.
- `app/minimal_completion_search.py` `_AthOracle` path (see oracle setup around cache/runner sections) invokes ATH/gmsh wrapper logic when verification is enabled.

## Recommended Destination and Gating
- Destination split:
  - Move read-only/report analysis to `tools/experiments/analysis/` (or `app/experiments/analysis`) with zero external-process side effects.
  - Keep collectors/runners under `tools/experiments/collectors/` (or `app/experiments/collectors`) and mark as non-shipping.
- CLI gating:
  - Introduce explicit `experimental` command group (or require `--experimental` flag) for:
    - `projectpage-ath-test`
    - `projectpage-ath-experiment`
    - `ath-experiments *`
    - `compat verify` when real external execution is enabled.
- Safety defaults:
  - Preserve current watchdog/kill-tree paths for all experimental external runners (`app/runners.py:199`, `app/runners.py:280`).
  - Keep default execution on bounded/timeboxed mode and make external retries explicit via flags.

## Practical Quarantine Plan (no moves in this branch)
- Phase 1: document these commands as experimental-only in user docs and CLI help text sections.
- Phase 2: isolate analysis-only codepaths first (`contextual_range_analysis`, DB report helpers).
- Phase 3: move collectors after analysis split is stable; retain compatibility wrappers for one release cycle.
