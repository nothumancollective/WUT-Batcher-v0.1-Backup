# P1.1 UI Fail Triage + Fix Summary

- branch: `fix/2026-02-21-batchpage-ui-tests`
- scope: UI test expectation drift triage (no external executables, no unbounded discovery)
- failing set addressed: 4 tests in `tests/test_batch_page_ui.py`

## 1) `test_advanced_toggle_hides_advanced_rows_by_default`

- test anchor: `tests/test_batch_page_ui.py:720`
- original failure log: `cleanup/p1_runtime/failures/test_advanced_toggle_hides_advanced_rows_by_default.txt`
- root cause: expectation drift (test referenced legacy private API `_group_advanced_buttons`).
- production path:
  - `ui/batch_parameter_form.py:594`
  - `ui/batch_parameter_form.py:603`
  - `ui/batch_parameter_form.py:731`
  - `ui/batch_parameter_form.py:764`
  - `ui/batch_parameter_form.py:1425`
- change made:
  - updated test to assert mesh advanced dialog entrypoint (`_mesh_advanced_button`) and detached-hidden advanced row state, without clicking modal dialog.
- post-fix single-test log: `cleanup/p1_runtime/failures/test_advanced_toggle_hides_advanced_rows_by_default_after_fix.txt`
- commit: `1cef329` (`fix(ui): align mesh advanced toggle test with dialog-based UI`)

## 2) `test_gcurve_subgroup_headers_hidden_for_no_gcurve`

- test anchor: `tests/test_batch_page_ui.py:307`
- original failure log: `cleanup/p1_runtime/failures/test_gcurve_subgroup_headers_hidden_for_no_gcurve.txt`
- root cause: expectation drift (compact batch layout intentionally omits GCurve subgroup title labels).
- production path:
  - `ui/batch_parameter_form.py:572`
  - `ui/batch_parameter_form.py:574`
  - `ui/batch_parameter_form.py:577`
- change made:
  - updated test to assert that `Superellipse`/`Superformula` subgroup headers are omitted.
- post-fix single-test log: `cleanup/p1_runtime/failures/test_gcurve_subgroup_headers_hidden_for_no_gcurve_after_fix.txt`
- commit: `e1cade8` (`fix(ui): update gcurve subgroup header expectation for compact layout`)

## 3) `test_disclosure_hint_marks_selected_segment_button`

- test anchor: `tests/test_batch_page_ui.py:169`
- original failure log: `cleanup/p1_runtime/failures/test_disclosure_hint_marks_selected_segment_button.txt`
- root cause: expectation drift (helper label is intentionally suppressed for controller groups; disclosure hint is still applied on control widgets).
- production path:
  - `ui/batch_parameter_form.py:1147`
  - `ui/batch_parameter_form.py:1175`
  - `ui/batch_parameter_form.py:1182`
- change made:
  - replaced helper-label-visible assertion with controller-group expected hidden helper assertion; retained disclosure-hint button assertion.
- post-fix single-test log: `cleanup/p1_runtime/failures/test_disclosure_hint_marks_selected_segment_button_after_fix.txt`
- commit: `75e06d3` (`fix(ui): align disclosure hint test with controller-group behavior`)

## 4) `test_batch_ui_risks_colorize_fields_and_warn_summary`

- test anchor: `tests/test_batch_page_ui.py:442`
- original failure log: `cleanup/p1_runtime/failures/test_batch_ui_risks_colorize_fields_and_warn_summary.txt`
- root cause: expectation drift (test referenced `action_status_pill`, which belongs to another page flow; `BatchPage` uses `summary_issue_hint`).
- production path:
  - `app/gui.py:1873`
  - `app/gui.py:1960`
  - `app/gui.py:2144`
  - `app/gui.py:2284`
- change made:
  - switched assertion target from `action_status_pill` to `summary_issue_hint` severity/text.
- post-fix single-test log: `cleanup/p1_runtime/failures/test_batch_ui_risks_colorize_fields_and_warn_summary_after_fix.txt`
- commit: `83a103a` (`fix(ui): point batch warning summary assertion to current hint widget`)

## Final Verification

- command:
  - `python tools/audit/run_tests_bounded.py --pattern test_batch_page_ui.py --include ui --max-tests 20 --chunk-size 5 --timeout-s 120 --include-strict-timeout-s 45 --audit-dir cleanup/p1_runtime`
- environment used:
  - `QT_QPA_PLATFORM=offscreen`
  - `HOME=cleanup/p1_runtime/home`
  - `USERPROFILE=cleanup/p1_runtime/home`
- final bounded result (`cleanup/p1_runtime/tests_summary.md`):
  - discovered: 35
  - selected: 20
  - subprocess runs: 4
  - failed runs: 0
  - timeout runs: 0
  - observed failing tests: none
