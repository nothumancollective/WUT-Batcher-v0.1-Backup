# Post-Merge Status

- Branch: `wut-batcher/rebuild`
- Merge check: `a3effe2 Merge pull request #3 from nothumancollective/cleanup/2026-02-20-p0`

## Validation Results
| Check | Command | Exit | Timed Out | Duration (s) | Error |
|---|---|---:|---|---:|---|
| `bounded_tests` | `python tools/audit/run_tests_bounded.py` | 0 | `False` | 21.555 | - |
| `help` | `python -m app --help` | 0 | `False` | 0.132 | - |
| `doctor` | `python -m app doctor --report-path cleanup/runtime/postmerge_doctor.json` | 3 | `False` | 0.284 |     } \|   ] \| } |
| `run_sample_dry` | `python -m app run-sample --dry-run --library-root cleanup/runtime/postmerge_lib` | 0 | `False` | 0.375 | - |

## Evidence Files
- `cleanup\runtime\postmerge_bounded_tests.stdout.txt`
- `cleanup\runtime\postmerge_bounded_tests.stderr.txt`
- `cleanup\runtime\postmerge_help.stdout.txt`
- `cleanup\runtime\postmerge_help.stderr.txt`
- `cleanup\runtime\postmerge_doctor.stdout.txt`
- `cleanup\runtime\postmerge_doctor.stderr.txt`
- `cleanup\runtime\postmerge_run_sample_dry.stdout.txt`
- `cleanup\runtime\postmerge_run_sample_dry.stderr.txt`
- `cleanup\runtime\postmerge_validation.json`

## Housekeeping
- `cleanup/runtime/` tracking check:
  - `git ls-files cleanup/runtime` -> empty (no tracked files).
  - `git check-ignore -v cleanup/runtime cleanup/runtime/postmerge_validation.json` -> ignored via `.git/info/exclude` (`cleanup/runtime/` rule).
- Optional branch cleanup after merge confirmation:
  - Local branch deleted: `git branch -d cleanup/2026-02-20-p0`
  - Remote branch deleted: `git push origin --delete cleanup/2026-02-20-p0`
