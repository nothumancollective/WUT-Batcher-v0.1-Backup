# P1 Verification (Bounded, No External Tools)

- branch: `cleanup/2026-02-21-p1`
- run_utc: `2026-02-21T00:13:02Z`
- command: `python tools/audit/run_tests_bounded.py --pattern test_batch_page_ui.py --include ui --max-tests 20 --chunk-size 5 --timeout-s 120 --include-strict-timeout-s 45 --audit-dir cleanup/p1_runtime`
- external executables used: `none`

## Result

- exit_code: `0`
- discovered_total: `35`
- selected_total: `20`
- skipped_default_total: `0` (UI family explicitly included)
- include_modes: `ui`
- effective_timeout_s: `45.0`
- subprocess_runs_total: `4`
- timeout_runs: `0`
- accumulated_duration_s: `3.98`

## Observed Failing Tests

- `test_batch_page_ui.BatchPageUiTests.test_advanced_toggle_hides_advanced_rows_by_default`
- `test_batch_page_ui.BatchPageUiTests.test_batch_ui_risks_colorize_fields_and_warn_summary`
- `test_batch_page_ui.BatchPageUiTests.test_disclosure_hint_marks_selected_segment_button`
- `test_batch_page_ui.BatchPageUiTests.test_gcurve_subgroup_headers_hidden_for_no_gcurve`

## Artifacts (Ignored Runtime)

- `cleanup/p1_runtime/tests_discovered.txt`
- `cleanup/p1_runtime/tests_summary.md`
- `cleanup/p1_runtime/flaky_or_hanging_tests.md`
- `cleanup/p1_runtime/data/bounded_runner_results.json`
- `cleanup/p1_runtime/data/bounded_chunk_logs/*.log`

