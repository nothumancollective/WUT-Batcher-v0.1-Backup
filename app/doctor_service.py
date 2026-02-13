"""Shared doctor checks used by CLI and GUI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from app.models import AppConfig


STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"
ZOMBIE_PROCESS_NAMES = ("akabak.exe", "vacsviewer_32.exe")


@dataclass
class DoctorCheck:
    key: str
    label: str
    status: str
    detail: str


@dataclass
class DoctorReport:
    overall_status: str
    checks: List[DoctorCheck]


def _overall_status(checks: List[DoctorCheck]) -> str:
    if any(check.status == STATUS_FAIL for check in checks):
        return STATUS_FAIL
    if any(check.status == STATUS_WARN for check in checks):
        return STATUS_WARN
    return STATUS_OK


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read_config_payload(config_path: Optional[Path]) -> Tuple[Dict[str, Any], Optional[str]]:
    if config_path is None or not config_path.exists():
        return {}, None
    try:
        return json.loads(config_path.read_text(encoding="utf-8")), None
    except Exception as exc:  # pragma: no cover - defensive
        return {}, str(exc)


def _default_report_path() -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    return repo_root / "logs" / "doctor_report.json"


def _report_payload(report: DoctorReport) -> Dict[str, Any]:
    return {
        "overall_status": report.overall_status,
        "checks": [check.__dict__ for check in report.checks],
        "generated_at": _now_iso(),
    }


def _write_report(path: Path, report: DoctorReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_report_payload(report), indent=2), encoding="utf-8")


def _ensure_dir(path: Path, label: str, fix: bool, required: bool) -> DoctorCheck:
    if path.exists():
        if path.is_dir():
            return DoctorCheck(
                key=f"{label}_exists",
                label=label,
                status=STATUS_OK,
                detail=f"Directory ready: {path}",
            )
        return DoctorCheck(
            key=f"{label}_exists",
            label=label,
            status=STATUS_FAIL,
            detail=f"Path exists but is not a directory: {path}",
        )

    if fix:
        try:
            path.mkdir(parents=True, exist_ok=True)
            return DoctorCheck(
                key=f"{label}_exists",
                label=label,
                status=STATUS_OK,
                detail=f"Created directory: {path}",
            )
        except OSError as exc:
            return DoctorCheck(
                key=f"{label}_exists",
                label=label,
                status=STATUS_FAIL,
                detail=f"Cannot create directory ({path}): {exc}",
            )

    status = STATUS_FAIL if required else STATUS_WARN
    return DoctorCheck(
        key=f"{label}_exists",
        label=label,
        status=status,
        detail=f"Missing directory: {path} (run with --fix to create)",
    )


def _write_test(path: Path, label: str) -> DoctorCheck:
    if not path.exists() or not path.is_dir():
        return DoctorCheck(
            key=f"{label}_write",
            label=f"{label} writable",
            status=STATUS_WARN,
            detail="Write test skipped (directory missing).",
        )
    test_file = path / ".doctor_write_test"
    try:
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink(missing_ok=True)
        return DoctorCheck(
            key=f"{label}_write",
            label=f"{label} writable",
            status=STATUS_OK,
            detail="Write test passed.",
        )
    except OSError as exc:
        return DoctorCheck(
            key=f"{label}_write",
            label=f"{label} writable",
            status=STATUS_FAIL,
            detail=f"Write test failed: {exc}",
        )


def _check_templates(templates_dir: Path, required_assets: Sequence[str]) -> List[DoctorCheck]:
    if not templates_dir.exists():
        return [
            DoctorCheck(
                key="templates",
                label="Templates directory",
                status=STATUS_WARN,
                detail=f"Missing templates directory: {templates_dir}",
            )
        ]

    missing_assets = [name for name in required_assets if not (templates_dir / name).exists()]
    if missing_assets:
        return [
            DoctorCheck(
                key="templates",
                label="Templates assets",
                status=STATUS_WARN,
                detail="Missing assets: " + ", ".join(missing_assets),
            )
        ]

    return [
        DoctorCheck(
            key="templates",
            label="Templates assets",
            status=STATUS_OK,
            detail="Required template assets are present.",
        )
    ]


def _check_runner_dir(repo_root: Path) -> DoctorCheck:
    runner_dir = repo_root / "Runner"
    if runner_dir.exists():
        return DoctorCheck(
            key="runner_dir",
            label="Runner directory",
            status=STATUS_OK,
            detail=f"Runner directory present: {runner_dir}",
        )
    return DoctorCheck(
        key="runner_dir",
        label="Runner directory",
        status=STATUS_WARN,
        detail=f"Runner directory missing: {runner_dir}",
    )


def _check_exe(config_data: Dict[str, Any], key: str, label: str) -> DoctorCheck:
    if os.name != "nt":
        return DoctorCheck(
            key=key,
            label=label,
            status=STATUS_WARN,
            detail="Skipped on non-Windows platform.",
        )

    raw = config_data.get(key)
    if not raw:
        return DoctorCheck(
            key=key,
            label=label,
            status=STATUS_WARN,
            detail=f"{label} not configured in app_config.json.",
        )

    path = Path(str(raw))
    if path.exists():
        return DoctorCheck(
            key=key,
            label=label,
            status=STATUS_OK,
            detail=f"Found: {path}",
        )
    return DoctorCheck(
        key=key,
        label=label,
        status=STATUS_FAIL,
        detail=f"Not found: {path}",
    )


def _check_exe_with_dir_fallback(
    config_data: Dict[str, Any],
    exe_key: str,
    dir_key: str,
    label: str,
) -> DoctorCheck:
    if os.name != "nt":
        return DoctorCheck(
            key=exe_key,
            label=label,
            status=STATUS_WARN,
            detail="Skipped on non-Windows platform.",
        )

    exe_value = config_data.get(exe_key)
    if exe_value:
        return _check_exe(config_data, exe_key, label)

    dir_value = config_data.get(dir_key)
    if dir_value:
        dir_path = Path(str(dir_value))
        if dir_path.exists():
            return DoctorCheck(
                key=exe_key,
                label=label,
                status=STATUS_WARN,
                detail=f"{label} exe not configured; {dir_key} exists: {dir_path}",
            )
        return DoctorCheck(
            key=exe_key,
            label=label,
            status=STATUS_FAIL,
            detail=f"{label} exe not configured and {dir_key} missing: {dir_path}",
        )

    return DoctorCheck(
        key=exe_key,
        label=label,
        status=STATUS_WARN,
        detail=f"{label} not configured in app_config.json.",
    )


def _list_windows_processes() -> Optional[Set[str]]:
    if os.name != "nt":
        return None
    result = subprocess.run(
        ["tasklist"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    processes: Set[str] = set()
    for line in result.stdout.splitlines()[3:]:
        if not line.strip():
            continue
        name = line.split()[0].strip().lower()
        processes.add(name)
    return processes


def _check_zombies(kill_zombies: bool) -> DoctorCheck:
    if os.name != "nt":
        return DoctorCheck(
            key="zombies",
            label="Zombie processes",
            status=STATUS_WARN,
            detail="Skipped on non-Windows platform.",
        )

    processes = _list_windows_processes()
    if processes is None:
        return DoctorCheck(
            key="zombies",
            label="Zombie processes",
            status=STATUS_WARN,
            detail="Unable to list processes via tasklist.",
        )

    targets = [name for name in ZOMBIE_PROCESS_NAMES if name.lower() in processes]
    if not targets:
        return DoctorCheck(
            key="zombies",
            label="Zombie processes",
            status=STATUS_OK,
            detail="No known zombie processes found.",
        )

    if not kill_zombies:
        return DoctorCheck(
            key="zombies",
            label="Zombie processes",
            status=STATUS_WARN,
            detail="Found running processes: " + ", ".join(targets) + " (use --kill-zombies to terminate).",
        )

    failures: List[str] = []
    for name in targets:
        result = subprocess.run(
            ["taskkill", "/F", "/IM", name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            failures.append(name)

    if failures:
        return DoctorCheck(
            key="zombies",
            label="Zombie processes",
            status=STATUS_WARN,
            detail="Failed to terminate: " + ", ".join(failures),
        )
    return DoctorCheck(
        key="zombies",
        label="Zombie processes",
        status=STATUS_OK,
        detail="Terminated processes: " + ", ".join(targets),
    )


def run_doctor_checks(
    app_config: AppConfig,
    config_path: Optional[Path] = None,
    *,
    fix: bool = False,
    kill_zombies: bool = False,
    report_path: Optional[Path] = None,
) -> DoctorReport:
    checks: List[DoctorCheck] = []

    config_payload, config_error = _read_config_payload(config_path)

    if config_path is None:
        checks.append(
            DoctorCheck(
                key="config_path",
                label="Config path",
                status=STATUS_WARN,
                detail="No config file path provided; defaults in use.",
            )
        )
    elif config_path.exists():
        checks.append(
            DoctorCheck(
                key="config_path",
                label="Config path",
                status=STATUS_OK,
                detail=f"Loaded config from {config_path}",
            )
        )
    else:
        checks.append(
            DoctorCheck(
                key="config_path",
                label="Config path",
                status=STATUS_WARN,
                detail=(
                    f"Config file not found at {config_path}; using defaults."
                ),
            )
        )

    if config_error:
        checks.append(
            DoctorCheck(
                key="config_parse",
                label="Config parse",
                status=STATUS_FAIL,
                detail=f"Config file could not be parsed: {config_error}",
            )
        )

    projects_root = Path(app_config.projects_root).expanduser()
    checks.append(_ensure_dir(projects_root, "Projects root", fix=fix, required=True))
    checks.append(_write_test(projects_root, "Projects root"))

    batch_results_root_value = config_payload.get("batch_results_root")
    if batch_results_root_value:
        batch_results_root = Path(str(batch_results_root_value)).expanduser()
        checks.append(
            _ensure_dir(batch_results_root, "Batch results root", fix=fix, required=False)
        )
        checks.append(_write_test(batch_results_root, "Batch results root"))
    else:
        checks.append(
            DoctorCheck(
                key="batch_results_root_exists",
                label="Batch results root",
                status=STATUS_WARN,
                detail="batch_results_root not configured in app_config.json.",
            )
        )

    ath_export_root_value = config_payload.get("ath_export_root")
    if os.name != "nt":
        checks.append(
            DoctorCheck(
                key="ath_export_root_exists",
                label="ATH export root",
                status=STATUS_WARN,
                detail="Skipped on non-Windows platform.",
            )
        )
    elif ath_export_root_value:
        ath_export_root = Path(str(ath_export_root_value)).expanduser()
        checks.append(
            _ensure_dir(ath_export_root, "ATH export root", fix=fix, required=False)
        )
        checks.append(_write_test(ath_export_root, "ATH export root"))
    else:
        checks.append(
            DoctorCheck(
                key="ath_export_root_exists",
                label="ATH export root",
                status=STATUS_WARN,
                detail="ATH export root not configured in app_config.json.",
            )
        )

    repo_root = Path(__file__).resolve().parents[1]
    templates_dir_value = config_payload.get("templates_dir")
    templates_dir = (
        Path(str(templates_dir_value)).expanduser()
        if templates_dir_value
        else (repo_root / "templates")
    )
    checks.extend(_check_templates(templates_dir, ["akabak_ready.png", "akabak_processing.png"]))
    checks.append(_check_runner_dir(repo_root))

    checks.append(_check_exe(config_payload, "ath_exe", "ATH executable"))
    checks.append(
        _check_exe_with_dir_fallback(
            config_payload, "akabak_exe", "akabak_dir", "Akabak executable"
        )
    )
    checks.append(
        _check_exe_with_dir_fallback(
            config_payload, "vacs_exe", "vacs_dir", "VACS Viewer executable"
        )
    )
    checks.append(_check_zombies(kill_zombies))

    report = DoctorReport(overall_status=_overall_status(checks), checks=checks)

    target_path = report_path or _default_report_path()
    try:
        _write_report(target_path, report)
        checks = report.checks + [
            DoctorCheck(
                key="doctor_report",
                label="Doctor report",
                status=STATUS_OK,
                detail=f"Wrote report to {target_path}",
            )
        ]
        report = DoctorReport(overall_status=_overall_status(checks), checks=checks)
        _write_report(target_path, report)
    except OSError as exc:
        checks = report.checks + [
            DoctorCheck(
                key="doctor_report",
                label="Doctor report",
                status=STATUS_FAIL,
                detail=f"Failed to write report to {target_path}: {exc}",
            )
        ]
        report = DoctorReport(overall_status=_overall_status(checks), checks=checks)
        try:
            _write_report(target_path, report)
        except OSError:
            pass

    return report


