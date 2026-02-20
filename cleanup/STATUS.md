# Cleanup Status

## Step 0: Sanity Check
- Current branch: `cleanup/2026-02-20-p0`
- Branch heads:
  - `main`: `e1e282b`
  - `wut-batcher/rebuild`: `1a52e15`
  - `audit/2026-02-20`: `675561a`
  - `cleanup/2026-02-20-p0`: `1a52e15`

### Branch Graph (last ~30 commits across main/rebuild/audit)
```text
* 675561a (origin/audit/2026-02-20, audit/2026-02-20) audit: add phase E synthesis (scenarios, results, cleanup plan)
* 852c592 audit: add opt-in AUDIT_MODE instrumentation
* b2c3564 audit: add phase A inventory and evidence artifacts
*   1a52e15 (HEAD -> cleanup/2026-02-20-p0, origin/wut-batcher/rebuild, wut-batcher/rebuild) Merge branch 'wut-batcher/rebuild' of https://github.com/nothumancollective/WUT-Batcher-v0.1 into wut-batcher/rebuild
|\  
| * 8c04ed6 Refine external VACS export mapping metadata and variants
| * 01b5967 Optimize AKABAK/VACS fast-path waits with safe fallbacks
| * 8abaab8 Make GUI batch run async and add background automation toggle
* | 2fd5697 Refine external VACS export mapping metadata and variants
* | 79da972 Make GUI batch run async and add background automation toggle
* | 63971fe Optimize AKABAK/VACS fast-path waits with safe fallbacks
|/  
* 6eaef84 Revert "Prefer background automation for AKABAK/VACS with foreground fallback"
* c11870f Prefer background automation for AKABAK/VACS with foreground fallback
* c6c8a7f Disable default interim rescue fallback; add fallback audit log
* b415686 feat(ui): align non-batch pages with batch design language
* 84c423e feat(runtime): stabilize run orchestration and VACS export flow
* 844db49 Batch parameter form: enforce popup-based mesh advanced and enclosure flow
* 422f148 Batch styling pass: header reset icon, arrows, sliders, and scrollbar refinements
* 161de6d Batch surface: add enclosure/export dialogs and relocate project-manager action
* f2c8518 Batch cards: move block reset to headers and streamline mode/advanced row layout
* 5ef571b Batch top/bottom bar restructuring and validation summary relocation
* dfa9b6f Batch visual polish: arrows, loader styling, and closer preview framing
* ef13c20 Batch form layout: fix segmented rows and tighten subblock spacing
* 9780cba fix: harden STL hook and preview diagnostics
* 137fb9d ui: commit pending project/batch polish updates
* 381d6b7 docs(qa): record interactive 3-run UI stress results and stability status
* 67067ec test(ui-e2e): add 3-cycle interactive UI stress run with preview and run-pipeline assertions
* aa22ebd docs(qa): add pipeline integration report, changelog and cleanup policy notes
* 3ac9c7f fix(runtime): enforce per-version cfg/export cleanup contract with sync-gated persistence
* 8beea4e fix(batch-sweeps): remove controller sweeps and enable sweeps in non-basic numeric subblocks
* 14c57aa test+docs: cover warning UX and sweep policy updates, document final UI pass
```

### Base Relationship Confirmation
- `merge-base(wut-batcher/rebuild, audit/2026-02-20) = 1a52e159171d753a46dd2b2e6c337d13561e5876`
- `wut-batcher/rebuild` current HEAD is the same base commit `1a52e15`.
- `cleanup/2026-02-20-p0` was created from `wut-batcher/rebuild` and is currently at `1a52e15`.
- `merge-base(main, wut-batcher/rebuild) = e1e282b0d2c202af0218be5255c2663d0b04004d`.
- `merge-base(main, audit/2026-02-20) = e1e282b0d2c202af0218be5255c2663d0b04004d`.

## Validation Log
- Pending.


## Validation After Commit `0d058c9` (P0.3)
- Timestamp: `2026-02-20T20:32:31.536593+01:00`
- Bounded tests command: `python tools/audit/run_tests_bounded.py`
- Bounded tests result: `exit=0`, discovered=`322`, selected=`176`, skipped=`146`, subprocess_runs=`18`, timeouts=`0`.
- Fast non-external scenarios:
  - `python -m app --help` -> `exit=0`, `duration_s=0.144`
  - `python -m app doctor --report-path cleanup/runtime/doctor_report_p03.json` -> `exit=0`, `duration_s=0.332`
  - `python -m app run-sample --dry-run --library-root cleanup/runtime/lib_p03` -> `exit=0`, `duration_s=0.438`
- Evidence files:
  - `cleanup/runtime/validation_p03.json`
  - `cleanup/runtime/help.stdout.txt`
  - `cleanup/runtime/doctor.stdout.txt`
  - `cleanup/runtime/run_sample_dry.stdout.txt`


## Validation After Commit `07ebc6b` (P0.2)
- Timestamp: `2026-02-20T20:35:51.172908+01:00`
- Bounded tests command: `python tools/audit/run_tests_bounded.py`
- Bounded tests result: `exit=0`, discovered=`323`, selected=`177`, skipped=`146`, subprocess_runs=`18`, timeouts=`0`.
- Fast non-external scenarios:
  - `python -m app --help` -> `exit=0`, `duration_s=0.138`
  - `python -m app doctor --report-path cleanup/runtime/doctor_report_p02.json` -> `exit=0`, `duration_s=0.311`
  - `python -m app run-sample --dry-run --library-root cleanup/runtime/lib_p02` -> `exit=0`, `duration_s=0.369`
- Evidence files:
  - `cleanup/runtime/validation_p02.json`
  - `cleanup/runtime/help_p02.stdout.txt`
  - `cleanup/runtime/doctor_p02.stdout.txt`
  - `cleanup/runtime/run_sample_dry_p02.stdout.txt`


## Validation After Commit `5c3f774` (P0.1)
- Timestamp: `2026-02-20T20:38:19.752288+01:00`
- Bounded tests command: `python tools/audit/run_tests_bounded.py`
- Bounded tests result: `exit=0`, discovered=`324`, selected=`178`, skipped=`146`, subprocess_runs=`18`, timeouts=`0`.
- Fast non-external scenarios:
  - `python -m app --help` -> `exit=0`, `duration_s=0.147`
  - `python -m app doctor --report-path cleanup/runtime/doctor_report_p01.json` -> `exit=0`, `duration_s=0.365`
  - `python -m app run-sample --dry-run --library-root cleanup/runtime/lib_p01` -> `exit=0`, `duration_s=0.475`
- Evidence files:
  - `cleanup/runtime/validation_p01.json`
  - `cleanup/runtime/help_p01.stdout.txt`
  - `cleanup/runtime/doctor_p01.stdout.txt`
  - `cleanup/runtime/run_sample_dry_p01.stdout.txt`


## Validation After Commit `7b92ade` (P0.4)
- Timestamp: `2026-02-20T20:41:00.682354+01:00`
- Bounded tests command: `python tools/audit/run_tests_bounded.py`
- Bounded tests result: `exit=0`, discovered=`328`, selected=`182`, skipped=`146`, subprocess_runs=`19`, timeouts=`0`.
- Fast non-external scenarios:
  - `python -m app --help` -> `exit=0`, `duration_s=0.140`
  - `python -m app doctor --report-path cleanup/runtime/doctor_report_p04.json` -> `exit=3`, `duration_s=0.301` (expected after exit-code semantics hardening when doctor reports fail)
  - `python -m app run-sample --dry-run --library-root cleanup/runtime/lib_p04` -> `exit=0`, `duration_s=0.365`
- Evidence files:
  - `cleanup/runtime/validation_p04.json`
  - `cleanup/runtime/help_p04.stdout.txt`
  - `cleanup/runtime/doctor_p04.stdout.txt`
  - `cleanup/runtime/run_sample_dry_p04.stdout.txt`
