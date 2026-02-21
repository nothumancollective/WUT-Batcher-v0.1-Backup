# P1 PR Preflight

- generated_utc: 2026-02-21T00:24:59.452253+00:00
- base: `wut-batcher/rebuild`
- head: `cleanup/2026-02-21-p1`
- current_branch: `cleanup/2026-02-21-p1`
- git_status_porcelain_clean_before_doc_write: true
- git_status_after_doc_write: false (`cleanup/P1_PR_PREFLIGHT.md` untracked at generation time)
- cleanup/p1_runtime tracked files: `none`
- cleanup/p1_runtime ignore rule: `cleanup/.gitignore` -> `p1_runtime/`

## Changed Files vs Base

- `A	cleanup/.gitignore`
- `A	cleanup/P1_VERIFICATION.md`
- `A	cleanup/tests_skipped_map.md`
- `M	tools/audit/run_tests_bounded.py`

## Commits vs Base

- `75b4c5d docs(qa): add P1 verification notes and ignore runtime logs`
- `2c70425 tools(audit): add include flags for bounded test families`
- `c044c7c docs(qa): map skipped tests by token and module`

## Verification Summary

- command: `python tools/audit/run_tests_bounded.py --pattern test_batch_page_ui.py --include ui --max-tests 20 --chunk-size 5 --timeout-s 120 --include-strict-timeout-s 45 --audit-dir cleanup/p1_runtime`
- bounded include-mode validation completed (UI subset)
- known failing UI tests in that subset (4):
  - `test_batch_page_ui.BatchPageUiTests.test_advanced_toggle_hides_advanced_rows_by_default`
  - `test_batch_page_ui.BatchPageUiTests.test_batch_ui_risks_colorize_fields_and_warn_summary`
  - `test_batch_page_ui.BatchPageUiTests.test_disclosure_hint_marks_selected_segment_button`
  - `test_batch_page_ui.BatchPageUiTests.test_gcurve_subgroup_headers_hidden_for_no_gcurve`
