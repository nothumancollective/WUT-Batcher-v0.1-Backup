# Post-Merge P1 Checkpoint

- branch: `wut-batcher/rebuild`
- checkpoint_after_pr: `#5`

## Command 1

- command: `python tools/audit/run_tests_bounded.py`
- exit_code: `0`
- output:

```text
Discovered: 329
Selected: 183
Skipped by default filter: 146
Include modes: none
Effective timeout_s: 120.0
Subprocess runs: 19
Observed failures/errors: 0/0
Artifacts:
  - C:/Users/maximilianheinze/Desktop/WUT Batcher v0.1/audit/tests_discovered.txt
  - C:/Users/maximilianheinze/Desktop/WUT Batcher v0.1/audit/tests_summary.md
  - C:/Users/maximilianheinze/Desktop/WUT Batcher v0.1/audit/flaky_or_hanging_tests.md
  - C:/Users/maximilianheinze/Desktop/WUT Batcher v0.1/audit/data/bounded_runner_results.json
```

## Command 2

- command: `python tools/audit/run_tests_bounded.py --pattern test_batch_page_ui.py --include ui --max-tests 20 --chunk-size 5 --include-strict-timeout-s 45 --timeout-s 120`
- exit_code: `0`
- output:

```text
Discovered: 35
Selected: 20
Skipped by default filter: 0
Include modes: ui
Effective timeout_s: 45.0
Subprocess runs: 4
Observed failures/errors: 0/0
Artifacts:
  - C:/Users/maximilianheinze/Desktop/WUT Batcher v0.1/audit/tests_discovered.txt
  - C:/Users/maximilianheinze/Desktop/WUT Batcher v0.1/audit/tests_summary.md
  - C:/Users/maximilianheinze/Desktop/WUT Batcher v0.1/audit/flaky_or_hanging_tests.md
  - C:/Users/maximilianheinze/Desktop/WUT Batcher v0.1/audit/data/bounded_runner_results.json
```

## Runtime Artifact Tracking Check

- `git status --short`: clean
- tracked runtime artifacts found (`git ls-files ...`): none
