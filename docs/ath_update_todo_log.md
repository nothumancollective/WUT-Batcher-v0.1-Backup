# ATH Update TODO Log

Stand: 2026-02-10

## Roadmap-Status

- [x] M0 Foundations (Contracts/RunnerMode/Migration)
  - Commit: `cce4765`
  - Kernpfade: `app/models.py`, `app/cli.py`, `tools/legacy/storage_migrations.py`, `schemas/constraints.schema.json`
- [x] M1 Wissensartefakte (Katalog/Ruleset/Runner-Restrictions + Loader)
  - Commit: `7f1cab5`
  - Kernpfade: `app/knowledge/ath/catalog.v1.json`, `app/knowledge/ath/ruleset.v1.json`, `app/ath_knowledge.py`
- [x] M2 Constraint Engine (visible/validity/sweepable)
  - Commit: `7d17701`
  - Kernpfade: `app/compat_engine.py`, `tests/test_m2_compat_engine.py`
- [x] M3 Project Page Constraints UI (No-Invalid-UI + fatal block)
  - Commit: `55954d8`
  - Kernpfade: `app/gui.py`
- [x] M4 Batch Page Sweep UI + Sweepmode single/combined + job_count
  - Commit: `55954d8`
  - Kernpfade: `app/gui.py`, `app/batch_planner.py`, `app/cli.py`
- [x] M5 Deterministischer Planner + CFG-Renderer + Pflichtblock + Snapshots
  - Commit: `55954d8`
  - Kernpfade: `app/batch_planner.py`, `app/cfg_renderer.py`, `app/batch_artifacts.py`, `Runner/wut_ath_batch_creator_v2.py`
- [x] M6 Sim/Export strikt batch-global getrennt von Geometrie
  - Commit: `55954d8`
  - Kernpfade: `app/batch_artifacts.py`, `app/gui.py`
- [x] M7 QA/Regression/Fixtures + Dry-Run Smoke
  - Commit: `55954d8`
  - Kernpfade: `tests/test_m4_batch_modes.py`, `tests/test_m5_planner_renderer.py`, `tests/test_m6_snapshots.py`, `tests/test_m7_dry_run_smoke.py`
- [x] M8 Doku/Upgrade-Pfad
  - Commit: `7c76b55`
  - Kernpfade: `docs/ath_constraints_runnermode_guide.md`, `docs/ath_update_todo_log.md`

## Designentscheidungen (Kurz)

- RunnerMode ist projektweit in `constraints.json` verankert.
- Katalog/Ruleset sind versionierte JSON-Artefakte unter `app/knowledge/ath/`.
- Engine wertet Regeln deklarativ aus und trennt ATH-Issues von Runner-Restrictions.
- Batch-Expansion ist deterministisch:
  - `single`: pro Parameter nacheinander.
  - `combined`: kartesisches Produkt in stabiler Schlüsselreihenfolge.
- CFGs werden durch den Orchestrator gerendert; Pflicht-Source-Block gewinnt immer.
- Pro Version wird ein vollständiger Snapshot gespeichert:
  - Geometrie + Sim/Export Settings + RunnerMode.

## 2026-02-16 Consistency Update

M4 status text was historically marked done while the GUI still used JSON textareas.
This mismatch is now resolved by the Batch UI rework:
- `app/gui.py`: project-style Batch layout, structured parameter/sweep/edit/clone flow
- `ui/batch_parameter_form.py`: per-parameter base+sweep controls
- `ui/batch_export_panel.py`: presets + advanced export specs
- `ui/batch_preview_placeholder.py`: STL preview placeholder
- `app/services.py` + `app/sql_dataset_store.py`: SQL-history ETA estimator
- `app/compatibility_service.py`: invalid sweep parsing now surfaces `sweep_parse_failed`

See `docs/BATCH_UI.md` for the final implemented state.
