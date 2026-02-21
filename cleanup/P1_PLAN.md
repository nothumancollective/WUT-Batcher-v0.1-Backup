# P1 Plan (Post-Merge, No Code Changes Yet)

## Scope
- Branch target for next implementation branch: `wut-batcher/rebuild`
- This document defines safe next steps only. No runtime-code changes are included here.

## Constraints
- No unbounded `python -m unittest discover`.
- No automatic external-tool runs (`gmsh`, `ath`, `akabak`, `vacs`) in P1 automation.
- Keep all new validation bounded and reproducible.

## Evidence Anchors
- `audit/01_findings.md`:
  - `F-002` gmsh hang / S07 instability
  - `F-004` silent exception risk
- `audit/scenario_results.md`:
  - `S07_projectpage_ath_experiment_min` and retry marked unstable
- `cleanup/POST_MERGE_STATUS.md`:
  - Post-merge bounded validation baseline

## P1.a Skipped-Test Map + Bounded Include Strategy
- Goal:
  - Produce an explicit inventory of tests currently skipped by default bounded filter (`ui/qt/gui/preview/stl/gmsh`).
- Proposed deliverables:
  - `cleanup/P1_skipped_tests_map.md`
  - `cleanup/data/p1_skipped_tests.json`
- Proposed method:
  - Parse `audit/tests_discovered.txt` and classify skipped entries by token hit.
  - Group into buckets: `ui`, `preview`, `stl`, `gmsh`, `mixed`.
  - Identify candidate subsets with low external-tool risk.

## P1.b Bounded Runner Optional Include Flags
- Goal:
  - Add opt-in include switches while preserving current safe defaults.
- Proposed CLI additions for `tools/audit/run_tests_bounded.py`:
  - `--include ui`
  - `--include preview`
  - `--include external`
- Behavior model:
  - Default remains existing skip behavior.
  - `--include <group>` removes that group from skip filter.
  - Apply stricter timeout profiles per include group (e.g., UI chunk timeout lower than external).

## P1.c Safe UI/Preview Representative Tests (No gmsh)
- Goal:
  - Select 1–2 representative tests from skipped area that can run with stubs/mocks and no gmsh.
- Candidate selection rules:
  - Must not require real external executables.
  - Must run under bounded runner with explicit timeout.
  - Must produce deterministic pass/fail outcome.
- Expected output:
  - `cleanup/P1_ui_preview_candidates.md` with selected tests + rationale + exact command.

## P1.d Controlled S07 Re-Test Plan (Manual Only)
- Goal:
  - Define a manual-only, operator-confirmed S07 re-test using the new timeout watchdog.
- Requirements:
  - Explicit pre-checks (tool paths, environment, isolated output dirs).
  - Strict wall-clock caps and process-tree termination verification.
  - Manual execution checklist, not part of automatic CI/test script.
- Expected output:
  - `cleanup/P1_s07_manual_retest.md`

## Execution Order (Recommended)
1. P1.a skipped-test map
2. P1.b bounded include flags
3. P1.c run 1–2 safe UI/preview tests with bounded settings
4. P1.d prepare manual S07 retest checklist
