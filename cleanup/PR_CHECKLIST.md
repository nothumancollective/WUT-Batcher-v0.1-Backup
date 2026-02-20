# PR Checklist (`cleanup/2026-02-20-p0` -> `wut-batcher/rebuild`)

## Scope
- [x] P0.3 Docs drift / legacy quarantine
  - Commit: `0d058c9`
  - Summary: moved old context doc to `docs/legacy/ChatGPT_Context_OLD.md` with LEGACY banner; created new `docs/ChatGPT_Context.md` aligned to current entrypoints.

- [x] P0.2 Silent exception / UI traceback integrity
  - Commit: `07ebc6b`
  - Summary: `OrchestratorService.evaluate_batch_definition` now returns structured fatal issue state for missing project instead of raising `FileNotFoundError`.

- [x] P0.1 External-process hang safety (watchdog)
  - Commit: `5c3f774`
  - Summary: external runner timeout path now kills process tree (Windows-safe `taskkill /T /F`); added Windows timeout/kill-tree unit test.

- [x] P0.4 Exit code semantics
  - Commit: `7b92ade`
  - Summary: non-zero exit on doctor fail reports and failed run pipeline summaries; added CLI exit-code tests.

## Validation
- [x] `python tools/audit/run_tests_bounded.py` executed after each P0 commit.
- [x] Fast non-external smoke commands executed after each P0 commit:
  - `python -m app --help`
  - `python -m app doctor --report-path ...`
  - `python -m app run-sample --dry-run --library-root ...`
- [x] Validation evidence logged in `cleanup/STATUS.md`.
