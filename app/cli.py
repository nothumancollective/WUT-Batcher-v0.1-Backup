from __future__ import annotations

import argparse
from contextlib import closing
import dataclasses
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, Optional

from app.models import AppConfig, Batch, Project
from app.services import OrchestratorService
from app.settings_store import SettingsStore, UserSettings


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))

def _json_default(obj: Any):
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


def _default_project_root(config: AppConfig, project_id: str) -> Path:
    return config.projects_root_path / f"Project_{project_id}"


def _is_executable_path(path: Optional[str]) -> bool:
    if not path:
        return False
    candidate = Path(path).expanduser()
    return candidate.exists() and candidate.is_file() and os.access(candidate, os.X_OK)


def _status_rows_for_versions(db_path: Path, version_ids: list[str]) -> list[tuple[str, str]]:
    if not version_ids:
        return []
    placeholders = ", ".join("?" for _ in version_ids)
    with closing(sqlite3.connect(str(db_path))) as conn:
        rows = conn.execute(
            f"SELECT version_id, status FROM versions WHERE version_id IN ({placeholders}) ORDER BY version_id",
            tuple(version_ids),
        ).fetchall()
    return [(str(row[0]), str(row[1])) for row in rows]


def _table_count_for_versions(db_path: Path, table: str, version_ids: list[str]) -> int:
    if not version_ids:
        return 0
    placeholders = ", ".join("?" for _ in version_ids)
    with closing(sqlite3.connect(str(db_path))) as conn:
        if table in {"graphs", "ath_dimensions"}:
            row = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE version_id IN ({placeholders})",
                tuple(version_ids),
            ).fetchone()
            return int(row[0]) if row else 0
        if table == "graph_points":
            row = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM graph_points gp
                JOIN graph_series gs ON gs.series_id = gp.series_id
                JOIN graphs g ON g.graph_id = gs.graph_id
                WHERE g.version_id IN ({placeholders})
                """,
                tuple(version_ids),
            ).fetchone()
            return int(row[0]) if row else 0
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    import app.doctor_service as doctor_service

    config = AppConfig.load(args.config)
    report = doctor_service.run_doctor_checks(
        config,
        config_path=Path(args.config) if args.config else None,
        fix=args.fix,
        kill_zombies=args.kill_zombies,
        report_path=Path(args.report_path) if args.report_path else None,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False, default=_json_default))
    status = ""
    if isinstance(report, dict):
        status = str(report.get("overall_status", "") or "")
    else:
        status = str(getattr(report, "overall_status", "") or "")
    return 0 if status.lower() != "fail" else 3


def cmd_batch_job_count(args: argparse.Namespace) -> int:
    import app.batch_planner as batch_planner

    batch = Batch.load(args.batch_json)
    constraints: Dict[str, Any] = {}
    if args.constraints_json:
        constraints = _read_json(Path(args.constraints_json))

    count = batch_planner.compute_job_count(batch, constraints)
    print(count)
    return 0


def cmd_dataset_build_or_update(args: argparse.Namespace, *, rebuild: bool) -> int:
    import app.dataset_pipeline as dataset_pipeline

    config = AppConfig.load(args.config)
    project_root = _default_project_root(config, args.project_id)
    manifest_path = Path(args.manifest_path) if args.manifest_path else (project_root / "dataset_manifest.json")

    summary = dataset_pipeline.run_dataset_import(
        project_id=args.project_id,
        project_root=project_root,
        manifest_path=manifest_path,
        rebuild=rebuild,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=_json_default))
    return 0


def cmd_dataset_sync_global(args: argparse.Namespace) -> int:
    settings_store = SettingsStore()
    settings = settings_store.load()
    if args.library_root:
        settings = UserSettings(
            library_root=args.library_root,
            ath_exe=settings.ath_exe,
            akabak_exe=settings.akabak_exe,
            vacs_exe=settings.vacs_exe,
            template_cfg=settings.template_cfg,
            background_automation_mode=bool(getattr(settings, "background_automation_mode", True)),
        )
        settings_store.save(settings)
    service = OrchestratorService(settings_store=settings_store)
    summary = service.sync_global_db(max_items_per_project=args.max_items_per_project)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=_json_default))
    return 0


def cmd_plan_materialize(args: argparse.Namespace) -> int:
    from app.batch_orchestrator import materialize_batch_plan

    project = Project.from_dict(_read_json(Path(args.project_json)))
    batch = Batch.from_dict(_read_json(Path(args.batch_json)))
    summary = materialize_batch_plan(
        project=project,
        batch=batch,
        projects_root=args.projects_root or "projects",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=_json_default))
    return 0


def cmd_run_pipeline(args: argparse.Namespace) -> int:
    from app.runtime_orchestrator import run_batch_pipeline

    project = Project.from_dict(_read_json(Path(args.project_json)))
    batch = Batch.from_dict(_read_json(Path(args.batch_json)))
    summary = run_batch_pipeline(
        project=project,
        batch=batch,
        projects_root=args.projects_root or "projects",
        template_cfg_path=args.template_cfg,
        ath_executable=args.ath_exe,
        akabak_executable=args.akabak_exe,
        vacs_executable=args.vacs_exe,
        continue_on_error=args.continue_on_error,
        dry_run=args.dry_run,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=_json_default))
    run_status = ""
    if isinstance(summary, dict):
        run_status = str(summary.get("run_status", "") or "")
    else:
        run_status = str(getattr(summary, "run_status", "") or "")
    return 0 if run_status.lower() not in {"fail", "failed", "error"} else 3


def cmd_run_sample(args: argparse.Namespace) -> int:
    settings_store = SettingsStore()
    settings = settings_store.load()
    if args.library_root:
        settings = UserSettings(
            library_root=args.library_root,
            ath_exe=settings.ath_exe,
            akabak_exe=settings.akabak_exe,
            vacs_exe=settings.vacs_exe,
            template_cfg=settings.template_cfg,
            background_automation_mode=bool(getattr(settings, "background_automation_mode", True)),
        )
        settings_store.save(settings)
    service = OrchestratorService(settings_store=settings_store)

    tool_paths = {
        "ath": service.settings.ath_exe,
        "akabak": service.settings.akabak_exe,
        "vacs": service.settings.vacs_exe,
    }
    tool_ready = {key: _is_executable_path(value) for key, value in tool_paths.items()}
    all_tools_ready = all(tool_ready.values())
    if args.real and not all_tools_ready:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "real_run_requested_but_tools_unavailable",
                    "tool_paths": tool_paths,
                    "tool_ready": tool_ready,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 2

    dry_run = bool(args.dry_run or (not args.real and not all_tools_ready))

    if args.project_id:
        project = service.repo.load_project(args.project_id)
    else:
        project = service.create_project(
            args.project_name,
            {
                "fixed_params": {"Length": 120},
                "limits": {},
                "runner_mode": "AkabakImportFixedSource",
            },
        )

    if args.batch_id:
        batch_id = args.batch_id
        service.repo.load_batch(project.project_id, batch_id)
    else:
        batch_summary = service.create_batch(
            project_id=project.project_id,
            batch_name=args.batch_name,
            selected_params={"Throat.Diameter": 30.0, "Coverage.Angle": None},
            sweeps={},
            sweep_mode="single",
            sim_export_params={},
        )
        batch_id = batch_summary.batch_id

    summary = service.run_batch(
        project.project_id,
        batch_id,
        continue_on_error=False,
        dry_run=dry_run,
    )

    project_paths = service.repo.project_paths(project.project_id, ensure=True)
    db_path = project_paths.dataset_dir / "project.sqlite"
    version_ids = list(summary.versions)
    expected_status = "dry_run_completed" if dry_run else "success"
    status_rows = _status_rows_for_versions(db_path, version_ids)
    statuses_ok = bool(status_rows) and all(status == expected_status for _, status in status_rows)

    ath_dims_count = _table_count_for_versions(db_path, "ath_dimensions", version_ids)
    graph_count = _table_count_for_versions(db_path, "graphs", version_ids)
    graph_points_count = _table_count_for_versions(db_path, "graph_points", version_ids)

    cleanup_ok = False
    if dry_run:
        cleanup_ok = bool(summary.cleanup_results) and all(
            result.get("reason") in {"dry_run_no_delete", "ath_export_root_unset"} for result in summary.cleanup_results
        )
    else:
        cfg_rows = [row for row in summary.cleanup_results if str(row.get("artifact")) == "cfg"]
        export_rows = [row for row in summary.cleanup_results if str(row.get("artifact")) == "ath_export_subdir"]
        cfg_ok = bool(cfg_rows) and all(bool(row.get("deleted")) and row.get("reason") == "deleted" for row in cfg_rows)
        export_ok = bool(export_rows) and all(
            row.get("reason") in {"deleted", "target_missing", "ath_export_root_unset"} for row in export_rows
        )
        cleanup_ok = cfg_ok and export_ok

    artifacts_ok = True
    artifact_issues: list[str] = []
    for version_id in version_ids:
        version_dir = project_paths.versions_dir / version_id
        cfg_path = version_dir / "cfg" / "input.cfg"
        logs_dir = version_dir / "logs"
        if not cfg_path.exists():
            artifacts_ok = False
            artifact_issues.append(f"{version_id}: missing cfg file")
        if not logs_dir.exists():
            artifacts_ok = False
            artifact_issues.append(f"{version_id}: missing logs directory")

    checks = [
        {"name": "version_status", "ok": statuses_ok, "detail": f"expected={expected_status}, rows={status_rows}"},
        {"name": "ath_dimensions", "ok": dry_run or ath_dims_count > 0, "detail": f"count={ath_dims_count}"},
        {"name": "graphs", "ok": dry_run or graph_count > 0, "detail": f"count={graph_count}"},
        {"name": "graph_points", "ok": dry_run or graph_points_count > 0, "detail": f"count={graph_points_count}"},
        {"name": "cleanup_guarded", "ok": cleanup_ok, "detail": json.dumps(summary.cleanup_results, ensure_ascii=False)},
        {
            "name": "artifacts_present",
            "ok": artifacts_ok,
            "detail": "; ".join(artifact_issues) if artifact_issues else "cfg/log paths present",
        },
    ]
    ok = all(bool(check["ok"]) for check in checks)
    payload = {
        "ok": ok,
        "mode": "dry-run" if dry_run else "real",
        "project_id": project.project_id,
        "batch_id": batch_id,
        "version_ids": version_ids,
        "tool_paths": tool_paths,
        "tool_ready": tool_ready,
        "checks": checks,
        "runtime_summary": dataclasses.asdict(summary),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default))
    return 0 if ok else 3


def cmd_gui(args: argparse.Namespace) -> int:
    from app.gui import launch_gui

    return int(launch_gui())


def cmd_theme_preview(args: argparse.Namespace) -> int:
    from ui.theme_preview import launch_preview

    return int(launch_preview())


def cmd_compat_verify(args: argparse.Namespace) -> int:
    from app.compat_verification import run_compat_verification

    settings_store = SettingsStore()
    settings = settings_store.load()
    if args.library_root:
        settings = UserSettings(
            library_root=args.library_root,
            ath_exe=settings.ath_exe,
            akabak_exe=settings.akabak_exe,
            vacs_exe=settings.vacs_exe,
            template_cfg=settings.template_cfg,
            background_automation_mode=bool(getattr(settings, "background_automation_mode", True)),
        )
        settings_store.save(settings)
    service = OrchestratorService(settings_store=settings_store)

    project_id = args.project_id or "P_COMPAT"
    project_root = service.repo.project_paths(project_id, ensure=True).project_dir
    ath_exe = args.ath_exe or service.settings.ath_exe
    mode = "full" if args.all_cases else str(args.mode)
    summary = run_compat_verification(
        project_root=project_root,
        project_id=project_id,
        ath_executable=ath_exe,
        ath_base_args=args.ath_base_args or [],
        timeout_s=args.timeout_s,
        gmsh_path=args.gmsh_path,
        persist_sql=not args.no_sql,
        only_hypothesis=bool(args.hypothesis_only),
        mode=mode,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=_json_default))
    return 0 if int(summary["status_counts"].get("fail", 0)) == 0 else 3


def cmd_projectpage_ath_test(args: argparse.Namespace) -> int:
    from app.projectpage_ath_test import run_projectpage_ath_test_suite

    settings_store = SettingsStore()
    settings = settings_store.load()
    summary = run_projectpage_ath_test_suite(
        settings=settings,
        ath_exe=args.ath_exe,
        template_cfg=args.template_cfg,
        cfg_dir=args.cfg_dir,
        export_root=args.export_root,
        reports_root=args.reports_root,
        case_limit=args.count,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=_json_default))
    failed = int(summary.get("status_counts", {}).get("failed", 0))
    return 0 if failed == 0 else 3


def cmd_projectpage_ath_experiment(args: argparse.Namespace) -> int:
    from app.projectpage_ath_experiment import run_projectpage_ath_experiment

    settings_store = SettingsStore()
    settings = settings_store.load()
    aggregate_groups = None
    if args.aggregate_run_groups:
        aggregate_groups = [item.strip() for item in str(args.aggregate_run_groups).split(",") if item.strip()]
    summary = run_projectpage_ath_experiment(
        settings=settings,
        cases=args.cases,
        seed=args.seed,
        run_group=args.run_group,
        ath_exe=args.ath_exe,
        template_cfg=args.template_cfg,
        cfg_dir=args.cfg_dir,
        export_root=args.export_root,
        reports_root=args.reports_root,
        cleanup_files=bool(args.cleanup_files),
        max_dim_mm=args.max_dim_mm,
        hard_cap_mm=args.hard_cap_mm,
        priors_path=args.priors_path,
        commit_every=args.commit_every,
        preclean_files=bool(args.preclean_files),
        cleanup_cases=args.cleanup_cases,
        cleanup_log=args.cleanup_log,
        aggregate_run_groups=aggregate_groups,
        backfill_legacy_null_run_groups=bool(args.backfill_legacy_null_run_groups),
        write_history_snapshots=bool(args.write_history_snapshots),
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=_json_default))
    pipeline_errors = int(summary.get("status_counts", {}).get("pipeline_error", 0))
    return 0 if pipeline_errors == 0 else 3


def cmd_ath_experiments_backfill_subkeys(args: argparse.Namespace) -> int:
    from app.projectpage_ath_experiment import run_ath_experiments_backfill_subkeys

    summary = run_ath_experiments_backfill_subkeys(
        reports_root=args.reports_root,
        run_group=args.run_group,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=_json_default))
    return 0


def cmd_ath_experiments_split_unknown(args: argparse.Namespace) -> int:
    from app.projectpage_ath_experiment import run_ath_experiments_backfill_unknown_split

    summary = run_ath_experiments_backfill_unknown_split(
        reports_root=args.reports_root,
        run_group=args.run_group,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=_json_default))
    return 0


def cmd_ath_experiments_refined_reports(args: argparse.Namespace) -> int:
    from app.projectpage_ath_experiment import run_ath_experiments_refined_reports

    summary = run_ath_experiments_refined_reports(
        reports_root=args.reports_root,
        run_group=args.run_group,
        version_tag=getattr(args, "version_tag", None),
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=_json_default))
    return 0


def cmd_ath_experiments_analyze_compare_mismatch(args: argparse.Namespace) -> int:
    from app.projectpage_ath_experiment import run_ath_experiments_analyze_compare_mismatch

    summary = run_ath_experiments_analyze_compare_mismatch(
        reports_root=args.reports_root,
        run_group=args.run_group,
        limit=args.limit,
        version_tag=getattr(args, "version_tag", None),
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=_json_default))
    return 0


def cmd_ath_experiments_minimal_completion_search(args: argparse.Namespace) -> int:
    from app.minimal_completion_search import run_minimal_completion_search

    settings_store = SettingsStore()
    loaded = settings_store.load()
    settings = UserSettings(
        library_root=loaded.library_root,
        ath_exe=(args.ath_exe or loaded.ath_exe),
        akabak_exe=loaded.akabak_exe,
        vacs_exe=loaded.vacs_exe,
        template_cfg=(args.template_cfg or loaded.template_cfg),
        background_automation_mode=bool(getattr(loaded, "background_automation_mode", True)),
    )
    summary = run_minimal_completion_search(
        settings=settings,
        reports_root=args.reports_root,
        output_root=args.output_root,
        run_group=args.run_group,
        include_all_combinations=bool(args.all_combinations),
        verify_with_ath=bool(args.verify_ath),
        seed_run_limit=args.seed_run_limit,
        max_seed_candidates=args.max_seed_candidates,
        max_eval_per_scenario=args.max_eval_per_scenario,
        scenario_filter=args.scenario_filter,
        mesh_cmd=args.mesh_cmd,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=_json_default))
    return 0


def cmd_ath_experiments_contextual_ranges(args: argparse.Namespace) -> int:
    from app.contextual_range_analysis import run_contextual_range_analysis

    summary = run_contextual_range_analysis(
        reports_root=args.reports_root,
        run_group=args.run_group,
        min_count=args.min_count,
        output_json_name=args.output_json,
        output_md_name=args.output_md,
        max_contexts_per_key=args.max_contexts_per_key,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=_json_default))
    return 0


def _inspect_ui_tool(
    *,
    tool_name: str,
    executable: Optional[str],
    output_dir: str,
    timeout_s: int,
    dry_run: bool,
) -> Dict[str, Any]:
    from app.ui_automation.inspector import inspect_tool_ui

    if not executable:
        raise ValueError(f"{tool_name} executable path is not configured.")
    return inspect_tool_ui(
        tool_name=tool_name,
        executable=executable,
        output_root=output_dir,
        startup_timeout_s=timeout_s,
        dry_run=dry_run,
    )


def cmd_ui_inspect_akabak(args: argparse.Namespace) -> int:
    settings_store = SettingsStore()
    settings = settings_store.load()
    payload = _inspect_ui_tool(
        tool_name="akabak",
        executable=args.akabak_exe or settings.akabak_exe,
        output_dir=args.output_dir,
        timeout_s=args.timeout_s,
        dry_run=args.dry_run,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default))
    return 0 if "error" not in payload else 3


def cmd_ui_inspect_vacs(args: argparse.Namespace) -> int:
    settings_store = SettingsStore()
    settings = settings_store.load()
    payload = _inspect_ui_tool(
        tool_name="vacs",
        executable=args.vacs_exe or settings.vacs_exe,
        output_dir=args.output_dir,
        timeout_s=args.timeout_s,
        dry_run=args.dry_run,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default))
    return 0 if "error" not in payload else 3


def cmd_vacs_discover_graphs(args: argparse.Namespace) -> int:
    from app.ui_automation.inspector import inspect_tool_ui
    from app.ui_automation.recipes import load_vacs_export_recipes
    from app.vacs_graph_catalog import discover_graph_catalog

    settings_store = SettingsStore()
    settings = settings_store.load()
    vacs_exe = args.vacs_exe or settings.vacs_exe
    inspect_summary = None
    if not args.dry_run and vacs_exe:
        inspect_summary = inspect_tool_ui(
            tool_name="vacs",
            executable=vacs_exe,
            output_root=args.ui_maps_output,
            startup_timeout_s=args.timeout_s,
            dry_run=False,
        )

    recipes = load_vacs_export_recipes()
    catalog = discover_graph_catalog(
        vacs_version=args.vacs_version,
        output_root=args.catalog_root,
        recipes=recipes,
        inspect_summary=inspect_summary,
    )
    payload = {
        "ok": True,
        "vacs_version": args.vacs_version,
        "catalog_path": catalog["catalog_path"],
        "entry_count": catalog["entry_count"],
        "inspect_summary": inspect_summary,
        "recipes_loaded": len(recipes),
        "dry_run": bool(args.dry_run),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default))
    return 0


def cmd_ui_discover(args: argparse.Namespace) -> int:
    from app.ui_automation.discover import discover_app_ui

    settings = SettingsStore().load()
    app_name = str(args.app).strip().lower()
    executable = args.exe
    if not executable:
        if app_name == "akabak":
            executable = settings.akabak_exe
        elif app_name == "vacs":
            executable = settings.vacs_exe

    payload = discover_app_ui(
        app=app_name,
        executable=executable,
        pid=args.pid,
        output_root=args.output_dir,
        startup_timeout_s=args.timeout_s,
        max_depth=args.max_depth,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default))
    return 0 if "error" not in payload else 3


def cmd_runner_test_run(args: argparse.Namespace) -> int:
    from app.runner_test_harness import run_runner_test_harness

    settings_store = SettingsStore()
    settings = settings_store.load()
    summary = run_runner_test_harness(
        case_id=args.case_id,
        repeats=args.repeats,
        keep_exports=str(args.keep_exports).strip().lower() == "true",
        test_profile=args.test_profile,
        workspace_root=args.workspace_root,
        cases_root=args.cases_root,
        template_cfg_path=args.template_cfg or settings.template_cfg,
        ath_executable=args.ath_exe or settings.ath_exe,
        akabak_executable=args.akabak_exe or settings.akabak_exe,
        vacs_executable=args.vacs_exe or settings.vacs_exe,
        le_repair_profile=str(args.le_repair_profile or "").strip() or None,
        cfg_le_profile=str(args.cfg_le_profile or "").strip() or None,
        radimp_observation_profile=str(args.radimp_observation_profile or "").strip() or None,
        driving_observation_profile=str(args.driving_observation_profile or "").strip() or None,
        strict_nonzero_radimp=bool(args.strict_nonzero_radimp),
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=_json_default))
    return 0 if summary.get("ok", False) else 4


def cmd_runner_test_open_dialog_only(args: argparse.Namespace) -> int:
    from app.runner_test_harness import run_runner_test_open_dialog_only

    settings_store = SettingsStore()
    settings = settings_store.load()
    executable = args.akabak_exe or settings.akabak_exe
    summary = run_runner_test_open_dialog_only(
        akabak_executable=executable,
        abec_path=args.abec_path,
        repeats=args.repeats,
        workspace_root=args.workspace_root,
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=_json_default))
    return 0 if summary.get("ok", False) else 4


def cmd_runner_test_import_start_apply_only(args: argparse.Namespace) -> int:
    from app.runner_test_harness import run_runner_test_import_start_apply_only

    settings_store = SettingsStore()
    settings = settings_store.load()
    executable = args.akabak_exe or settings.akabak_exe
    summary = run_runner_test_import_start_apply_only(
        akabak_executable=executable,
        abec_path=args.abec_path,
        repeats=args.repeats,
        workspace_root=args.workspace_root,
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=_json_default))
    return 0 if summary.get("ok", False) else 4


def cmd_runner_test_le_repair_import_only(args: argparse.Namespace) -> int:
    from app.runner_test_harness import run_runner_test_le_repair_import_only

    settings_store = SettingsStore()
    settings = settings_store.load()
    executable = args.akabak_exe or settings.akabak_exe
    summary = run_runner_test_le_repair_import_only(
        akabak_executable=executable,
        repeats=args.repeats,
        workspace_root=args.workspace_root,
        dry_run=bool(args.dry_run),
        ath_executable=args.ath_exe or settings.ath_exe,
        ath_cfg_path=args.ath_cfg_path,
        abec_path=args.abec_path,
        reuse_export_dir=args.reuse_export_dir,
        le_repair_profile=str(args.le_repair_profile or "").strip() or None,
        le_driver_tag=str(args.le_driver_tag or "D1"),
        le_drvgroup=str(args.le_drvgroup or "1001"),
        le_voltage_vrms=float(args.le_voltage_vrms),
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=_json_default))
    return 0 if summary.get("ok", False) else 4


def cmd_runner_test_radimp_driving_matrix(args: argparse.Namespace) -> int:
    from app.runner_test_harness import run_runner_test_radimp_driving_matrix

    settings_store = SettingsStore()
    settings = settings_store.load()
    profiles_raw = str(args.profiles or "").strip()
    profiles = [item.strip() for item in profiles_raw.split(",") if item.strip()] if profiles_raw else None
    summary = run_runner_test_radimp_driving_matrix(
        case_id=args.case_id,
        driving_profiles=profiles,
        repeats_per_profile=args.repeats_per_profile,
        keep_exports=str(args.keep_exports).strip().lower() == "true",
        test_profile=args.test_profile,
        workspace_root=args.workspace_root,
        cases_root=args.cases_root,
        template_cfg_path=args.template_cfg or settings.template_cfg,
        ath_executable=args.ath_exe or settings.ath_exe,
        akabak_executable=args.akabak_exe or settings.akabak_exe,
        vacs_executable=args.vacs_exe or settings.vacs_exe,
        le_repair_profile=str(args.le_repair_profile or "").strip() or None,
        radimp_observation_profile=str(args.radimp_observation_profile or "").strip() or None,
        strict_nonzero_radimp=bool(args.strict_nonzero_radimp),
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=_json_default))
    return 0 if summary.get("ok", False) else 4


def cmd_runner_test_radimp_3scope_matrix(args: argparse.Namespace) -> int:
    from app.runner_test_harness import run_runner_test_radimp_3scope_matrix

    settings_store = SettingsStore()
    settings = settings_store.load()
    cfg_profiles_raw = str(args.cfg_profiles or "").strip()
    radimp_profiles_raw = str(args.radimp_profiles or "").strip()
    driving_profiles_raw = str(args.driving_profiles or "").strip()
    cfg_profiles = [item.strip() for item in cfg_profiles_raw.split(",") if item.strip()] if cfg_profiles_raw else None
    radimp_profiles = [item.strip() for item in radimp_profiles_raw.split(",") if item.strip()] if radimp_profiles_raw else None
    driving_profiles = [item.strip() for item in driving_profiles_raw.split(",") if item.strip()] if driving_profiles_raw else None
    summary = run_runner_test_radimp_3scope_matrix(
        case_id=args.case_id,
        cfg_profiles=cfg_profiles,
        radimp_profiles=radimp_profiles,
        driving_profiles=driving_profiles,
        repeats_per_combo=args.repeats_per_combo,
        keep_exports=str(args.keep_exports).strip().lower() == "true",
        test_profile=args.test_profile,
        workspace_root=args.workspace_root,
        cases_root=args.cases_root,
        template_cfg_path=args.template_cfg or settings.template_cfg,
        ath_executable=args.ath_exe or settings.ath_exe,
        akabak_executable=args.akabak_exe or settings.akabak_exe,
        vacs_executable=args.vacs_exe or settings.vacs_exe,
        le_repair_profile=str(args.le_repair_profile or "").strip() or None,
        strict_nonzero_radimp=bool(args.strict_nonzero_radimp),
        randomize_order=not bool(args.no_randomize_order),
        random_seed=int(args.matrix_seed),
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=_json_default))
    return 0 if summary.get("ok", False) else 4


def cmd_runner_test_le_proof_matrix(args: argparse.Namespace) -> int:
    from app.runner_test_harness import run_runner_test_le_proof_matrix

    settings_store = SettingsStore()
    settings = settings_store.load()
    profiles_raw = str(args.profiles or "").strip()
    profiles = [item.strip() for item in profiles_raw.split(",") if item.strip()] if profiles_raw else None
    summary = run_runner_test_le_proof_matrix(
        case_id=args.case_id,
        profiles=profiles,
        repeats_per_profile=args.repeats_per_profile,
        keep_exports=str(args.keep_exports).strip().lower() == "true",
        test_profile=args.test_profile,
        workspace_root=args.workspace_root,
        cases_root=args.cases_root,
        template_cfg_path=args.template_cfg or settings.template_cfg,
        ath_executable=args.ath_exe or settings.ath_exe,
        akabak_executable=args.akabak_exe or settings.akabak_exe,
        vacs_executable=args.vacs_exe or settings.vacs_exe,
        cfg_le_profile=str(args.cfg_le_profile or "").strip() or None,
        radimp_observation_profile=str(args.radimp_observation_profile or "").strip() or None,
        driving_observation_profile=str(args.driving_observation_profile or "").strip() or None,
        strict_le_proof=bool(args.strict_le_proof),
        randomize_order=not bool(args.no_randomize_order),
        random_seed=int(args.matrix_seed),
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=_json_default))
    return 0 if summary.get("ok", False) else 4


def _resolve_run_project_id(service: OrchestratorService, run_id: str, project_id: Optional[str]) -> str:
    if project_id:
        return project_id
    matches: list[str] = []
    for project in service.list_projects():
        rows = service.list_runs(project_id=project.project_id)
        if any(str(row.get("run_id")) == run_id for row in rows):
            matches.append(project.project_id)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"Run not found in library: {run_id}")
    raise ValueError(f"Run id is ambiguous across projects; pass --project-id explicitly: {run_id}")


def cmd_runs_pin(args: argparse.Namespace) -> int:
    service = OrchestratorService(settings_store=SettingsStore())
    project_id = _resolve_run_project_id(service, args.run_id, args.project_id)
    result = service.pin_run(project_id=project_id, run_id=args.run_id, tag=args.tag)
    print(json.dumps({"ok": True, "project_id": project_id, "run_id": args.run_id, **result}, indent=2, ensure_ascii=False))
    return 0


def cmd_runs_unpin(args: argparse.Namespace) -> int:
    service = OrchestratorService(settings_store=SettingsStore())
    project_id = _resolve_run_project_id(service, args.run_id, args.project_id)
    result = service.unpin_run(project_id=project_id, run_id=args.run_id)
    print(json.dumps({"ok": True, "project_id": project_id, "run_id": args.run_id, **result}, indent=2, ensure_ascii=False))
    return 0


def cmd_runs_cleanup_testdata(args: argparse.Namespace) -> int:
    service = OrchestratorService(settings_store=SettingsStore())
    if args.project_id:
        projects = [args.project_id]
    else:
        projects = [project.project_id for project in service.list_projects()]

    project_results: list[dict[str, Any]] = []
    aggregate = {
        "runs": 0,
        "run_versions": 0,
        "ath_dimensions": 0,
        "graphs": 0,
        "graph_series": 0,
        "graph_points": 0,
        "files": 0,
    }
    for project_id in projects:
        result = service.cleanup_test_data(
            project_id=project_id,
            delete_exports=bool(args.delete_exports),
            dry_run=bool(args.dry_run),
        )
        project_results.append(result)
        counts = dict(result.get("counts", {}) or {})
        for key in aggregate:
            aggregate[key] += int(counts.get(key, 0))

    payload = {
        "ok": True,
        "dry_run": bool(args.dry_run),
        "delete_exports": bool(args.delete_exports),
        "project_count": len(project_results),
        "aggregate_counts": aggregate,
        "projects": project_results,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="batch-software")
    parser.add_argument("--config", default="app_config.json", help="Path to app_config.json (optional).")

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_doctor = sub.add_parser("doctor", help="Run environment checks.")
    p_doctor.add_argument("--fix", action="store_true", help="Attempt non-destructive fixes (mkdir/write tests).")
    p_doctor.add_argument("--kill-zombies", action="store_true", help="Attempt to kill stuck tool processes.")
    p_doctor.add_argument("--report-path", help="Write doctor_report.json to this path.")
    p_doctor.set_defaults(func=cmd_doctor)

    p_batch = sub.add_parser("batch", help="Batch utilities.")
    sub_batch = p_batch.add_subparsers(dest="batch_cmd", required=True)

    p_job = sub_batch.add_parser("job-count", help="Compute job_count from batch.json + constraints.json.")
    p_job.add_argument("--batch-json", required=True, help="Path to batch.json")
    p_job.add_argument("--constraints-json", help="Path to constraints.json (optional)")
    p_job.set_defaults(func=cmd_batch_job_count)

    p_dataset = sub.add_parser("dataset", help="Dataset import utilities.")
    sub_dataset = p_dataset.add_subparsers(dest="dataset_cmd", required=True)

    p_build = sub_dataset.add_parser("build", help="(Re)build dataset.sqlite from Result_*.txt files.")
    p_build.add_argument("--project-id", required=True, help="Project id like P001")
    p_build.add_argument("--manifest-path", help="Override dataset_manifest.json path")
    p_build.set_defaults(func=lambda a: cmd_dataset_build_or_update(a, rebuild=True))

    p_update = sub_dataset.add_parser("update", help="Incrementally update dataset.sqlite based on manifest.")
    p_update.add_argument("--project-id", required=True, help="Project id like P001")
    p_update.add_argument("--manifest-path", help="Override dataset_manifest.json path")
    p_update.set_defaults(func=lambda a: cmd_dataset_build_or_update(a, rebuild=False))

    p_sync = sub_dataset.add_parser("sync-global", help="Replay pending project DB writes into global.sqlite.")
    p_sync.add_argument("--library-root", help="Override library root containing project folders")
    p_sync.add_argument("--max-items-per-project", type=int, default=100, help="Retry limit per project")
    p_sync.set_defaults(func=cmd_dataset_sync_global)

    p_plan = sub.add_parser("plan", help="Project/batch planning utilities.")
    sub_plan = p_plan.add_subparsers(dest="plan_cmd", required=True)

    p_materialize = sub_plan.add_parser(
        "materialize",
        help="Resolve versions from project+batch and materialize project structure and tidy metadata outputs.",
    )
    p_materialize.add_argument("--project-json", required=True, help="Path to project.json payload")
    p_materialize.add_argument("--batch-json", required=True, help="Path to batch.json payload")
    p_materialize.add_argument(
        "--projects-root",
        help="Root folder that contains project folders in format projects/<project_id>/...",
    )
    p_materialize.set_defaults(func=cmd_plan_materialize)

    p_run = sub.add_parser("run", help="Runtime execution utilities.")
    sub_run = p_run.add_subparsers(dest="run_cmd", required=True)

    p_pipeline = sub_run.add_parser(
        "pipeline",
        help="Run staged ATH->AKABAK->VACS pipeline for all resolved versions.",
    )
    p_pipeline.add_argument("--project-json", required=True, help="Path to project.json payload")
    p_pipeline.add_argument("--batch-json", required=True, help="Path to batch.json payload")
    p_pipeline.add_argument("--projects-root", help="Root folder for projects/<project_id>/...")
    p_pipeline.add_argument("--template-cfg", help="Path to ATH template CFG file")
    p_pipeline.add_argument("--ath-exe", help="ATH executable path")
    p_pipeline.add_argument("--akabak-exe", help="AKABAK executable path")
    p_pipeline.add_argument("--vacs-exe", help="VACS executable path")
    p_pipeline.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue with next stages/versions when a stage fails.",
    )
    p_pipeline.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip external tool invocations and run deterministic CFG/SQL/cleanup-guard flow only.",
    )
    p_pipeline.set_defaults(func=cmd_run_pipeline)

    p_gui = sub.add_parser("gui", help="Launch PySide6 orchestrator GUI.")
    p_gui.set_defaults(func=cmd_gui)

    p_sample = sub.add_parser("run-sample", help="Run and verify a minimal one-version sample batch.")
    p_sample.add_argument("--project-id", help="Use existing project id. If omitted, create a sample project.")
    p_sample.add_argument("--batch-id", help="Use existing batch id. If omitted, create a sample batch.")
    p_sample.add_argument("--project-name", default="Sample Project", help="Name for auto-created sample project")
    p_sample.add_argument("--batch-name", default="Sample Batch", help="Name for auto-created sample batch")
    p_sample.add_argument("--library-root", help="Override library root in settings before run")
    mode_group = p_sample.add_mutually_exclusive_group()
    mode_group.add_argument("--real", action="store_true", help="Require real ATH/AKABAK/VACS execution")
    mode_group.add_argument("--dry-run", action="store_true", help="Force deterministic dry-run")
    p_sample.set_defaults(func=cmd_run_sample)

    p_runner_test = sub.add_parser("runner-test", help="Isolated runner test harness.")
    sub_runner_test = p_runner_test.add_subparsers(dest="runner_test_cmd", required=True)

    p_runner_test_run = sub_runner_test.add_parser(
        "run",
        help="Run isolated harness case (ATH -> AKABAK -> VACS) with persistent Runner_Test DB logging.",
    )
    p_runner_test_run.add_argument("--case", dest="case_id", required=True, help="Runner test case id")
    p_runner_test_run.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="Sequential repetitions for flake detection",
    )
    p_runner_test_run.add_argument(
        "--keep-exports",
        default="true",
        choices=["true", "false"],
        help="Retain exported TXT artifacts (reserved for full E2E mode).",
    )
    p_runner_test_run.add_argument(
        "--test-profile",
        default="fast",
        help="Test profile id (reserved for full E2E mode).",
    )
    p_runner_test_run.add_argument(
        "--le-repair-profile",
        default=None,
        help=(
            "Optional LE script patch profile for post-ATH repair "
            "(baseline, driver_drvgroup, driver_drvgroup_def_driving, "
            "driver_drvgroup_def_driving_resistor, mut_electrical, mut_motor)."
        ),
    )
    p_runner_test_run.add_argument(
        "--cfg-le-profile",
        default=None,
        help=(
            "Optional harness-only CFG LE profile "
            "(default, le_voltage_2p83, le_voltage_10, le_voltage_0p1)."
        ),
    )
    p_runner_test_run.add_argument(
        "--radimp-observation-profile",
        default=None,
        help=(
            "Optional observation patch profile for RadImp experiment "
            "(default, force_absolute, drop_radimptype)."
        ),
    )
    p_runner_test_run.add_argument(
        "--driving-observation-profile",
        default=None,
        help=(
            "Optional Driving_Values patch profile "
            "(default, accel_2p83, accel_10, velocity_1, displacement_1)."
        ),
    )
    p_runner_test_run.add_argument(
        "--strict-nonzero-radimp",
        action="store_true",
        help="Fail run unless RadImp diagnosis is explicitly non-zero.",
    )
    p_runner_test_run.add_argument(
        "--workspace-root",
        default="runner_test_workspace",
        help="Dedicated workspace root for harness artifacts.",
    )
    p_runner_test_run.add_argument(
        "--cases-root",
        default="runner_test_cases",
        help="Directory containing runner test case JSON files.",
    )
    p_runner_test_run.add_argument("--template-cfg", help="Override template CFG path used for case rendering.")
    p_runner_test_run.add_argument("--ath-exe", help="Override ATH executable path")
    p_runner_test_run.add_argument("--akabak-exe", help="Override AKABAK executable path")
    p_runner_test_run.add_argument("--vacs-exe", help="Override VACS executable path")
    p_runner_test_run.add_argument(
        "--dry-run",
        action="store_true",
        help="Stop before launching ATH/AKABAK/VACS (CFG+DB+cleanup wiring only).",
    )
    p_runner_test_run.set_defaults(func=cmd_runner_test_run)

    p_runner_test_matrix = sub_runner_test.add_parser(
        "radimp-driving-matrix",
        help="Run a controlled Driving_Values/DrvType hypothesis matrix for RadImp diagnostics.",
    )
    p_runner_test_matrix.add_argument("--case", dest="case_id", default="test_cfg_baseline", help="Runner test case id.")
    p_runner_test_matrix.add_argument(
        "--profiles",
        default="default,accel_2p83,accel_10,velocity_1,displacement_1",
        help="Comma-separated driving observation profiles to execute in order.",
    )
    p_runner_test_matrix.add_argument(
        "--repeats-per-profile",
        type=int,
        default=1,
        help="Sequential repeats for each driving profile.",
    )
    p_runner_test_matrix.add_argument(
        "--keep-exports",
        default="true",
        choices=["true", "false"],
        help="Retain exported TXT artifacts for each matrix profile.",
    )
    p_runner_test_matrix.add_argument(
        "--test-profile",
        default="fast",
        help="Harness test profile id.",
    )
    p_runner_test_matrix.add_argument(
        "--workspace-root",
        default="runner_test_workspace",
        help="Dedicated workspace root for harness artifacts.",
    )
    p_runner_test_matrix.add_argument(
        "--cases-root",
        default="runner_test_cases",
        help="Directory containing runner test case JSON files.",
    )
    p_runner_test_matrix.add_argument("--template-cfg", help="Override template CFG path used for case rendering.")
    p_runner_test_matrix.add_argument("--ath-exe", help="Override ATH executable path")
    p_runner_test_matrix.add_argument("--akabak-exe", help="Override AKABAK executable path")
    p_runner_test_matrix.add_argument("--vacs-exe", help="Override VACS executable path")
    p_runner_test_matrix.add_argument(
        "--le-repair-profile",
        default=None,
        help=(
            "Optional LE script patch profile "
            "(baseline, driver_drvgroup, driver_drvgroup_def_driving, "
            "driver_drvgroup_def_driving_resistor, mut_electrical, mut_motor)."
        ),
    )
    p_runner_test_matrix.add_argument(
        "--radimp-observation-profile",
        default=None,
        help="Optional RadImp observation patch profile (default, force_absolute, drop_radimptype).",
    )
    p_runner_test_matrix.add_argument(
        "--strict-nonzero-radimp",
        action="store_true",
        help="Fail each profile run unless RadImp diagnosis is explicitly non-zero.",
    )
    p_runner_test_matrix.add_argument(
        "--dry-run",
        action="store_true",
        help="Run matrix in dry-run mode (no tool launch).",
    )
    p_runner_test_matrix.set_defaults(func=cmd_runner_test_radimp_driving_matrix)

    p_runner_test_3scope = sub_runner_test.add_parser(
        "radimp-3scope-matrix",
        help="Run combined CFG/observation/driving hypothesis matrix for RadImp diagnostics.",
    )
    p_runner_test_3scope.add_argument("--case", dest="case_id", default="test_cfg_baseline", help="Runner test case id.")
    p_runner_test_3scope.add_argument(
        "--cfg-profiles",
        default="default,le_voltage_2p83,le_voltage_10",
        help="Comma-separated harness-only cfg LE profiles.",
    )
    p_runner_test_3scope.add_argument(
        "--radimp-profiles",
        default="default,force_absolute",
        help="Comma-separated RadImp observation patch profiles.",
    )
    p_runner_test_3scope.add_argument(
        "--driving-profiles",
        default="default,accel_2p83",
        help="Comma-separated Driving_Values patch profiles.",
    )
    p_runner_test_3scope.add_argument(
        "--repeats-per-combo",
        type=int,
        default=1,
        help="Sequential repeats for each matrix combination.",
    )
    p_runner_test_3scope.add_argument(
        "--keep-exports",
        default="true",
        choices=["true", "false"],
        help="Retain exported TXT artifacts for each matrix combination.",
    )
    p_runner_test_3scope.add_argument(
        "--test-profile",
        default="fast",
        help="Harness test profile id.",
    )
    p_runner_test_3scope.add_argument(
        "--workspace-root",
        default="runner_test_workspace",
        help="Dedicated workspace root for harness artifacts.",
    )
    p_runner_test_3scope.add_argument(
        "--cases-root",
        default="runner_test_cases",
        help="Directory containing runner test case JSON files.",
    )
    p_runner_test_3scope.add_argument("--template-cfg", help="Override template CFG path used for case rendering.")
    p_runner_test_3scope.add_argument("--ath-exe", help="Override ATH executable path")
    p_runner_test_3scope.add_argument("--akabak-exe", help="Override AKABAK executable path")
    p_runner_test_3scope.add_argument("--vacs-exe", help="Override VACS executable path")
    p_runner_test_3scope.add_argument(
        "--le-repair-profile",
        default=None,
        help=(
            "Optional LE script patch profile "
            "(baseline, driver_drvgroup, driver_drvgroup_def_driving, "
            "driver_drvgroup_def_driving_resistor, mut_electrical, mut_motor)."
        ),
    )
    p_runner_test_3scope.add_argument(
        "--strict-nonzero-radimp",
        action="store_true",
        help="Fail each matrix combination unless RadImp diagnosis is explicitly non-zero.",
    )
    p_runner_test_3scope.add_argument(
        "--no-randomize-order",
        action="store_true",
        help="Execute matrix combinations in deterministic nested order (default: randomized with seed).",
    )
    p_runner_test_3scope.add_argument(
        "--matrix-seed",
        type=int,
        default=1337,
        help="Seed used for randomized matrix combination order.",
    )
    p_runner_test_3scope.add_argument(
        "--dry-run",
        action="store_true",
        help="Run matrix in dry-run mode (no tool launch).",
    )
    p_runner_test_3scope.set_defaults(func=cmd_runner_test_radimp_3scope_matrix)

    p_runner_test_le_proof = sub_runner_test.add_parser(
        "le-proof-matrix",
        help="Run composite LE integration proof matrix (control vs mutation profiles).",
    )
    p_runner_test_le_proof.add_argument("--case", dest="case_id", default="test_cfg_baseline", help="Runner test case id.")
    p_runner_test_le_proof.add_argument(
        "--profiles",
        default="control,mut_electrical,mut_motor",
        help="Comma-separated LE proof profiles (control, mut_electrical, mut_motor).",
    )
    p_runner_test_le_proof.add_argument(
        "--repeats-per-profile",
        type=int,
        default=3,
        help="Sequential repeats for each LE proof profile.",
    )
    p_runner_test_le_proof.add_argument(
        "--keep-exports",
        default="true",
        choices=["true", "false"],
        help="Retain exported TXT artifacts for each LE proof run.",
    )
    p_runner_test_le_proof.add_argument(
        "--test-profile",
        default="fast",
        help="Harness test profile id.",
    )
    p_runner_test_le_proof.add_argument(
        "--workspace-root",
        default="runner_test_workspace",
        help="Dedicated workspace root for harness artifacts.",
    )
    p_runner_test_le_proof.add_argument(
        "--cases-root",
        default="runner_test_cases",
        help="Directory containing runner test case JSON files.",
    )
    p_runner_test_le_proof.add_argument("--template-cfg", help="Override template CFG path used for case rendering.")
    p_runner_test_le_proof.add_argument("--ath-exe", help="Override ATH executable path")
    p_runner_test_le_proof.add_argument("--akabak-exe", help="Override AKABAK executable path")
    p_runner_test_le_proof.add_argument("--vacs-exe", help="Override VACS executable path")
    p_runner_test_le_proof.add_argument(
        "--cfg-le-profile",
        default=None,
        help="Optional harness-only cfg LE profile (default, le_voltage_2p83, le_voltage_10, le_voltage_0p1).",
    )
    p_runner_test_le_proof.add_argument(
        "--radimp-observation-profile",
        default=None,
        help="Optional RadImp observation patch profile (default, force_absolute, drop_radimptype).",
    )
    p_runner_test_le_proof.add_argument(
        "--driving-observation-profile",
        default=None,
        help="Optional Driving_Values patch profile (default, accel_2p83, accel_10, velocity_1, displacement_1).",
    )
    p_runner_test_le_proof.add_argument(
        "--strict-le-proof",
        action="store_true",
        help="Fail unless the composite LE diagnosis is explicitly le_active_confirmed.",
    )
    p_runner_test_le_proof.add_argument(
        "--no-randomize-order",
        action="store_true",
        help="Execute LE proof runs in profile-major order (default: randomized with seed).",
    )
    p_runner_test_le_proof.add_argument(
        "--matrix-seed",
        type=int,
        default=1337,
        help="Seed used for randomized LE proof run order.",
    )
    p_runner_test_le_proof.add_argument(
        "--dry-run",
        action="store_true",
        help="Run matrix in dry-run mode (no tool launch).",
    )
    p_runner_test_le_proof.set_defaults(func=cmd_runner_test_le_proof_matrix)

    p_runner_test_open_dialog_only = sub_runner_test.add_parser(
        "open-dialog-only",
        help="Micro-harness for AKABAK open-file dialog determinism (start -> open dialog -> set path -> confirm -> close).",
    )
    p_runner_test_open_dialog_only.add_argument("--abec-path", required=True, help="ABEC file to open in AKABAK.")
    p_runner_test_open_dialog_only.add_argument("--akabak-exe", help="Override AKABAK executable path")
    p_runner_test_open_dialog_only.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="Sequential repetitions for dialog flake detection.",
    )
    p_runner_test_open_dialog_only.add_argument(
        "--workspace-root",
        default="runner_test_workspace",
        help="Dedicated workspace root for harness artifacts.",
    )
    p_runner_test_open_dialog_only.add_argument(
        "--dry-run",
        action="store_true",
        help="Write DB telemetry without launching AKABAK.",
    )
    p_runner_test_open_dialog_only.set_defaults(func=cmd_runner_test_open_dialog_only)

    p_runner_test_import_apply_only = sub_runner_test.add_parser(
        "import-start-apply-only",
        help="Micro-harness for AKABAK interpreter flow (open project -> Start Importing -> Apply -> verify postcondition).",
    )
    p_runner_test_import_apply_only.add_argument("--abec-path", required=True, help="ABEC file to import in AKABAK.")
    p_runner_test_import_apply_only.add_argument("--akabak-exe", help="Override AKABAK executable path")
    p_runner_test_import_apply_only.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="Sequential repetitions for import-start-apply flake detection.",
    )
    p_runner_test_import_apply_only.add_argument(
        "--workspace-root",
        default="runner_test_workspace",
        help="Dedicated workspace root for harness artifacts.",
    )
    p_runner_test_import_apply_only.add_argument(
        "--dry-run",
        action="store_true",
        help="Write DB telemetry without launching AKABAK.",
    )
    p_runner_test_import_apply_only.set_defaults(func=cmd_runner_test_import_start_apply_only)

    p_runner_test_le_repair_import_only = sub_runner_test.add_parser(
        "le-repair-import-only",
        help=(
            "Micro-harness for post-ATH LE repair + AKABAK import flow "
            "(copy generic25, patch Project.abec LESCript, Start Importing -> Apply)."
        ),
    )
    p_runner_test_le_repair_import_only.add_argument("--akabak-exe", help="Override AKABAK executable path")
    p_runner_test_le_repair_import_only.add_argument("--ath-exe", help="Optional ATH executable path")
    p_runner_test_le_repair_import_only.add_argument(
        "--ath-cfg-path",
        help="Optional ATH cfg to run before repair/import. If omitted, --abec-path or --reuse-export-dir is used.",
    )
    p_runner_test_le_repair_import_only.add_argument(
        "--abec-path",
        help="Optional existing ABEC project file to repair/import (used when --ath-cfg-path is omitted).",
    )
    p_runner_test_le_repair_import_only.add_argument(
        "--reuse-export-dir",
        help="Optional existing ATH export root to scan for Project.abec (alternative to --abec-path).",
    )
    p_runner_test_le_repair_import_only.add_argument(
        "--le-repair-profile",
        default=None,
        help=(
            "Optional LE script patch profile "
            "(baseline, driver_drvgroup, driver_drvgroup_def_driving, driver_drvgroup_def_driving_resistor)."
        ),
    )
    p_runner_test_le_repair_import_only.add_argument(
        "--le-driver-tag",
        default="D1",
        help="LE driver tag used when applying LE profile patches.",
    )
    p_runner_test_le_repair_import_only.add_argument(
        "--le-drvgroup",
        default="1001",
        help="DrvGroup value used for LE profile patches.",
    )
    p_runner_test_le_repair_import_only.add_argument(
        "--le-voltage-vrms",
        type=float,
        default=1.0,
        help="Voltage value used when profile inserts Def_Driving.",
    )
    p_runner_test_le_repair_import_only.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="Sequential repetitions for flake detection.",
    )
    p_runner_test_le_repair_import_only.add_argument(
        "--workspace-root",
        default="runner_test_workspace",
        help="Dedicated workspace root for harness artifacts.",
    )
    p_runner_test_le_repair_import_only.add_argument(
        "--dry-run",
        action="store_true",
        help="Write DB telemetry without launching ATH/AKABAK.",
    )
    p_runner_test_le_repair_import_only.set_defaults(func=cmd_runner_test_le_repair_import_only)

    p_theme = sub.add_parser("theme", help="Theme tooling.")
    sub_theme = p_theme.add_subparsers(dest="theme_cmd", required=True)

    p_theme_preview = sub_theme.add_parser("preview", help="Open visual preview window for current theme.")
    p_theme_preview.set_defaults(func=cmd_theme_preview)

    p_compat = sub.add_parser("compat", help="Compatibility verification tooling.")
    sub_compat = p_compat.add_subparsers(dest="compat_cmd", required=True)

    p_compat_verify = sub_compat.add_parser(
        "verify",
        help="Run semantic fact verification harness and persist results.",
    )
    p_compat_verify.add_argument("--project-id", help="Project id for storing verification results")
    p_compat_verify.add_argument("--library-root", help="Override library root in settings")
    p_compat_verify.add_argument("--ath-exe", help="Override ATH executable path")
    p_compat_verify.add_argument(
        "--ath-base-args",
        nargs="*",
        help="Optional ATH command prefix args (useful for harness stubs).",
    )
    p_compat_verify.add_argument("--gmsh-path", help="Optional gmsh directory prepended to PATH for ATH runs")
    p_compat_verify.add_argument("--timeout-s", type=int, default=120, help="Per-case timeout in seconds")
    p_compat_verify.add_argument(
        "--mode",
        choices=["quick", "full"],
        default="quick",
        help="Verification profile: quick (5-10 deterministic cases) or full (all cases).",
    )
    p_compat_verify.add_argument("--all-cases", action="store_true", help="Legacy alias for --mode full")
    p_compat_verify.add_argument(
        "--hypothesis-only",
        action="store_true",
        help="Skip cases that already have ath_doc evidence and run only hypothesis-backed facts.",
    )
    p_compat_verify.add_argument("--no-sql", action="store_true", help="Disable SQL persistence for results")
    p_compat_verify.set_defaults(func=cmd_compat_verify)

    p_projectpage_ath = sub.add_parser(
        "projectpage-ath-test",
        help="Run isolated PROJECT-page -> CFG -> ATH -> export config consistency tests.",
    )
    p_projectpage_ath.add_argument("--ath-exe", help="Override ATH executable path")
    p_projectpage_ath.add_argument("--template-cfg", help="Override template CFG path")
    p_projectpage_ath.add_argument(
        "--cfg-dir",
        default=r"C:\Tools\ATH",
        help="Directory for generated ProjectPageATHTestN.cfg files",
    )
    p_projectpage_ath.add_argument(
        "--export-root",
        default=r"C:\Horns",
        help="ATH export root directory to monitor",
    )
    p_projectpage_ath.add_argument(
        "--reports-root",
        default="reports/projectpage_ath_test",
        help="Directory for per-case JSON reports and summary",
    )
    p_projectpage_ath.add_argument(
        "--count",
        type=int,
        help="Optional max number of autonomous cases to execute",
    )
    p_projectpage_ath.set_defaults(func=cmd_projectpage_ath_test)

    p_projectpage_ath_exp = sub.add_parser(
        "projectpage-ath-experiment",
        help="Run large PROJECT-page ATH experiment suite and persist reports.",
    )
    p_projectpage_ath_exp.add_argument("--ath-exe", help="Override ATH executable path")
    p_projectpage_ath_exp.add_argument("--template-cfg", help="Override template CFG path")
    p_projectpage_ath_exp.add_argument("--cases", type=int, default=500, help="Number of experiment cases to execute")
    p_projectpage_ath_exp.add_argument("--seed", type=int, default=1337, help="Seed for deterministic case generation")
    p_projectpage_ath_exp.add_argument("--run-group", help="Optional run_group identifier for resume and grouped analysis")
    p_projectpage_ath_exp.add_argument(
        "--aggregate-run-groups",
        help="Comma-separated run_group ids for aggregate summary/range analysis from SQLite.",
    )
    p_projectpage_ath_exp.add_argument(
        "--cfg-dir",
        default=r"C:\Tools\ATH",
        help="Directory for generated ProjectPageATHTestN.cfg files",
    )
    p_projectpage_ath_exp.add_argument(
        "--export-root",
        default=r"C:\Horns",
        help="ATH export root directory to monitor",
    )
    p_projectpage_ath_exp.add_argument(
        "--reports-root",
        default="reports/ath_experiments",
        help="Directory for experiment reports, logs, and summaries",
    )
    p_projectpage_ath_exp.add_argument(
        "--cleanup-files",
        default="true",
        choices=["true", "false"],
        help="Delete generated CFG + ATH export folders after each run (true|false).",
    )
    p_projectpage_ath_exp.add_argument(
        "--max-dim-mm",
        type=float,
        default=2000.0,
        help="Soft dimension threshold in mm for exploratory generation.",
    )
    p_projectpage_ath_exp.add_argument(
        "--hard-cap-mm",
        type=float,
        default=5000.0,
        help="Hard input cap in mm; extreme cases are skipped pre-run.",
    )
    p_projectpage_ath_exp.add_argument(
        "--priors-path",
        help="Optional range_suggestions JSON path used as sampling priors.",
    )
    p_projectpage_ath_exp.add_argument(
        "--commit-every",
        type=int,
        default=25,
        help="SQLite commit interval (number of runs per transaction).",
    )
    p_projectpage_ath_exp.add_argument(
        "--preclean-files",
        default="false",
        choices=["true", "false"],
        help="Before run, safely clear only reports/ath_experiments/{cases,log}.",
    )
    p_projectpage_ath_exp.add_argument(
        "--cleanup-cases",
        default="never",
        choices=["end", "always", "never"],
        help="Cleanup policy for reports/ath_experiments/cases files.",
    )
    p_projectpage_ath_exp.add_argument(
        "--cleanup-log",
        default="never",
        choices=["end", "always", "never"],
        help="Cleanup policy for reports/ath_experiments/log files.",
    )
    p_projectpage_ath_exp.add_argument(
        "--backfill-legacy-null-run-groups",
        default="false",
        choices=["true", "false"],
        help="Idempotently migrate legacy NULL run_group rows to stable legacy_* group ids.",
    )
    p_projectpage_ath_exp.add_argument(
        "--history-snapshots",
        default="true",
        choices=["true", "false"],
        help="Write timestamped snapshots to reports/ath_experiments/history on each aggregation run.",
    )
    p_projectpage_ath_exp.set_defaults(
        func=lambda a: cmd_projectpage_ath_experiment(
            argparse.Namespace(
                **{
                    **vars(a),
                    "cleanup_files": str(getattr(a, "cleanup_files", "true")).strip().lower() == "true",
                    "preclean_files": str(getattr(a, "preclean_files", "false")).strip().lower() == "true",
                    "backfill_legacy_null_run_groups": str(
                        getattr(a, "backfill_legacy_null_run_groups", "false")
                    ).strip().lower()
                    == "true",
                    "write_history_snapshots": str(getattr(a, "history_snapshots", "true")).strip().lower()
                    == "true",
                }
            )
        )
    )

    p_ath_exp_admin = sub.add_parser("ath-experiments", help="ATH experiment DB maintenance utilities.")
    sub_ath_exp_admin = p_ath_exp_admin.add_subparsers(dest="ath_exp_cmd", required=True)

    p_backfill_subkeys = sub_ath_exp_admin.add_parser(
        "backfill-subkeys",
        help="Backfill flattened subkeys for object/list experiment params (idempotent).",
    )
    p_backfill_subkeys.add_argument(
        "--reports-root",
        default="reports/ath_experiments",
        help="ATH experiment reports root containing ath_experiments.sqlite",
    )
    p_backfill_subkeys.add_argument(
        "--run-group",
        default="all",
        help="Run-group selector (single, comma-separated, or 'all').",
    )
    p_backfill_subkeys.set_defaults(func=cmd_ath_experiments_backfill_subkeys)

    p_split_unknown = sub_ath_exp_admin.add_parser(
        "split-unknown",
        help="Refine unknown ATH errors into compare_mismatch_exit0 or ath_runtime_unknown.",
    )
    p_split_unknown.add_argument(
        "--reports-root",
        default="reports/ath_experiments",
        help="ATH experiment reports root containing ath_experiments.sqlite",
    )
    p_split_unknown.add_argument(
        "--run-group",
        default="all",
        help="Run-group selector (single, comma-separated, or 'all').",
    )
    p_split_unknown.set_defaults(func=cmd_ath_experiments_split_unknown)

    p_refined_reports = sub_ath_exp_admin.add_parser(
        "refined-reports",
        help="Write timestamped refined summary and mode error matrix using refined error patterns.",
    )
    p_refined_reports.add_argument(
        "--reports-root",
        default="reports/ath_experiments",
        help="ATH experiment reports root containing ath_experiments.sqlite",
    )
    p_refined_reports.add_argument(
        "--run-group",
        default="all",
        help="Run-group selector (single, comma-separated, or 'all').",
    )
    p_refined_reports.add_argument(
        "--version-tag",
        help="Optional tag inserted into filenames (e.g. v2).",
    )
    p_refined_reports.set_defaults(func=cmd_ath_experiments_refined_reports)

    p_analyze_compare = sub_ath_exp_admin.add_parser(
        "analyze-compare-mismatch",
        help="Classify and inventory compare_mismatch_exit0 rows with key-level breakdown.",
    )
    p_analyze_compare.add_argument(
        "--reports-root",
        default="reports/ath_experiments",
        help="ATH experiment reports root containing ath_experiments.sqlite",
    )
    p_analyze_compare.add_argument(
        "--run-group",
        default="all",
        help="Run-group selector (single, comma-separated, or 'all').",
    )
    p_analyze_compare.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Sample size for run-level excerpts.",
    )
    p_analyze_compare.add_argument(
        "--version-tag",
        help="Optional tag inserted into filenames (e.g. v2).",
    )
    p_analyze_compare.set_defaults(func=cmd_ath_experiments_analyze_compare_mismatch)

    p_min_completion = sub_ath_exp_admin.add_parser(
        "minimal-completion-search",
        help=(
            "Search minimal per-card parameter configurations (minXY) for STL-feasible ATH geometry "
            "using experiment DB seeds and optional ATH verification."
        ),
    )
    p_min_completion.add_argument(
        "--reports-root",
        default="reports/ath_experiments",
        help="ATH experiment reports root containing ath_experiments.sqlite",
    )
    p_min_completion.add_argument(
        "--output-root",
        default="reports/minimal_completion",
        help="Output folder for summary JSON/Markdown and oracle cache",
    )
    p_min_completion.add_argument(
        "--run-group",
        default="all",
        help="Run-group selector (single, comma-separated, or 'all') for seed extraction",
    )
    p_min_completion.add_argument(
        "--seed-run-limit",
        type=int,
        default=20000,
        help="Maximum number of successful runs loaded as seed pool from ath_experiments.sqlite",
    )
    p_min_completion.add_argument(
        "--max-seed-candidates",
        type=int,
        default=12,
        help="Maximum seed candidates retained per scenario before minimization",
    )
    p_min_completion.add_argument(
        "--max-eval-per-scenario",
        type=int,
        default=250,
        help="ATH oracle evaluation budget per scenario (used only with --verify-ath)",
    )
    p_min_completion.add_argument(
        "--scenario-filter",
        default="",
        help="Optional substring filter for scenario_id (e.g. s2_profile1 or s6_profile2).",
    )
    p_min_completion.add_argument(
        "--mesh-cmd",
        default="",
        help="Optional override for ATH MeshCmd path (e.g. C:\\Tools\\ATH\\gmsh.exe).",
    )
    p_min_completion.add_argument(
        "--all-combinations",
        action="store_true",
        help="Include full step-7 combination matrix (can significantly increase runtime).",
    )
    p_min_completion.add_argument(
        "--verify-ath",
        action="store_true",
        help="Enable real ATH STL feasibility oracle and greedy minimization (slow, robust).",
    )
    p_min_completion.add_argument("--ath-exe", help="Override ATH executable path used for --verify-ath")
    p_min_completion.add_argument("--template-cfg", help="Override template CFG path used for --verify-ath")
    p_min_completion.set_defaults(func=cmd_ath_experiments_minimal_completion_search)

    p_contextual_ranges = sub_ath_exp_admin.add_parser(
        "contextual-ranges",
        help=(
            "Compute context-stratified safe-range suggestions from ath_experiments.sqlite "
            "(profile/gcurve/morph/enclosure)."
        ),
    )
    p_contextual_ranges.add_argument(
        "--reports-root",
        default="reports/ath_experiments",
        help="ATH experiment reports root containing ath_experiments.sqlite",
    )
    p_contextual_ranges.add_argument(
        "--run-group",
        default="all",
        help="Run-group selector (single, comma-separated, or 'all').",
    )
    p_contextual_ranges.add_argument(
        "--min-count",
        type=int,
        default=80,
        help="Minimum sample count per key/context bucket.",
    )
    p_contextual_ranges.add_argument(
        "--output-json",
        default="range_suggestions.contextual.v1.json",
        help="Output JSON filename written under reports-root.",
    )
    p_contextual_ranges.add_argument(
        "--output-md",
        default="range_suggestions.contextual.v1.md",
        help="Output markdown filename written under reports-root.",
    )
    p_contextual_ranges.add_argument(
        "--max-contexts-per-key",
        type=int,
        default=8,
        help="Maximum number of context rows shown per key in markdown report.",
    )
    p_contextual_ranges.set_defaults(func=cmd_ath_experiments_contextual_ranges)

    p_ui = sub.add_parser("ui", help="UI automation inspection utilities.")
    sub_ui = p_ui.add_subparsers(dest="ui_cmd", required=True)

    p_ui_inspect_akabak = sub_ui.add_parser(
        "inspect-akabak",
        help="Start/connect AKABAK and dump top-level windows + UIA tree to ui_maps/",
    )
    p_ui_inspect_akabak.add_argument("--akabak-exe", help="Override AKABAK executable path")
    p_ui_inspect_akabak.add_argument("--output-dir", default="ui_maps", help="Directory for inspector artifacts")
    p_ui_inspect_akabak.add_argument("--timeout-s", type=int, default=20, help="Startup/connect timeout in seconds")
    p_ui_inspect_akabak.add_argument("--dry-run", action="store_true", help="Write planned outputs without launching tools")
    p_ui_inspect_akabak.set_defaults(func=cmd_ui_inspect_akabak)

    p_ui_inspect_vacs = sub_ui.add_parser(
        "inspect-vacs",
        help="Start/connect VACS and dump top-level windows + UIA tree to ui_maps/",
    )
    p_ui_inspect_vacs.add_argument("--vacs-exe", help="Override VACS executable path")
    p_ui_inspect_vacs.add_argument("--output-dir", default="ui_maps", help="Directory for inspector artifacts")
    p_ui_inspect_vacs.add_argument("--timeout-s", type=int, default=20, help="Startup/connect timeout in seconds")
    p_ui_inspect_vacs.add_argument("--dry-run", action="store_true", help="Write planned outputs without launching tools")
    p_ui_inspect_vacs.set_defaults(func=cmd_ui_inspect_vacs)

    p_vacs = sub.add_parser("vacs", help="VACS graph catalog and export mapping tools.")
    sub_vacs = p_vacs.add_subparsers(dest="vacs_cmd", required=True)

    p_vacs_discover = sub_vacs.add_parser(
        "discover-graphs",
        help="Best-effort discovery and graph catalog skeleton generation for a VACS version.",
    )
    p_vacs_discover.add_argument("--vacs-exe", help="Override VACS executable path")
    p_vacs_discover.add_argument("--vacs-version", default="default", help="VACS version label for catalog folder")
    p_vacs_discover.add_argument(
        "--catalog-root",
        default="ui_maps/vacs",
        help="Graph catalog root folder (<root>/<vacs_version>/graph_catalog.json)",
    )
    p_vacs_discover.add_argument(
        "--ui-maps-output",
        default="ui_maps",
        help="Output folder for optional UI inspection artifacts.",
    )
    p_vacs_discover.add_argument("--timeout-s", type=int, default=20, help="Inspector startup/connect timeout")
    p_vacs_discover.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate catalog skeleton from recipes without launching VACS.",
    )
    p_vacs_discover.set_defaults(func=cmd_vacs_discover_graphs)

    p_ui_discover = sub.add_parser("ui-discover", help="PID-scoped UIA discovery dump (top windows + shallow tree).")
    p_ui_discover.add_argument("--app", required=True, choices=["akabak", "vacs"], help="Target application")
    p_ui_discover.add_argument("--pid", type=int, help="Existing process id. If omitted, connect/start by executable.")
    p_ui_discover.add_argument("--exe", help="Executable path when no --pid is supplied")
    p_ui_discover.add_argument(
        "--output-dir",
        default="runner_test_workspace/logs/ui_discover",
        help="Output directory for discovery artifacts",
    )
    p_ui_discover.add_argument("--timeout-s", type=int, default=20, help="Startup/connect timeout when launching")
    p_ui_discover.add_argument("--max-depth", type=int, default=2, help="Control tree depth for dumps")
    p_ui_discover.set_defaults(func=cmd_ui_discover)

    p_runs = sub.add_parser("runs", help="Run pinning and cleanup utilities.")
    sub_runs = p_runs.add_subparsers(dest="runs_cmd", required=True)

    p_runs_pin = sub_runs.add_parser("pin", help="Pin a run to keep it during cleanup.")
    p_runs_pin.add_argument("run_id", help="Run identifier")
    p_runs_pin.add_argument("--project-id", help="Project id override if run id exists in multiple projects")
    p_runs_pin.add_argument("--tag", help="Optional tag (e.g. baseline/final)")
    p_runs_pin.set_defaults(func=cmd_runs_pin)

    p_runs_unpin = sub_runs.add_parser("unpin", help="Remove pin from a run.")
    p_runs_unpin.add_argument("run_id", help="Run identifier")
    p_runs_unpin.add_argument("--project-id", help="Project id override if run id exists in multiple projects")
    p_runs_unpin.set_defaults(func=cmd_runs_unpin)

    p_runs_cleanup = sub_runs.add_parser(
        "cleanup-testdata",
        help="Delete all unpinned runs (test data) with optional export file deletion.",
    )
    p_runs_cleanup.add_argument("--project-id", help="Limit cleanup to one project id")
    p_runs_cleanup.add_argument(
        "--delete-exports",
        action="store_true",
        help="Delete run-linked export TXT files (inside project root only).",
    )
    p_runs_cleanup.add_argument("--dry-run", action="store_true", help="Preview only; do not mutate data.")
    p_runs_cleanup.set_defaults(func=cmd_runs_cleanup_testdata)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    from app.audit_mode import enable_audit_mode

    enable_audit_mode(entrypoint="app.cli.main")
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
