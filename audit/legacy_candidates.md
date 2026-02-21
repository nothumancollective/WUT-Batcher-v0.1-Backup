# Legacy Candidates Census (Static Reachability from Production Roots)

## Method
- Roots: `app.__main__` (`app/__main__.py:3`), `app.cli` (`app/cli.py:1844`), `app.gui` (`app/gui.py:3759`).
- Graph method: AST import graph across `app/` and `ui/`, then BFS from roots.
- Result: `67` reachable modules, `76` total `app/ui` modules, `9` non-trivial unreachable modules (`11` including package markers).
- Package-marker note: `app/__init__.py` and `ui/__init__.py` are not imported directly by roots/submodules in this graph and are not removal candidates.

## Candidate Table
| Path | Last commit | Prod import refs (reachable modules) | Test refs | Doc refs | Class | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `app/compat_rules.py` | `f42fda3` (2026-02-13) | `0` | `1` (`tests/test_compat_rules.py`) | `0` | `QUARANTINE` | Test-only compatibility export path; not used by CLI/GUI/runtime traces. |
| `app/gui_theme.py` | `4781f67` (2026-02-12) | `0` | `0` | `0` | `DELETE_LATER` | Wrapper module appears fully orphaned; no code/test/doc references found. |
| `app/parameter_registry.py` | `e1e282b` (2026-02-12) | `0` | `0` | `2` (`docs/parameter_registry.md`, `docs/legacy/ChatGPT_Context_OLD.md`) | `QUARANTINE` | Docs-only artifact reconstructed from recovery notes. |
| `app/path_resolver.py` | `e1e282b` (2026-02-12) | `0` | `0` | `2` (`docs/path_resolver.md`, `docs/legacy/ChatGPT_Context_OLD.md`) | `QUARANTINE` | Docs-only helper not wired into current runtime. |
| `app/storage_migrations.py` | `e1e282b` (2026-02-12) | `0` | `0` | `1` (`docs/ath_update_todo_log.md`) | `QUARANTINE` | Migration helper exists but is not called from production roots. |
| `app/ui_risk_layer.py` | `3afdeb0` (2026-02-14) | `0` | `1` (`tests/test_ui_risk_layer.py`) | `2` (`docs/Rules.md`, `docs/UI_RISK_LAYER.md`) | `QUARANTINE` | Test/doc-covered, but currently disconnected from CLI/GUI entry roots. |
| `app/ui_automation/__init__.py` | `b91a701` (2026-02-13) | `7` | `4` | `0` | `KEEP` | Namespace package marker; submodules are active in S10 traces. |
| `app/ui_contracts/__init__.py` | `b91a701` (2026-02-13) | `2` | `2` | `0` | `KEEP` | Namespace package marker referenced by active drivers. |
| `app/vacs_exporters/__init__.py` | `5e37f96` (2026-02-13) | `3` | `1` | `0` | `KEEP` | Package marker used by exporter registry/builtin modules. |
| `scripts/vacs_export_dialog_rounds.py` | `40756a8` (2026-02-20) | `N/A` (standalone script) | `0` | `2` | `KEEP` | Script entrypoint, observed in `S10` (`--help`) traces. |
| `scripts/vacs_export_save_all.py` | `40756a8` (2026-02-20) | `N/A` (standalone script) | `0` | `1` | `KEEP` | Script entrypoint, observed in `S10` (`--help`) traces. |
| `scripts/vacs_interim_reimport.py` | `40756a8` (2026-02-20) | `N/A` (standalone script) | `0` | `2` | `KEEP` | Script entrypoint, observed in `S10` (`--help`) traces. |

## WinFR Recovered Artifacts Bucket
- Filesystem scan for filenames/paths matching `winfr|recovered` found no standalone recovered artifact files (`Get-ChildItem -Recurse -File ... | where name/fullpath matches`).
- Code comments contain recovery breadcrumbs only, e.g. `app/parameter_registry.py:1`, `app/path_resolver.py:1`, `app/batch_planner.py:1`, `app/dataset_pipeline.py:1`.
- Bucket result: no removable "winfr recovered artifact" file candidates with zero code/doc references.

## Repro Commands
- Reachability roots and parser anchors: `rg -n "def build_parser|def main\(" app/__main__.py app/cli.py app/gui.py`
- Candidate import checks (example):
  - `rg -n "import app.path_resolver|from app.path_resolver" app ui`
  - `rg -n "app.path_resolver|path_resolver.py" tests docs`
- Last-touch metadata (example): `git log -1 --format="%h|%ad|%s" --date=short -- app/path_resolver.py`
