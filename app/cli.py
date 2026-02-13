from __future__ import annotations

import argparse
import dataclasses
import json
from pathlib import Path
from typing import Any, Dict, Optional

from app.models import AppConfig, Batch


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
    return 0


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

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
