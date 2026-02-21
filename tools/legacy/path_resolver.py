"""
LEGACY QUARANTINED: non-shipping path resolver snapshot.

Reconstructed from recovery artifacts
Confidence Level: HIGH
Sources used:
- c:/Work/Rebuild/docs/path_resolver.md
- c:/Work/Rebuild/app/path_resolver.py (recovered baseline)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.models import AppConfig


@dataclass(frozen=True)
class ResolvedPaths:
    projects_root: Path
    project_dir: Path
    batches_dir: Path
    batch_dir: Path
    config_dir: Path
    ath_export_archive_dir: Path
    result_dir: Path
    logs_dir: Path
    dataset_dir: Path


def resolve_paths(
    config: AppConfig,
    *,
    project_id: str,
    batch_id: str,
    ensure: bool = True,
) -> ResolvedPaths:
    projects_root = config.projects_root_path

    project_dir = projects_root / f"Project_{project_id}"
    batches_dir = project_dir / "batches"
    batch_dir = batches_dir / f"Batch_{batch_id}"

    config_dir = batch_dir / "Config"
    ath_export_archive_dir = batch_dir / "ATH Export"
    result_dir = batch_dir / "Resultate"
    logs_dir = batch_dir / "Logs"
    dataset_dir = project_dir / "dataset"

    if ensure:
        for d in (
            projects_root,
            project_dir,
            batches_dir,
            batch_dir,
            config_dir,
            ath_export_archive_dir,
            result_dir,
            logs_dir,
            dataset_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    return ResolvedPaths(
        projects_root=projects_root,
        project_dir=project_dir,
        batches_dir=batches_dir,
        batch_dir=batch_dir,
        config_dir=config_dir,
        ath_export_archive_dir=ath_export_archive_dir,
        result_dir=result_dir,
        logs_dir=logs_dir,
        dataset_dir=dataset_dir,
    )
