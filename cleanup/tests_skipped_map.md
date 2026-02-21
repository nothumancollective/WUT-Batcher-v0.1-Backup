# Skipped Tests Map (P1)

- source: `audit/tests_discovered.txt`
- generated_utc: 2026-02-21T00:11:06.315936+00:00
- method: parsed `## Skipped By Default Filter` from bounded runner output.
- skipped_total: 146
- skipped_unique_modules: 30
- skipped_unique_directories: 1

## By Skip Token

| Token | Count | Unique Modules | Unique Directories |
|---|---:|---:|---:|
| ui | 128 | 28 | 1 |
| qt | 1 | 1 | 1 |
| gui | 0 | 0 | 0 |
| preview | 14 | 3 | 1 |
| stl | 1 | 1 | 1 |
| gmsh | 2 | 1 | 1 |

## Token Details

### `ui`

- skipped_count: 128
- run_safely_strategy: Use headless UI settings (`QT_QPA_PLATFORM=offscreen`), reduce chunk size, and prefer deterministic widget-contract tests first.
- directories:
  - `tests` (128)
- modules (by skipped count):
  - `tests/test_project_form_ui.py` (`test_project_form_ui`): 47
  - `tests/test_batch_page_ui.py` (`test_batch_page_ui`): 35
  - `tests/test_m2_compat_engine.py` (`test_m2_compat_engine`): 6
  - `tests/test_ui_automation_contracts.py` (`test_ui_automation_contracts`): 4
  - `tests/test_ui_risk_layer.py` (`test_ui_risk_layer`): 4
  - `tests/test_preview_pipeline.py` (`test_preview_pipeline`): 3
  - `tests/test_project_manager_ui.py` (`test_project_manager_ui`): 2
  - `tests/test_service_export.py` (`test_service_export`): 2
  - `tests/test_ui_automation_integration_optional.py` (`test_ui_automation_integration_optional`): 2
  - `tests/test_ui_validation_candidates.py` (`test_ui_validation_candidates`): 2
  - `tests/test_ui_validation_ranges.py` (`test_ui_validation_ranges`): 2
  - `tests/test_ui_waits.py` (`test_ui_waits`): 2
  - `tests/test_version_resolver.py` (`test_version_resolver`): 2
  - `tests/test_batch_validation_alignment_fuzz.py` (`test_batch_validation_alignment_fuzz`): 1
  - `tests/test_compare_policy.py` (`test_compare_policy`): 1
  - `tests/test_compat_rules.py` (`test_compat_rules`): 1
  - `tests/test_compat_schema.py` (`test_compat_schema`): 1
  - `tests/test_compatibility_service_batch_ui_separation.py` (`test_compatibility_service_batch_ui_separation`): 1
  - `tests/test_contextual_range_analysis.py` (`test_contextual_range_analysis`): 1
  - `tests/test_minimal_completion_search.py` (`test_minimal_completion_search`): 1
  - `tests/test_project_issue_model.py` (`test_project_issue_model`): 1
  - `tests/test_project_validation_alignment_fuzz.py` (`test_project_validation_alignment_fuzz`): 1
  - `tests/test_projectpage_ath_test.py` (`test_projectpage_ath_test`): 1
  - `tests/test_runner_test_db.py` (`test_runner_test_db`): 1
  - `tests/test_runner_test_harness.py` (`test_runner_test_harness`): 1
  - `tests/test_runtime_orchestrator.py` (`test_runtime_orchestrator`): 1
  - `tests/test_ui_e2e_stress_runs.py` (`test_ui_e2e_stress_runs`): 1
  - `tests/test_vacs_export_pipeline.py` (`test_vacs_export_pipeline`): 1

### `qt`

- skipped_count: 1
- run_safely_strategy: Gate with explicit Qt include mode and isolated temp HOME paths; run only bounded smoke subsets before wider coverage.
- directories:
  - `tests` (1)
- modules (by skipped count):
  - `tests/test_preview_pipeline.py` (`test_preview_pipeline`): 1

### `gui`

- skipped_count: 0
- run_safely_strategy: Prefer mocked event-loop and dialog-contract tests; avoid full interactive workflows by default.
- directories: none
- modules: none

### `preview`

- skipped_count: 14
- run_safely_strategy: Stub preview mesh/render boundaries and verify payload/policy normalization with fixtures only.
- directories:
  - `tests` (14)
- modules (by skipped count):
  - `tests/test_preview_pipeline.py` (`test_preview_pipeline`): 12
  - `tests/test_cli_runs_tools.py` (`test_cli_runs_tools`): 1
  - `tests/test_compatibility_service_batch_sweep_validation.py` (`test_compatibility_service_batch_sweep_validation`): 1

### `stl`

- skipped_count: 1
- run_safely_strategy: Use fixture STL metadata with mocked parser/loader boundaries; avoid real meshing backends.
- directories:
  - `tests` (1)
- modules (by skipped count):
  - `tests/test_service_export.py` (`test_service_export`): 1

### `gmsh`

- skipped_count: 2
- run_safely_strategy: Keep skipped by default; allow only explicit external opt-in runs with watchdog timeout and process-tree kill.
- directories:
  - `tests` (2)
- modules (by skipped count):
  - `tests/test_runner_test_harness.py` (`test_runner_test_harness`): 2

