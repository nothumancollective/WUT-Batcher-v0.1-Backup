# Production Surface Map

## Scope and Evidence
- Entry roots scanned: `app/__main__.py:3`, `app/cli.py:878`, `app/cli.py:1844`, `app/gui.py:3734`, `app/gui.py:3759`.
- Runtime evidence: `audit/data/scenario_runs_raw.json`, `audit/run_traces/S01_cli_help/*.coverage.json`, `audit/run_traces/S03_run_sample_dry/*.coverage.json`, `audit/run_traces/S04_run_sample_real/*.coverage.json`, `audit/run_traces/S05_runner_test_dry/*.coverage.json`, `audit/run_traces/S06_runner_test_real/*.coverage.json`, `audit/run_traces/S07_projectpage_ath_experiment_min_retry/*.coverage.json`, `audit/run_traces/S08_ath_experiments_admin/*.coverage.json`, `audit/run_traces/S09_compat_verify_quick/*.coverage.json`, `audit/run_traces/S10_ui_and_script_entrypoints/*.coverage.json`.
- Static command extraction method: `python` introspection of `app.cli.build_parser()` (command -> handler map).

## CLI Commands and Handlers
- `doctor` -> `cmd_doctor` (`app/cli.py:77`) -> `app.doctor_service.run_doctor_checks` -> `PRODUCTION`
- `batch job-count` -> `cmd_batch_job_count` (`app/cli.py:97`) -> `app.batch_planner.compute_job_count` -> `TOOLING`
- `dataset build` -> parser lambda (`app/cli.py:904`) -> `cmd_dataset_build_or_update` (`app/cli.py:110`) -> `app.dataset_pipeline.run_dataset_import` -> `PRODUCTION`
- `dataset update` -> parser lambda (`app/cli.py:909`) -> `cmd_dataset_build_or_update` (`app/cli.py:110`) -> `app.dataset_pipeline.run_dataset_import` -> `PRODUCTION`
- `dataset sync-global` -> `cmd_dataset_sync_global` (`app/cli.py:127`) -> `OrchestratorService.sync_global_db` -> `PRODUCTION`
- `plan materialize` -> `cmd_plan_materialize` (`app/cli.py:146`) -> `app.batch_orchestrator.materialize_batch_plan` -> `PRODUCTION`
- `run pipeline` -> `cmd_run_pipeline` (`app/cli.py:160`) -> `app.runtime_orchestrator.run_batch_pipeline` -> `PRODUCTION`
- `gui` -> `cmd_gui` (`app/cli.py:323`) -> `app.gui.launch_gui` -> `PRODUCTION`
- `run-sample` -> `cmd_run_sample` (`app/cli.py:185`) -> `OrchestratorService.run_batch` -> `PRODUCTION`
- `runner-test run` -> `cmd_runner_test_run` (`app/cli.py:628`) -> `app.runner_test_harness.run_runner_test_harness` -> `TOOLING`
- `runner-test radimp-driving-matrix` -> `cmd_runner_test_radimp_driving_matrix` (`app/cli.py:713`) -> `app.runner_test_harness.run_runner_test_radimp_driving_matrix` -> `TOOLING`
- `runner-test radimp-3scope-matrix` -> `cmd_runner_test_radimp_3scope_matrix` (`app/cli.py:741`) -> `app.runner_test_harness.run_runner_test_radimp_3scope_matrix` -> `TOOLING`
- `runner-test le-proof-matrix` -> `cmd_runner_test_le_proof_matrix` (`app/cli.py:776`) -> `app.runner_test_harness.run_runner_test_le_proof_matrix` -> `TOOLING`
- `runner-test open-dialog-only` -> `cmd_runner_test_open_dialog_only` (`app/cli.py:655`) -> `app.runner_test_harness.run_runner_test_open_dialog_only` -> `TOOLING`
- `runner-test import-start-apply-only` -> `cmd_runner_test_import_start_apply_only` (`app/cli.py:672`) -> `app.runner_test_harness.run_runner_test_import_start_apply_only` -> `TOOLING`
- `runner-test le-repair-import-only` -> `cmd_runner_test_le_repair_import_only` (`app/cli.py:689`) -> `app.runner_test_harness.run_runner_test_le_repair_import_only` -> `TOOLING`
- `theme preview` -> `cmd_theme_preview` (`app/cli.py:329`) -> `ui.theme_preview.launch_preview` -> `TOOLING`
- `compat verify` -> `cmd_compat_verify` (`app/cli.py:335`) -> `app.compat_verification.run_compat_verification` -> `EXPERIMENTAL`
- `projectpage-ath-test` -> `cmd_projectpage_ath_test` (`app/cli.py:371`) -> `app.projectpage_ath_test.run_projectpage_ath_test_suite` -> `EXPERIMENTAL`
- `projectpage-ath-experiment` -> parser lambda (`app/cli.py:1548`) -> `cmd_projectpage_ath_experiment` (`app/cli.py:390`) -> `app.projectpage_ath_experiment.run_projectpage_ath_experiment` -> `EXPERIMENTAL`
- `ath-experiments backfill-subkeys` -> `cmd_ath_experiments_backfill_subkeys` (`app/cli.py:425`) -> `app.projectpage_ath_experiment.run_ath_experiments_backfill_subkeys` -> `EXPERIMENTAL`
- `ath-experiments split-unknown` -> `cmd_ath_experiments_split_unknown` (`app/cli.py:436`) -> `app.projectpage_ath_experiment.run_ath_experiments_backfill_unknown_split` -> `EXPERIMENTAL`
- `ath-experiments refined-reports` -> `cmd_ath_experiments_refined_reports` (`app/cli.py:447`) -> `app.projectpage_ath_experiment.run_ath_experiments_refined_reports` -> `EXPERIMENTAL`
- `ath-experiments analyze-compare-mismatch` -> `cmd_ath_experiments_analyze_compare_mismatch` (`app/cli.py:459`) -> `app.projectpage_ath_experiment.run_ath_experiments_analyze_compare_mismatch` -> `EXPERIMENTAL`
- `ath-experiments minimal-completion-search` -> `cmd_ath_experiments_minimal_completion_search` (`app/cli.py:472`) -> `app.minimal_completion_search.run_minimal_completion_search` -> `EXPERIMENTAL`
- `ath-experiments contextual-ranges` -> `cmd_ath_experiments_contextual_ranges` (`app/cli.py:502`) -> `app.contextual_range_analysis.run_contextual_range_analysis` -> `EXPERIMENTAL`
- `ui inspect-akabak` -> `cmd_ui_inspect_akabak` (`app/cli.py:538`) -> `_inspect_ui_tool` / `app.ui_automation.inspector.inspect_tool_ui` -> `TOOLING`
- `ui inspect-vacs` -> `cmd_ui_inspect_vacs` (`app/cli.py:552`) -> `_inspect_ui_tool` / `app.ui_automation.inspector.inspect_tool_ui` -> `TOOLING`
- `vacs discover-graphs` -> `cmd_vacs_discover_graphs` (`app/cli.py:566`) -> `app.vacs_graph_catalog.discover_graph_catalog` -> `TOOLING`
- `ui-discover` -> `cmd_ui_discover` (`app/cli.py:604`) -> `app.ui_automation.discover.discover_app_ui` -> `TOOLING`
- `runs pin` -> `cmd_runs_pin` (`app/cli.py:822`) -> `OrchestratorService.pin_run` -> `PRODUCTION`
- `runs unpin` -> `cmd_runs_unpin` (`app/cli.py:830`) -> `OrchestratorService.unpin_run` -> `PRODUCTION`
- `runs cleanup-testdata` -> `cmd_runs_cleanup_testdata` (`app/cli.py:838`) -> `OrchestratorService.cleanup_test_data` -> `PRODUCTION`

## GUI Entry Points and Run/Export Triggers
- GUI process entry: `app/gui.py:3759` (`main`) -> `app/gui.py:3734` (`launch_gui`).
- Main window root: `app/gui.py:2590` (`MainWindow`).
- Batch persistence trigger: `app/gui.py:3166` (`_save_batch`) calling `self.service.create_batch` at `app/gui.py:3210`.
- Batch execution trigger: `app/gui.py:3296` (`_run_batch`) with worker path calling `self._service.run_batch` at `app/gui.py:189`.
- Export trigger: `app/gui.py:3329` (`_open_export_dialog`) -> `app/gui.py:3398` (`_export_version`) -> `self.service.export_version` at `app/gui.py:3403`.

## Runtime-Observed Surface (S01-S10)
- Core production paths repeatedly hit in traces: `app/runtime_orchestrator.py`, `app/services.py`, `app/project_storage.py`, `app/sql_dataset_store.py`, `app/cfg_renderer.py`, `app/version_resolver.py`, `app/models.py`, `app/settings_store.py`.
- External-orchestration paths observed in real scenarios: `app/akabak_driver.py`, `app/runners.py`, `app/safe_cleanup.py`.
- Compatibility/UI computation is hot in both dry and real runs: `app/compat_engine.py`, `app/compat_schema.py`, `app/compatibility_service.py`, `ui/form_builder.py`, `ui/form_schema.py`, `ui/hints.py`.

## Minimal Shipping Surface (recommended docs baseline)
- CLI commands to document as shipping: `doctor`, `run pipeline`, `run-sample`, `dataset build`, `dataset update`, `dataset sync-global`, `runs pin`, `runs unpin`, `runs cleanup-testdata`, `gui`.
- GUI flows to document as shipping: create/save batch, run batch, export version, run cleanup.
- Keep tooling/experimental commands explicitly separated in docs under non-shipping sections.
