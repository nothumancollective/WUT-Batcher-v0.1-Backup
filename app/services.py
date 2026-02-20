"""Core application services used by CLI and GUI (UI-orchestrator only)."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
import hashlib
import logging
import os
import re
import statistics
import time
from contextlib import closing
from pathlib import Path
import sqlite3
import shutil
import subprocess
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from app.batch_orchestrator import PlanningSummary, materialize_batch_plan
from app.ath_knowledge import load_ath_knowledge
from app.ath_driver_assets import repair_post_ath_le_binding
from app.compatibility_service import CompatibilityService
from app.constants import (
    ATH_PREVIEW_CFG_DIR,
    ATH_PREVIEW_CFG_NAME,
    ATH_PREVIEW_EXPORT_ROOT,
    DEFAULT_RUNNER_MODE,
    PREVIEW_CACHE_APPDIR,
    PREVIEW_CACHE_KEEP_FILES,
    PREVIEW_CACHE_MAX_AGE_DAYS,
)
from app.cfg_renderer import render_cfg_text
from app.models import Batch, ParamSelection, Project, ProjectConstraints, SweepSpec
from app.project_storage import ProjectRepository
from app.runtime_orchestrator import RuntimeSummary, run_batch_pipeline
from app.runners import AthRunner
from app.settings_store import SettingsStore, UserSettings
from app.tidy_dataset import TidyDatasetWriter
from app.version_resolver import resolve_versions

LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _next_prefixed_id(existing_ids: List[str], prefix: str) -> str:
    max_num = 0
    for raw in existing_ids:
        value = str(raw).strip()
        if not value.startswith(prefix):
            continue
        tail = value[len(prefix) :]
        if tail.isdigit():
            max_num = max(max_num, int(tail))
    return f"{prefix}{max_num + 1:03d}"


ATH_STL_EXPORT_DIRECTIVE: Optional[str] = "Output.STL = 1"


def _apply_stl_export_hook(cfg_text: str) -> tuple[str, bool]:
    directive_line = str(ATH_STL_EXPORT_DIRECTIVE or "").strip()
    if not directive_line:
        directive_line = "Output.STL = 1"
    if directive_line and directive_line not in cfg_text:
        return (cfg_text.rstrip() + f"\n{directive_line}\n", False)
    return (cfg_text, False)


def _select_generated_abec(export_dir: Path) -> Optional[Path]:
    candidates = [path for path in export_dir.rglob("*.abec") if path.is_file()]
    if not candidates:
        return None
    candidates.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
    return candidates[0]


def _is_executable_path(path: Optional[str]) -> bool:
    if not path:
        return False
    candidate = Path(path).expanduser()
    return candidate.exists() and candidate.is_file()


def _settings_hash(settings: UserSettings) -> str:
    payload = {
        "library_root": settings.library_root,
        "ath_exe": settings.ath_exe,
        "akabak_exe": settings.akabak_exe,
        "vacs_exe": settings.vacs_exe,
        "template_cfg": settings.template_cfg,
        "background_automation_mode": bool(getattr(settings, "background_automation_mode", True)),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _detect_git_commit() -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2,
            check=False,
            creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0),
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    value = (result.stdout or "").strip()
    return value or None


def _percentile(sorted_values: List[float], p: float) -> Optional[float]:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = max(0.0, min(1.0, float(p))) * float(len(sorted_values) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = rank - lower
    if lower == upper:
        return float(sorted_values[lower])
    return float(sorted_values[lower] + ((sorted_values[upper] - sorted_values[lower]) * fraction))


class PreviewGenerationCancelled(RuntimeError):
    """Raised when an in-flight preview generation is cancelled by the UI."""


_OUTPUT_ASSIGN_RE = re.compile(r"(?im)^[ \t]*({key})[ \t]*=.*$")
_BATCH_NOT_VISIBLE_KEY_RE = re.compile(r"'([^']+)'")
_REQUIRED_KEY_PREFIX_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s+ist\b", re.IGNORECASE)
_EN_REQUIRED_KEY_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s+(?:is|was)\s+required\b", re.IGNORECASE)

_PREVIEW_R_OSSE_DEFAULTS: Dict[str, float] = {
    "R": 120.0,
    "r0": 12.7,
    "a0": 7.0,
    "a": 45.0,
    "k": 0.5,
    "r": 0.5,
    "m": 4.0,
    "b": 1.0,
    "q": 0.996,
}

_PREVIEW_ENCLOSURE_DEFAULTS: Dict[str, Any] = {
    "Spacing": [30.0, 30.0, 30.0, 200.0],
    "Depth": 180.0,
    "EdgeRadius": 20.0,
    "EdgeType": 1,
    "FrontResolution": [8.0, 8.0, 16.0, 16.0],
    "BackResolution": [20.0, 20.0, 20.0, 20.0],
}

_PREVIEW_ATH_MINIMAL_DEFAULTS: Dict[str, Any] = {
    # Keep STL preview generation resilient with the smallest practical set.
    "Length": 120.0,
    "GCurve.Dist": 80.0,
    "GCurve.Width": 0.7,
    "GCurve.SE.n": 3.0,
    "GCurve.SF.a": 1.0,
    "GCurve.SF.b": 1.0,
    "GCurve.SF.m1": 4.0,
    "GCurve.SF.m2": 4.0,
    "GCurve.SF.n1": 1.0,
    "GCurve.SF.n2": 1.0,
    "GCurve.SF.n3": 1.0,
    "R-OSSE": {},
    "Mesh.Enclosure": {},
}

_PREVIEW_POLICY_DEFAULTS: Dict[str, Any] = {
    "Throat.Diameter": 25.4,
    "Throat.Angle": 7.0,
    "Coverage.Angle": 45.0,
    "Length": 120.0,
    "Term.s": 0.7,
    "Term.n": 4.0,
    "Term.q": 0.995,
    "CircArc.TermAngle": 1.0,
    "GCurve.Dist": 80.0,
    "GCurve.Width": 0.7,
    "GCurve.AspectRatio": 1.0,
    "GCurve.SE.n": 3.0,
    "Morph.TargetShape": 0,
    "Morph.TargetWidth": 0.0,
    "Morph.TargetHeight": 0.0,
    "Morph.CornerRadius": 35.0,
    "Morph.FixedPart": 0.0,
    "Morph.Rate": 3.0,
    "Morph.AllowShrinkage": 0,
    "Mesh.AngularSegments": 64,
    "Mesh.LengthSegments": 20,
    "Mesh.ThroatResolution": 5.0,
    "Mesh.MouthResolution": 10.0,
    "Mesh.InterfaceResolution": 8.0,
    "Mesh.CornerSegments": 4,
    "Mesh.Enclosure": dict(_PREVIEW_ENCLOSURE_DEFAULTS),
    "Mesh.Enclosure.Depth": float(_PREVIEW_ENCLOSURE_DEFAULTS["Depth"]),
    "Mesh.Enclosure.EdgeType": int(_PREVIEW_ENCLOSURE_DEFAULTS["EdgeType"]),
    "R-OSSE": dict(_PREVIEW_R_OSSE_DEFAULTS),
}

_PREVIEW_POLICY_REQUIRED_BY_PROFILE: Dict[str, List[str]] = {
    "osse": ["Throat.Profile", "Length", "OS.k", "Term.s", "Term.n", "Term.q"],
    "circarc": ["Throat.Profile", "Length", "CircArc.TermAngle", "CircArc.Radius"],
    "rosse": [
        "R-OSSE.R",
        "R-OSSE.r0",
        "R-OSSE.a0",
        "R-OSSE.a",
        "R-OSSE.k",
        "R-OSSE.r",
        "R-OSSE.m",
        "R-OSSE.b",
        "R-OSSE.q",
    ],
}

_PREVIEW_POLICY_REQUIRED_MESH: List[str] = [
    "Mesh.ThroatResolution",
    "Mesh.MouthResolution",
    "Mesh.Quadrants",
]

_PREVIEW_POLICY_REQUIRED_MORPH_ON: List[str] = [
    "Morph.TargetShape",
    "Morph.TargetWidth",
    "Morph.TargetHeight",
    "Morph.CornerRadius",
    "Morph.FixedPart",
    "Morph.Rate",
    "Morph.AllowShrinkage",
]

_PREVIEW_POLICY_REQUIRED_GCURVE: Dict[int, List[str]] = {
    1: ["GCurve.Dist", "GCurve.Width", "GCurve.AspectRatio", "GCurve.SE.n"],
    2: [
        "GCurve.Dist",
        "GCurve.Width",
        "GCurve.AspectRatio",
        "GCurve.SF.a",
        "GCurve.SF.b",
        "GCurve.SF.m1",
        "GCurve.SF.m2",
        "GCurve.SF.n1",
        "GCurve.SF.n2",
        "GCurve.SF.n3",
    ],
}

_PREVIEW_POLICY_BLOCK_ORDER: Tuple[str, ...] = ("profile", "mesh", "gcurve", "morph", "enclosure")


def _local_appdata_root() -> Path:
    raw = os.environ.get("LOCALAPPDATA", "")
    if raw.strip():
        return Path(raw).expanduser()
    return (Path.home() / "AppData" / "Local").expanduser()


def _preview_cache_dir() -> Path:
    path = _local_appdata_root()
    for part in PREVIEW_CACHE_APPDIR:
        path = path / str(part)
    return path


def _snapshot_subdirs(root: Path) -> Dict[str, int]:
    if not root.exists():
        return {}
    result: Dict[str, int] = {}
    for entry in root.iterdir():
        if entry.is_dir():
            result[entry.name] = int(entry.stat().st_mtime_ns)
    return result


def _detect_changed_export_dir(export_root: Path, before: Mapping[str, int]) -> Optional[Path]:
    if not export_root.exists():
        return None
    after = _snapshot_subdirs(export_root)
    created = [name for name in after.keys() if name not in before]
    if created:
        created.sort(key=lambda name: after[name], reverse=True)
        return export_root / created[0]
    changed = [name for name, mtime_ns in after.items() if int(mtime_ns) > int(before.get(name, -1))]
    if changed:
        changed.sort(key=lambda name: after[name], reverse=True)
        return export_root / changed[0]
    return None


def _extract_mesh_cmd_from_runtime_cfg(runtime_cfg_path: Path) -> str:
    if not runtime_cfg_path.exists():
        return ""
    try:
        text = runtime_cfg_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    for raw_line in text.splitlines():
        line = str(raw_line).strip()
        if not line or line.startswith(";"):
            continue
        if "=" not in line:
            continue
        left, right = line.split("=", 1)
        if str(left).strip().lower() != "meshcmd":
            continue
        value = str(right).strip().strip('"').strip()
        if value:
            return value
    return ""


def _best_mesh_cmd_for_preview(ath_executable: Path, *, fallback_cmd: str = "") -> str:
    candidate = ath_executable.parent / "gmsh.exe"
    if candidate.exists():
        return str(candidate)
    return str(fallback_cmd or "").strip()


def _ensure_preview_gmsh_wrapper(*, cfg_dir: Path, gmsh_exe: str) -> str:
    cfg_dir.mkdir(parents=True, exist_ok=True)
    wrapper_path = cfg_dir / "wut_preview_gmsh_wrapper.cmd"
    gmsh_norm = str(gmsh_exe or "").strip().replace("\\", "/")
    wrapper_text = "\n".join(
        [
            "@echo off",
            "setlocal EnableDelayedExpansion",
            f"set \"GMSH_EXE={gmsh_norm}\"",
            "if not exist \"%GMSH_EXE%\" exit /b 1",
            "if exist \"mesh.geo\" (",
            "  \"%GMSH_EXE%\" -3 \"mesh.geo\" -format stl -o \"mesh.stl\" >nul 2>&1",
            "  exit /b %errorlevel%",
            ")",
            "for %%F in (*.geo) do (",
            "  \"%GMSH_EXE%\" -3 \"%%~fF\" -format stl -o \"%%~dpnF.stl\" >nul 2>&1",
            "  exit /b %errorlevel%",
            ")",
            "for /r %%F in (*.geo) do (",
            "  \"%GMSH_EXE%\" -3 \"%%~fF\" -format stl -o \"%%~dpnF.stl\" >nul 2>&1",
            "  exit /b %errorlevel%",
            ")",
            "exit /b 1",
            "",
        ]
    )
    wrapper_path.write_text(wrapper_text, encoding="ascii")
    return str(wrapper_path)


def _mesh_cmd_for_preview_runtime(*, cfg_dir: Path, mesh_cmd: str) -> str:
    raw = str(mesh_cmd or "").strip().strip('"').strip("'")
    if not raw:
        return raw
    if raw.lower().endswith("gmsh.exe"):
        return _ensure_preview_gmsh_wrapper(cfg_dir=cfg_dir, gmsh_exe=raw)
    return raw


def _write_preview_runtime_cfg(cfg_dir: Path, *, export_root: Path, mesh_cmd: str) -> Path:
    cfg_dir.mkdir(parents=True, exist_ok=True)
    export_value = str(export_root).replace("\\", "/")
    ath_cfg = cfg_dir / "ath.cfg"
    ath_cfg.write_text(
        (
            f'OutputRootDir = "{export_value}"\n'
            f'MeshCmd = "{mesh_cmd}"\n'
            'GnuplotPath = ""\n'
        ),
        encoding="utf-8",
    )
    return ath_cfg


def _enforce_output_flag(cfg_text: str, *, key: str, value: int) -> str:
    normalized_key = str(key).strip()
    pattern = re.compile(
        _OUTPUT_ASSIGN_RE.pattern.format(key=re.escape(normalized_key)),
        flags=re.IGNORECASE | re.MULTILINE,
    )
    replacement = f"{normalized_key} = {int(value)}"
    if pattern.search(cfg_text):
        return pattern.sub(replacement, cfg_text)
    text = cfg_text.rstrip()
    return f"{text}\n{replacement}\n"


def _pick_latest_stl(search_root: Path) -> Optional[Path]:
    candidates = [path for path in search_root.rglob("*.stl") if path.is_file()]
    if not candidates:
        return None
    candidates.sort(key=lambda path: int(path.stat().st_mtime_ns), reverse=True)
    return candidates[0]


def _terminate_process(proc: subprocess.Popen[str]) -> None:
    try:
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=3.0)
            return
        except Exception:
            LOGGER.debug("Preview process did not terminate gracefully; escalating to kill.")
        proc.kill()
        proc.wait(timeout=2.0)
    except Exception as exc:
        LOGGER.warning("Failed to terminate preview process cleanly: %s", exc)
        return


def _prune_preview_cache(cache_dir: Path, *, keep_last: int, max_age_days: int) -> Dict[str, int]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    keep = max(1, int(keep_last))
    max_age = max(0, int(max_age_days))
    now_s = float(time.time())
    age_limit_s = float(max_age) * 86400.0

    stl_files = [path for path in cache_dir.glob("horn_preview_*.stl") if path.is_file()]
    stl_files.sort(key=lambda path: int(path.stat().st_mtime_ns), reverse=True)

    deleted_retention = 0
    if len(stl_files) > keep:
        for stale in stl_files[keep:]:
            try:
                stale.unlink()
                deleted_retention += 1
            except Exception:
                continue

    deleted_age = 0
    if age_limit_s > 0:
        for candidate in list(cache_dir.glob("*")):
            if not candidate.is_file():
                continue
            try:
                age_s = now_s - float(candidate.stat().st_mtime)
            except Exception:
                continue
            if age_s <= age_limit_s:
                continue
            try:
                candidate.unlink()
                deleted_age += 1
            except Exception:
                continue

    remaining = len([path for path in cache_dir.glob("horn_preview_*.stl") if path.is_file()])
    return {
        "deleted_retention": int(deleted_retention),
        "deleted_age": int(deleted_age),
        "remaining_stl": int(remaining),
    }


def _issue_attr(issue: Any, key: str, default: Any = None) -> Any:
    if isinstance(issue, Mapping):
        return issue.get(key, default)
    return getattr(issue, key, default)


def _non_none_selected_params(selected_params: Mapping[str, Any]) -> Dict[str, Any]:
    selected: Dict[str, Any] = {}
    for key, value in dict(selected_params or {}).items():
        key_str = str(key).strip()
        if not key_str or value is None:
            continue
        selected[key_str] = value
    return selected


def _project_defined_values(constraints: ProjectConstraints) -> Dict[str, Any]:
    payload = constraints.to_dict()
    merged: Dict[str, Any] = {}
    fixed = payload.get("fixed_params")
    if isinstance(fixed, Mapping):
        for key, value in fixed.items():
            if value is not None:
                merged[str(key)] = value
    limits = payload.get("limits")
    if isinstance(limits, Mapping):
        for key, value in limits.items():
            if value is not None:
                merged[str(key)] = value
    for row in list(payload.get("param_states", []) or []):
        if not isinstance(row, Mapping):
            continue
        if not bool(row.get("is_set")):
            continue
        key = str(row.get("param_name", "")).strip()
        if not key:
            continue
        value = row.get("value")
        if value is not None:
            merged[key] = value
    return merged


def _catalog_parameter_map() -> Dict[str, Dict[str, Any]]:
    bundle = load_ath_knowledge()
    result: Dict[str, Dict[str, Any]] = {}
    for item in list(bundle.catalog.get("parameters", []) or []):
        if not isinstance(item, dict):
            continue
        key = str(item.get("key", "")).strip()
        if key:
            result[key] = item
    return result


def _extract_required_keys(issues: Sequence[Any]) -> List[str]:
    required: List[str] = []
    for issue in list(issues or []):
        severity = str(_issue_attr(issue, "severity", "")).strip().lower()
        if severity not in {"fatal", "error"}:
            continue
        rule_id = str(_issue_attr(issue, "rule_id", "")).strip().lower()
        message = str(_issue_attr(issue, "message", "")).strip()
        field_key = str(_issue_attr(issue, "field_key", "") or _issue_attr(issue, "key", "")).strip()
        if field_key and ("required" in rule_id or "erforderlich" in message.lower()):
            required.append(field_key)
            continue
        match = _REQUIRED_KEY_PREFIX_RE.search(message)
        if match:
            required.append(str(match.group(1)).strip())
            continue
        match_en = _EN_REQUIRED_KEY_RE.search(message)
        if match_en:
            required.append(str(match_en.group(1)).strip())
    return sorted({str(key) for key in required if str(key).strip()})


def _coerce_preview_scalar(value: Any, *, key: str, catalog: Mapping[str, Any]) -> Any:
    if value is None:
        return None
    ath_type = str(catalog.get("type", "")).strip().lower()
    domain = catalog.get("domain")
    domain_map = domain if isinstance(domain, Mapping) else {}
    if ath_type == "bool":
        return 1 if bool(value) else 0
    if ath_type == "int":
        try:
            return int(float(value))
        except Exception:
            return value
    if ath_type in {"float", "expr"}:
        try:
            return float(value)
        except Exception:
            return value
    if ath_type == "enum":
        enum_values = list(domain_map.get("enum", []) or [])
        if enum_values and value not in enum_values:
            return enum_values[0]
    if key == "Throat.Profile":
        try:
            parsed = int(float(value))
        except Exception:
            return value
        return parsed
    return value


def _default_for_catalog_key(
    key: str,
    *,
    current_values: Mapping[str, Any],
    catalog_map: Mapping[str, Dict[str, Any]],
    tier: str = "ath_minimal",
) -> Any:
    tier_token = str(tier or "ath_minimal").strip().lower()
    preset_map = _PREVIEW_POLICY_DEFAULTS if tier_token == "policy_minimal" else _PREVIEW_ATH_MINIMAL_DEFAULTS
    if key.startswith("Mesh.Enclosure."):
        sub_key = key.split(".", 2)[-1]
        if sub_key in _PREVIEW_ENCLOSURE_DEFAULTS:
            return _PREVIEW_ENCLOSURE_DEFAULTS[sub_key]
        return None
    if key in preset_map:
        preset = preset_map[key]
        if isinstance(preset, dict):
            return dict(preset)
        return preset

    raw = dict(catalog_map.get(str(key), {}) or {})
    if not raw:
        return None
    if "default" in raw:
        return raw.get("default")
    ath_type = str(raw.get("type", "")).strip().lower()
    domain = raw.get("domain")
    domain_map = domain if isinstance(domain, Mapping) else {}
    if ath_type == "enum":
        enum_values = list(domain_map.get("enum", []) or [])
        if enum_values:
            return enum_values[0]
        return None
    if ath_type == "bool":
        return 0
    if ath_type == "int":
        min_value = domain_map.get("min")
        try:
            return int(float(min_value))
        except Exception:
            return 1
    if ath_type in {"float", "expr"}:
        min_value = domain_map.get("min")
        try:
            return float(min_value)
        except Exception:
            return 0.0
    if ath_type == "list<int>":
        return []
    if ath_type == "list<float>":
        return []
    if ath_type == "object" and key == "R-OSSE":
        if tier_token == "policy_minimal":
            baseline = dict(_PREVIEW_R_OSSE_DEFAULTS)
            throat_diameter = current_values.get("Throat.Diameter")
            try:
                if throat_diameter is not None:
                    baseline["r0"] = float(throat_diameter) / 2.0
            except Exception:
                pass
            return baseline
        return {}
    if ath_type == "object" and key == "Mesh.Enclosure":
        return dict(_PREVIEW_ENCLOSURE_DEFAULTS) if tier_token == "policy_minimal" else {}
    return None


def _normalize_mesh_interface_lists(parameters: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(parameters)

    def _as_int_list(value: Any) -> Optional[List[int]]:
        if value is None:
            return None
        if isinstance(value, list):
            result: List[int] = []
            for item in value:
                try:
                    result.append(int(float(item)))
                except Exception:
                    continue
            return result
        try:
            return [int(float(value))]
        except Exception:
            return None

    def _as_float_list(value: Any) -> Optional[List[float]]:
        if value is None:
            return None
        if isinstance(value, list):
            result: List[float] = []
            for item in value:
                try:
                    result.append(float(item))
                except Exception:
                    continue
            return result
        try:
            return [float(value)]
        except Exception:
            return None

    slices = _as_int_list(normalized.get("Mesh.SubdomainSlices"))
    if slices:
        normalized["Mesh.SubdomainSlices"] = slices

    count = len(slices or [])
    for key in ("Mesh.InterfaceOffset", "Mesh.InterfaceDraw"):
        values = _as_float_list(normalized.get(key))
        if values is None:
            continue
        if count > 0:
            if len(values) > count:
                values = values[:count]
            elif len(values) < count:
                pad = [0.0 for _ in range(count - len(values))]
                values = [*values, *pad]
        normalized[key] = values
    return normalized


def _as_float_list(raw: Any) -> List[float]:
    if raw is None:
        return []
    if isinstance(raw, (int, float)):
        return [float(raw)]
    if isinstance(raw, str):
        values: List[float] = []
        for token in re.split(r"[,\s;]+", raw.strip()):
            if not token:
                continue
            try:
                values.append(float(token.replace(",", ".")))
            except Exception:
                continue
        return values
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        values = []
        for item in list(raw):
            try:
                values.append(float(item))
            except Exception:
                continue
        return values
    return []


def _expand4(values: Sequence[float], *, fallback: float) -> List[float]:
    raw = [float(item) for item in list(values or [])]
    if not raw:
        return [float(fallback)] * 4
    out = raw[:4]
    while len(out) < 4:
        out.append(float(out[-1]))
    return out


def _normalize_mesh_enclosure(
    value: Mapping[str, Any],
    *,
    allow_plan_mode: bool,
) -> Dict[str, Any]:
    enclosure: Dict[str, Any] = {}
    raw = dict(value or {})
    plan = str(raw.get("Plan", "") or "").strip()
    effective_plan = plan if allow_plan_mode else ""

    spacing = _as_float_list(raw.get("Spacing"))
    if spacing:
        enclosure["Spacing"] = _expand4(spacing, fallback=float(_PREVIEW_ENCLOSURE_DEFAULTS["Spacing"][0]))

    front = _as_float_list(raw.get("FrontResolution"))
    if front:
        enclosure["FrontResolution"] = _expand4(front, fallback=float(_PREVIEW_ENCLOSURE_DEFAULTS["FrontResolution"][0]))

    back = _as_float_list(raw.get("BackResolution"))
    if back:
        enclosure["BackResolution"] = _expand4(back, fallback=float(_PREVIEW_ENCLOSURE_DEFAULTS["BackResolution"][0]))

    try:
        depth = float(raw.get("Depth")) if raw.get("Depth") is not None else None
    except Exception:
        depth = None
    if depth is not None:
        enclosure["Depth"] = depth

    try:
        edge_radius = float(raw.get("EdgeRadius")) if raw.get("EdgeRadius") is not None else None
    except Exception:
        edge_radius = None
    if edge_radius is not None:
        enclosure["EdgeRadius"] = edge_radius

    try:
        edge_type = int(float(raw.get("EdgeType"))) if raw.get("EdgeType") is not None else None
    except Exception:
        edge_type = None
    if edge_type in {1, 2}:
        enclosure["EdgeType"] = edge_type

    if effective_plan:
        enclosure["Plan"] = effective_plan
        enclosure.setdefault("Spacing", _expand4([], fallback=float(_PREVIEW_ENCLOSURE_DEFAULTS["Spacing"][0])))
        enclosure.setdefault(
            "FrontResolution",
            _expand4([], fallback=float(_PREVIEW_ENCLOSURE_DEFAULTS["FrontResolution"][0])),
        )
        enclosure.setdefault(
            "BackResolution",
            _expand4([], fallback=float(_PREVIEW_ENCLOSURE_DEFAULTS["BackResolution"][0])),
        )
        return enclosure

    if not effective_plan and "Depth" not in enclosure:
        enclosure["Depth"] = float(_PREVIEW_ENCLOSURE_DEFAULTS.get("Depth", 180.0))
    if "EdgeType" not in enclosure:
        enclosure["EdgeType"] = int(_PREVIEW_ENCLOSURE_DEFAULTS.get("EdgeType", 1))
    enclosure.setdefault("Spacing", _expand4([], fallback=float(_PREVIEW_ENCLOSURE_DEFAULTS["Spacing"][0])))
    enclosure.setdefault(
        "FrontResolution",
        _expand4([], fallback=float(_PREVIEW_ENCLOSURE_DEFAULTS["FrontResolution"][0])),
    )
    enclosure.setdefault(
        "BackResolution",
        _expand4([], fallback=float(_PREVIEW_ENCLOSURE_DEFAULTS["BackResolution"][0])),
    )
    return enclosure


def _normalize_preview_render_parameters(
    parameters: Mapping[str, Any],
    *,
    expand_rosse_defaults: bool = False,
    allow_enclosure_plan_mode: bool = True,
) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}
    for key, value in dict(parameters or {}).items():
        key_s = str(key).strip()
        if not key_s or value is None:
            continue
        normalized[key_s] = value

    # Infer controller defaults from block-specific values so preview remains stable
    # even when UI controllers are currently unset in a partial draft.
    if "Throat.Profile" not in normalized:
        if isinstance(normalized.get("R-OSSE"), Mapping):
            normalized["Throat.Profile"] = 2
        elif any(key in normalized for key in ("CircArc.Radius", "CircArc.TermAngle")):
            normalized["Throat.Profile"] = 3
        elif any(key in normalized for key in ("Term.s", "Term.n", "Term.q", "Term.k")):
            normalized["Throat.Profile"] = 1
    if "GCurve.Type" not in normalized:
        if any(str(key).startswith("GCurve.SF.") for key in normalized.keys()):
            normalized["GCurve.Type"] = 2
        elif "GCurve.SE.n" in normalized:
            normalized["GCurve.Type"] = 1
    if "Morph.TargetShape" not in normalized:
        if any(
            key in normalized
            for key in (
                "Morph.TargetWidth",
                "Morph.TargetHeight",
                "Morph.CornerRadius",
                "Morph.FixedPart",
                "Morph.Rate",
                "Morph.AllowShrinkage",
            )
        ):
            normalized["Morph.TargetShape"] = 1

    throat_profile_value = normalized.get("Throat.Profile")
    rosse_mode = str(throat_profile_value).strip() in {"2", "2.0"}
    if rosse_mode:
        normalized.pop("Throat.Profile", None)

    rosse_value = normalized.get("R-OSSE")
    if isinstance(rosse_value, Mapping):
        if expand_rosse_defaults:
            merged = dict(_PREVIEW_R_OSSE_DEFAULTS)
            for name, raw_value in dict(rosse_value).items():
                if raw_value is None:
                    continue
                merged[str(name)] = raw_value
            normalized["R-OSSE"] = merged
        else:
            merged = {}
            for name, raw_value in dict(rosse_value).items():
                if raw_value is None:
                    continue
                merged[str(name)] = raw_value
            normalized["R-OSSE"] = merged
    elif rosse_mode and "R-OSSE" not in normalized:
        normalized["R-OSSE"] = dict(_PREVIEW_R_OSSE_DEFAULTS) if expand_rosse_defaults else {}

    enclosure_value = normalized.get("Mesh.Enclosure")
    if isinstance(enclosure_value, Mapping):
        normalized["Mesh.Enclosure"] = _normalize_mesh_enclosure(
            enclosure_value,
            allow_plan_mode=bool(allow_enclosure_plan_mode),
        )

    normalized = _normalize_mesh_interface_lists(normalized)
    return normalized


def _preview_profile_mode(parameters: Mapping[str, Any]) -> str:
    rosse_value = parameters.get("R-OSSE")
    if isinstance(rosse_value, Mapping):
        return "rosse"
    token = str(parameters.get("Throat.Profile", "")).strip()
    if token in {"2", "2.0"}:
        return "rosse"
    if token in {"3", "3.0"}:
        return "circarc"
    return "osse"


def _preview_policy_requirement_map(parameters: Mapping[str, Any]) -> Dict[str, List[str]]:
    payload = dict(parameters or {})
    requirements: Dict[str, List[str]] = {block: [] for block in _PREVIEW_POLICY_BLOCK_ORDER}

    profile_mode = _preview_profile_mode(payload)
    requirements["profile"] = list(_PREVIEW_POLICY_REQUIRED_BY_PROFILE.get(profile_mode, []))
    requirements["mesh"] = list(_PREVIEW_POLICY_REQUIRED_MESH)

    gcurve_type = payload.get("GCurve.Type")
    try:
        gcurve_type_num = int(float(gcurve_type)) if gcurve_type is not None else None
    except Exception:
        gcurve_type_num = None
    if gcurve_type_num not in {None, 0}:
        requirements["gcurve"].append("GCurve.Type")
        requirements["gcurve"].extend(list(_PREVIEW_POLICY_REQUIRED_GCURVE.get(int(gcurve_type_num), ["GCurve.Dist", "GCurve.Width"])))

    morph_shape = payload.get("Morph.TargetShape")
    try:
        morph_shape_num = int(float(morph_shape)) if morph_shape is not None else 0
    except Exception:
        morph_shape_num = 0
    if morph_shape_num in {1, 2}:
        requirements["morph"] = list(_PREVIEW_POLICY_REQUIRED_MORPH_ON)

    enclosure_value = payload.get("Mesh.Enclosure")
    if isinstance(enclosure_value, Mapping):
        plan_name = str(enclosure_value.get("Plan", "") or "").strip()
        requirements["enclosure"].append("Mesh.Enclosure")
        if plan_name:
            requirements["enclosure"].append("Mesh.Enclosure.Plan")
        else:
            requirements["enclosure"].append("Mesh.Enclosure.Depth")
    return requirements


def _missing_preview_policy_by_block(parameters: Mapping[str, Any]) -> Dict[str, List[str]]:
    payload = dict(parameters or {})
    required = _preview_policy_requirement_map(payload)
    missing_by_block: Dict[str, List[str]] = {}
    for block in _PREVIEW_POLICY_BLOCK_ORDER:
        missing: List[str] = []
        for key in list(required.get(block, []) or []):
            key_s = str(key).strip()
            if not key_s:
                continue
            if "." in key_s:
                parent_key, sub_key = key_s.rsplit(".", 1)
                parent_value = payload.get(parent_key)
                if isinstance(parent_value, Mapping):
                    if parent_value.get(sub_key) is None:
                        missing.append(key_s)
                    continue
            if payload.get(key_s) is None:
                missing.append(key_s)
        missing_by_block[block] = sorted(set(missing))
    return missing_by_block


def _missing_preview_policy_keys(parameters: Mapping[str, Any]) -> List[str]:
    missing_by_block = _missing_preview_policy_by_block(parameters)
    missing: List[str] = []
    for block in _PREVIEW_POLICY_BLOCK_ORDER:
        missing.extend(list(missing_by_block.get(block, []) or []))
    return sorted(set(missing))


def _policy_defaults_for_missing_keys(
    missing_keys: Sequence[str],
    *,
    context_values: Mapping[str, Any],
    catalog_map: Mapping[str, Dict[str, Any]],
) -> Dict[str, Any]:
    filled: Dict[str, Any] = {}
    rosse_sub_defaults: Dict[str, Any] = {}
    enclosure_sub_defaults: Dict[str, Any] = {}
    for raw_key in list(missing_keys or []):
        key = str(raw_key).strip()
        if not key:
            continue
        if key.startswith("R-OSSE."):
            sub_key = key.split(".", 1)[1]
            if sub_key in _PREVIEW_R_OSSE_DEFAULTS:
                rosse_sub_defaults[sub_key] = _PREVIEW_R_OSSE_DEFAULTS[sub_key]
            continue
        if key.startswith("Mesh.Enclosure."):
            sub_key = key.split(".", 2)[-1]
            if sub_key in _PREVIEW_ENCLOSURE_DEFAULTS:
                enclosure_sub_defaults[sub_key] = _PREVIEW_ENCLOSURE_DEFAULTS[sub_key]
            continue
        default_value = _default_for_catalog_key(
            key,
            current_values=context_values,
            catalog_map=catalog_map,
            tier="policy_minimal",
        )
        if default_value is None:
            continue
        filled[key] = default_value
    if rosse_sub_defaults:
        existing = filled.get("R-OSSE")
        merged = dict(existing) if isinstance(existing, Mapping) else {}
        merged.update(rosse_sub_defaults)
        filled["R-OSSE"] = merged
    if enclosure_sub_defaults:
        existing = filled.get("Mesh.Enclosure")
        merged = dict(existing) if isinstance(existing, Mapping) else {}
        merged.update(enclosure_sub_defaults)
        filled["Mesh.Enclosure"] = merged
    return filled


def _apply_ath_minimal_selected_defaults(selected_params: Mapping[str, Any]) -> Dict[str, Any]:
    selected: Dict[str, Any] = {}
    for key, value in dict(selected_params or {}).items():
        key_s = str(key).strip()
        if not key_s or value is None:
            continue
        selected[key_s] = value

    if "Length" not in selected and "OSSE" not in selected and "R-OSSE" not in selected:
        selected["Length"] = _PREVIEW_ATH_MINIMAL_DEFAULTS.get("Length", 120.0)

    gcurve_type = selected.get("GCurve.Type")
    try:
        gcurve_type_num = int(float(gcurve_type)) if gcurve_type is not None else None
    except Exception:
        gcurve_type_num = None
    if gcurve_type_num in {1, 2}:
        selected.setdefault("GCurve.Dist", _PREVIEW_ATH_MINIMAL_DEFAULTS.get("GCurve.Dist", 80.0))
        selected.setdefault("GCurve.Width", _PREVIEW_ATH_MINIMAL_DEFAULTS.get("GCurve.Width", 0.7))
    if gcurve_type_num == 2:
        for sf_key in (
            "GCurve.SF.a",
            "GCurve.SF.b",
            "GCurve.SF.m1",
            "GCurve.SF.m2",
            "GCurve.SF.n1",
            "GCurve.SF.n2",
            "GCurve.SF.n3",
        ):
            selected.setdefault(sf_key, _PREVIEW_ATH_MINIMAL_DEFAULTS.get(sf_key))

    enclosure_value = selected.get("Mesh.Enclosure")
    if isinstance(enclosure_value, Mapping):
        enclosure = dict(enclosure_value)
        plan_name = str(enclosure.get("Plan", "") or "").strip()
        if not plan_name and enclosure.get("Depth") is None:
            enclosure["Depth"] = float(_PREVIEW_ENCLOSURE_DEFAULTS.get("Depth", 180.0))
        selected["Mesh.Enclosure"] = enclosure
    return selected


def _build_preview_render_payload(
    *,
    project: Project,
    selected_params: Mapping[str, Any],
    sweep_mode: str,
) -> Dict[str, Any]:
    runner_mode = str(project.constraints.runner_mode or "")
    catalog_map = _catalog_parameter_map()
    project_values = _project_defined_values(project.constraints)
    selected_user_clean = _non_none_selected_params(selected_params)
    selected_clean = _apply_ath_minimal_selected_defaults(selected_user_clean)

    ignored_hidden_keys: List[str] = []
    auto_completed: Dict[str, Any] = {}
    preview_resolution_issues: List[Dict[str, Any]] = []
    resolver_fallback_used = False

    for _round in range(6):
        temp_batch = Batch(
            batch_id="B_PREVIEW",
            project_id=project.project_id,
            selected_params={
                str(key): ParamSelection(value=value)
                for key, value in dict(selected_clean).items()
                if str(key).strip()
            },
            sweeps={},
            sweep_mode=str(sweep_mode or "single"),
            runner_mode=runner_mode,
        )
        resolved = resolve_versions(project.constraints, temp_batch, strict=False)
        preview_resolution_issues = [issue.to_dict() for issue in list(resolved.issues or [])]
        if resolved.versions:
            version = resolved.versions[0]
            render_parameters = _normalize_preview_render_parameters(
                dict(version.parameters),
                allow_enclosure_plan_mode=False,
            )
            policy_basis = _preview_policy_seed_parameters(project.constraints, selected_user_clean)
            policy_missing_by_block = _missing_preview_policy_by_block(policy_basis)
            policy_missing_keys = _missing_preview_policy_keys(policy_basis)
            policy_default_values = _policy_defaults_for_missing_keys(
                policy_missing_keys,
                context_values=policy_basis,
                catalog_map=catalog_map,
            )
            return {
                "resolver_fallback_used": bool(resolver_fallback_used),
                "resolution_issues": preview_resolution_issues,
                "ignored_hidden_keys": sorted(set(ignored_hidden_keys)),
                "auto_completed": dict(auto_completed),
                "render_parameters": render_parameters,
                "omit_keys": list(version.unset_parameters),
                "completion_tier": "ath_minimal",
                "policy_missing_by_block": dict(policy_missing_by_block),
                "policy_missing_keys": list(policy_missing_keys),
                "policy_default_values": dict(policy_default_values),
            }

        resolver_fallback_used = True
        changed = False
        hidden = _extract_not_visible_batch_keys(list(resolved.issues or []))
        for key in hidden:
            key_s = str(key).strip()
            if key_s in selected_clean:
                selected_clean.pop(key_s, None)
                ignored_hidden_keys.append(key_s)
                changed = True

        required_keys = _extract_required_keys(list(resolved.issues or []))
        selected_clean = _apply_ath_minimal_selected_defaults(selected_clean)
        merged_seed = _preview_seed_parameters(project.constraints, selected_clean)
        context_values = dict(merged_seed)
        context_values.update(project_values)
        for required_key in required_keys:
            key_s = str(required_key).strip()
            if not key_s:
                continue
            if key_s in selected_clean or key_s in project_values:
                continue
            default_value = _default_for_catalog_key(
                key_s,
                current_values=context_values,
                catalog_map=catalog_map,
                tier="ath_minimal",
            )
            if default_value is None:
                continue
            coerced = _coerce_preview_scalar(default_value, key=key_s, catalog=dict(catalog_map.get(key_s, {}) or {}))
            selected_clean[key_s] = coerced
            auto_completed[key_s] = coerced
            context_values[key_s] = coerced
            changed = True

        if not changed:
            break

    merged = _preview_seed_parameters(project.constraints, selected_clean)
    merged = _normalize_preview_render_parameters(
        merged,
        allow_enclosure_plan_mode=False,
    )
    policy_basis = _preview_policy_seed_parameters(project.constraints, selected_user_clean)
    policy_missing_by_block = _missing_preview_policy_by_block(policy_basis)
    policy_missing_keys = _missing_preview_policy_keys(policy_basis)
    policy_default_values = _policy_defaults_for_missing_keys(
        policy_missing_keys,
        context_values=policy_basis,
        catalog_map=catalog_map,
    )
    return {
        "resolver_fallback_used": True,
        "resolution_issues": preview_resolution_issues,
        "ignored_hidden_keys": sorted(set(ignored_hidden_keys)),
        "auto_completed": dict(auto_completed),
        "render_parameters": merged,
        "omit_keys": [],
        "completion_tier": "ath_minimal",
        "policy_missing_by_block": dict(policy_missing_by_block),
        "policy_missing_keys": list(policy_missing_keys),
        "policy_default_values": dict(policy_default_values),
    }


def _preview_policy_seed_parameters(constraints: ProjectConstraints, selected_params: Mapping[str, Any]) -> Dict[str, Any]:
    payload = constraints.to_dict()
    merged: Dict[str, Any] = {}

    fixed = payload.get("fixed_params")
    if isinstance(fixed, Mapping):
        for key, value in fixed.items():
            if value is None:
                continue
            merged[str(key)] = value

    limits = payload.get("limits")
    if isinstance(limits, Mapping):
        for key, value in limits.items():
            if value is None:
                continue
            merged[str(key)] = value

    for row in list(payload.get("param_states", []) or []):
        if not isinstance(row, Mapping):
            continue
        if not bool(row.get("is_set")):
            continue
        key = str(row.get("param_name", "")).strip()
        if not key:
            continue
        value = row.get("value")
        if value is None:
            continue
        merged[key] = value

    for key, value in dict(selected_params or {}).items():
        key_str = str(key).strip()
        if not key_str or value is None:
            continue
        merged[key_str] = value

    # Throat.Profile=2 is an internal UI selector for R-OSSE and must not be
    # emitted into ATH cfg (ATH reports "Unknown profile type: 2").
    if str(merged.get("Throat.Profile", "")).strip() in {"2", "2.0"}:
        merged.pop("Throat.Profile", None)
        merged.setdefault("R-OSSE", {})

    return _normalize_preview_render_parameters(merged)


def _preview_seed_parameters(constraints: ProjectConstraints, selected_params: Mapping[str, Any]) -> Dict[str, Any]:
    merged = _preview_policy_seed_parameters(constraints, selected_params)

    # Keep preview generation robust for incomplete drafts by filling the small
    # set of ATH-minimal keys.
    if "Length" not in merged and "OSSE" not in merged and "R-OSSE" not in merged:
        merged["Length"] = _PREVIEW_ATH_MINIMAL_DEFAULTS.get("Length", 120.0)

    gcurve_type = merged.get("GCurve.Type")
    try:
        gcurve_type_num = int(float(gcurve_type)) if gcurve_type is not None else None
    except Exception:
        gcurve_type_num = None
    if gcurve_type_num in {1, 2}:
        merged.setdefault("GCurve.Dist", _PREVIEW_ATH_MINIMAL_DEFAULTS.get("GCurve.Dist", 80.0))
        merged.setdefault("GCurve.Width", _PREVIEW_ATH_MINIMAL_DEFAULTS.get("GCurve.Width", 0.7))
    if gcurve_type_num == 2:
        for sf_key in (
            "GCurve.SF.a",
            "GCurve.SF.b",
            "GCurve.SF.m1",
            "GCurve.SF.m2",
            "GCurve.SF.n1",
            "GCurve.SF.n2",
            "GCurve.SF.n3",
        ):
            merged.setdefault(sf_key, _PREVIEW_ATH_MINIMAL_DEFAULTS.get(sf_key))

    enclosure_value = merged.get("Mesh.Enclosure")
    if isinstance(enclosure_value, Mapping):
        enclosure = dict(enclosure_value)
        plan_name = str(enclosure.get("Plan", "") or "").strip()
        if not plan_name and enclosure.get("Depth") is None:
            enclosure["Depth"] = float(_PREVIEW_ENCLOSURE_DEFAULTS.get("Depth", 180.0))
        merged["Mesh.Enclosure"] = enclosure

    return _normalize_preview_render_parameters(merged)


def _extract_not_visible_batch_keys(issues: Sequence[Any]) -> List[str]:
    keys: List[str] = []
    for issue in list(issues or []):
        rule_id = str(getattr(issue, "rule_id", "") or "").strip()
        if rule_id != "batch_param_not_visible":
            continue
        message = str(getattr(issue, "message", "") or "")
        match = _BATCH_NOT_VISIBLE_KEY_RE.search(message)
        if not match:
            continue
        key = str(match.group(1) or "").strip()
        if key:
            keys.append(key)
    return sorted(set(keys))


class OrchestratorService:
    def __init__(self, settings_store: SettingsStore | None = None) -> None:
        self.settings_store = settings_store or SettingsStore()
        self.settings = self.settings_store.load()
        self.repo = ProjectRepository(self.settings.library_root)
        self.compatibility = CompatibilityService()

    def reload_settings(self) -> UserSettings:
        self.settings = self.settings_store.load()
        self.repo = ProjectRepository(self.settings.library_root)
        return self.settings

    def save_settings(self, settings: UserSettings) -> Dict[str, Any]:
        self.settings_store.save(settings)
        self.settings = settings
        self.repo = ProjectRepository(self.settings.library_root)
        return {
            "saved": True,
            "path": str(self.settings_store.path),
            "validation": self.settings_store.validate(settings),
        }

    def validate_settings(self, settings: Optional[UserSettings] = None) -> Dict[str, str]:
        return self.settings_store.validate(settings or self.settings)

    def list_projects(self) -> List[Project]:
        return self.repo.list_projects()

    def project_preview_image_path(self, project_id: str) -> Path:
        paths = self.repo.project_paths(str(project_id), ensure=True)
        return paths.project_dir / "_meta" / "project_preview.png"

    def compatibility_catalog_keys(self) -> List[str]:
        return list(self.compatibility.catalog_keys)

    def evaluate_project_constraints(self, constraints: Dict[str, Any]) -> Dict[str, Any]:
        return self.compatibility.evaluate_project_constraints(constraints)

    def evaluate_batch_definition(
        self,
        *,
        project_id: str,
        selected_params: Dict[str, Any],
        sweeps: Dict[str, Dict[str, Any]],
        sweep_mode: str,
    ) -> Dict[str, Any]:
        try:
            project = self.repo.load_project(project_id)
            return self.compatibility.evaluate_batch_definition(
                project.constraints,
                selected_params=selected_params,
                sweeps=sweeps,
                sweep_mode=sweep_mode,
            )
        except FileNotFoundError as exc:
            fallback_state = self.compatibility.evaluate_batch_definition(
                {
                    "project_id": str(project_id),
                    "runner_mode": DEFAULT_RUNNER_MODE,
                    "fixed_params": {},
                    "limits": {},
                    "param_states": [],
                },
                selected_params=selected_params,
                sweeps=sweeps,
                sweep_mode=sweep_mode,
            )
            issues = list(fallback_state.get("issues", []) or [])
            issues.append(
                {
                    "rule_id": "project_missing",
                    "severity": "fatal",
                    "category": "project",
                    "message": str(exc),
                    "source": "storage",
                    "scope": "batch",
                    "evidence_type": "runtime",
                }
            )
            fallback_state["issues"] = issues
            fallback_state["issue_count"] = len(issues)
            fallback_state["project_available"] = False
            return fallback_state

    def evaluate_batch_default_policy(
        self,
        *,
        project_id: str,
        selected_params: Dict[str, Any],
    ) -> Dict[str, Any]:
        project = self.repo.load_project(project_id)
        selected_clean = _non_none_selected_params(selected_params)
        policy_seed = _preview_policy_seed_parameters(project.constraints, selected_clean)
        missing_by_block = _missing_preview_policy_by_block(policy_seed)
        catalog_map = _catalog_parameter_map()
        missing_keys = _missing_preview_policy_keys(policy_seed)
        default_values = _policy_defaults_for_missing_keys(
            missing_keys,
            context_values=policy_seed,
            catalog_map=catalog_map,
        )
        return {
            "tier": "policy_minimal",
            "missing_keys": list(missing_keys),
            "missing_by_block": dict(missing_by_block),
            "default_values": default_values,
            "policy_seed": policy_seed,
            "ath_minimal_seed": _preview_seed_parameters(project.constraints, selected_clean),
        }

    def cleanup_preview_cache(
        self,
        *,
        keep_last: int = PREVIEW_CACHE_KEEP_FILES,
        max_age_days: int = PREVIEW_CACHE_MAX_AGE_DAYS,
    ) -> Dict[str, Any]:
        cache_dir = _preview_cache_dir()
        stats = _prune_preview_cache(cache_dir, keep_last=keep_last, max_age_days=max_age_days)
        return {"cache_dir": str(cache_dir), **stats}

    def generate_preview_stl(
        self,
        *,
        project_id: str,
        selected_params: Dict[str, Any],
        sweep_mode: str = "single",
        run_id: Optional[str] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        process_handle_cb: Optional[Callable[[subprocess.Popen[str]], None]] = None,
    ) -> Dict[str, Any]:
        if cancel_check and bool(cancel_check()):
            raise PreviewGenerationCancelled("Preview request cancelled before start.")

        ath_exe = str(self.settings.ath_exe or "").strip()
        if not ath_exe:
            fallback = Path(ATH_PREVIEW_CFG_DIR) / "ath.exe"
            if fallback.exists():
                ath_exe = str(fallback)
        if not ath_exe:
            raise ValueError("ATH executable is not configured for preview generation.")

        ath_executable = Path(ath_exe).expanduser()
        if not ath_executable.exists():
            raise FileNotFoundError(f"ATH executable not found: {ath_executable}")

        project = self.repo.load_project(project_id)
        preview_payload = _build_preview_render_payload(
            project=project,
            selected_params=dict(selected_params or {}),
            sweep_mode=str(sweep_mode or "single"),
        )
        render_parameters = dict(preview_payload.get("render_parameters", {}) or {})
        selected_enclosure = dict(selected_params.get("Mesh.Enclosure", {}) or {}) if isinstance(selected_params.get("Mesh.Enclosure"), Mapping) else {}
        render_enclosure = dict(render_parameters.get("Mesh.Enclosure", {}) or {}) if isinstance(render_parameters.get("Mesh.Enclosure"), Mapping) else {}
        preview_notes: List[str] = []
        requested_plan = str(selected_enclosure.get("Plan", "") or "").strip()
        rendered_plan = str(render_enclosure.get("Plan", "") or "").strip()
        if requested_plan and not rendered_plan:
            preview_notes.append(
                "Mesh.Enclosure.Plan was ignored for preview STL. "
                "Plan-mode enclosures require an in-CFG plan script block; preview uses stock enclosure fallback."
            )
        omit_keys = list(preview_payload.get("omit_keys", []) or [])
        preview_resolution_issues = [
            dict(item)
            for item in list(preview_payload.get("resolution_issues", []) or [])
            if isinstance(item, Mapping)
        ]
        resolver_fallback_used = bool(preview_payload.get("resolver_fallback_used", False))
        ignored_hidden_keys = [
            str(item)
            for item in list(preview_payload.get("ignored_hidden_keys", []) or [])
            if str(item).strip()
        ]
        auto_completed = {
            str(key): value
            for key, value in dict(preview_payload.get("auto_completed", {}) or {}).items()
            if str(key).strip()
        }
        completion_tier = str(preview_payload.get("completion_tier", "ath_minimal") or "ath_minimal")
        policy_missing_by_block = {
            str(block): [str(item) for item in list(items or []) if str(item).strip()]
            for block, items in dict(preview_payload.get("policy_missing_by_block", {}) or {}).items()
            if str(block).strip()
        }
        policy_missing_keys = [
            str(item)
            for item in list(preview_payload.get("policy_missing_keys", []) or [])
            if str(item).strip()
        ]
        policy_default_values = dict(preview_payload.get("policy_default_values", {}) or {})

        template_text = "; autogenerated template\n"
        if self.settings.template_cfg:
            template_text = Path(self.settings.template_cfg).read_text(encoding="utf-8")

        cfg_text = render_cfg_text(
            template_text=template_text,
            parameters=render_parameters,
            version_id="V_PREVIEW",
            runner_mode=project.constraints.runner_mode,
            omit_keys=omit_keys,
        )
        cfg_text = _enforce_output_flag(cfg_text, key="Output.STL", value=1)
        cfg_text = _enforce_output_flag(cfg_text, key="Output.ABECProject", value=0)

        cfg_hash = hashlib.sha1(cfg_text.encode("utf-8")).hexdigest()[:10]
        run_token = str(run_id or _now_iso().replace(":", "").replace("-", ""))

        cfg_dir = Path(ATH_PREVIEW_CFG_DIR)
        export_root = Path(ATH_PREVIEW_EXPORT_ROOT)
        cache_dir = _preview_cache_dir()
        logs_dir = cache_dir / "logs"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        export_root.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)

        cfg_path = cfg_dir / ATH_PREVIEW_CFG_NAME
        stdout_log = logs_dir / f"preview_{run_token}.stdout.log"
        stderr_log = logs_dir / f"preview_{run_token}.stderr.log"
        summary_log = logs_dir / f"preview_{run_token}.runner.log"

        before_snapshot = _snapshot_subdirs(export_root)
        cfg_path.write_text(cfg_text, encoding="utf-8")

        backup_path = cfg_dir / "ath.wut_preview.backup.cfg"
        runtime_cfg_path = cfg_dir / "ath.cfg"
        had_runtime_cfg = runtime_cfg_path.exists()
        if had_runtime_cfg:
            try:
                shutil.copy2(runtime_cfg_path, backup_path)
            except Exception as exc:
                LOGGER.warning("Failed to backup ATH runtime cfg at %s: %s", runtime_cfg_path, exc)
                had_runtime_cfg = False

        existing_mesh_cmd = _extract_mesh_cmd_from_runtime_cfg(runtime_cfg_path)
        mesh_cmd = _best_mesh_cmd_for_preview(ath_executable, fallback_cmd=existing_mesh_cmd)
        mesh_cmd_for_runtime = _mesh_cmd_for_preview_runtime(cfg_dir=cfg_dir, mesh_cmd=mesh_cmd)
        _write_preview_runtime_cfg(cfg_dir, export_root=export_root, mesh_cmd=mesh_cmd_for_runtime)

        command = [str(ath_executable), str(cfg_path)]
        proc: Optional[subprocess.Popen[str]] = None
        started_at = _now_iso()
        exit_code = -1
        preview_timeout_s = 90.0

        try:
            with stdout_log.open("w", encoding="utf-8") as stdout_handle, stderr_log.open(
                "w", encoding="utf-8"
            ) as stderr_handle:
                proc = subprocess.Popen(
                    command,
                    cwd=str(cfg_dir),
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                if process_handle_cb is not None:
                    process_handle_cb(proc)

                started_monotonic = time.monotonic()
                while True:
                    if cancel_check and bool(cancel_check()):
                        _terminate_process(proc)
                        raise PreviewGenerationCancelled("Preview request cancelled.")
                    if (time.monotonic() - started_monotonic) > preview_timeout_s:
                        _terminate_process(proc)
                        raise RuntimeError(f"ATH preview run timed out after {int(preview_timeout_s)}s.")
                    rc = proc.poll()
                    if rc is not None:
                        exit_code = int(rc)
                        break
                    time.sleep(0.20)
        finally:
            finished_at = _now_iso()
            summary_log.write_text(
                json.dumps(
                    {
                        "started_at": started_at,
                        "finished_at": finished_at,
                        "command": command,
                        "cfg_path": str(cfg_path),
                        "run_id": run_token,
                        "exit_code": exit_code,
                        "export_root": str(export_root),
                        "mesh_cmd": str(mesh_cmd_for_runtime),
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            if had_runtime_cfg and backup_path.exists():
                try:
                    shutil.copy2(backup_path, runtime_cfg_path)
                except Exception as exc:
                    LOGGER.warning("Failed to restore ATH runtime cfg from backup %s: %s", backup_path, exc)
            elif (not had_runtime_cfg) and runtime_cfg_path.exists():
                try:
                    runtime_cfg_path.unlink()
                except Exception as exc:
                    LOGGER.warning("Failed to remove temporary ATH runtime cfg %s: %s", runtime_cfg_path, exc)
            if backup_path.exists():
                try:
                    backup_path.unlink()
                except Exception as exc:
                    LOGGER.warning("Failed to remove ATH runtime cfg backup %s: %s", backup_path, exc)

        if exit_code != 0:
            raise RuntimeError(f"ATH preview run failed (exit_code={exit_code}).")

        expected_export_dir = export_root / Path(ATH_PREVIEW_CFG_NAME).stem
        export_dir = expected_export_dir if expected_export_dir.exists() else _detect_changed_export_dir(export_root, before_snapshot)
        source_stl = _pick_latest_stl(export_dir) if export_dir is not None else None
        if source_stl is None:
            source_stl = _pick_latest_stl(expected_export_dir)
        if source_stl is None:
            source_stl = _pick_latest_stl(export_root)
        if source_stl is None:
            raise RuntimeError(f"No STL artifact generated under {export_root}.")
        if source_stl.stat().st_size <= 0:
            raise RuntimeError(f"Generated STL is empty: {source_stl}")

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target_stl = cache_dir / f"horn_preview_{stamp}_{cfg_hash}.stl"
        shutil.copy2(source_stl, target_stl)

        prune_stats = _prune_preview_cache(
            cache_dir,
            keep_last=PREVIEW_CACHE_KEEP_FILES,
            max_age_days=PREVIEW_CACHE_MAX_AGE_DAYS,
        )
        return {
            "ok": True,
            "run_id": run_token,
            "cfg_hash": cfg_hash,
            "resolver_fallback_used": bool(resolver_fallback_used),
            "resolution_issues": preview_resolution_issues,
            "ignored_hidden_keys": ignored_hidden_keys,
            "auto_completed": auto_completed,
            "completion_tier": completion_tier,
            "policy_missing_by_block": policy_missing_by_block,
            "policy_missing_keys": policy_missing_keys,
            "policy_default_values": policy_default_values,
            "preview_notes": preview_notes,
            "cfg_path": str(cfg_path),
            "command": command,
            "export_root": str(export_root),
            "export_dir": None if export_dir is None else str(export_dir),
            "source_stl": str(source_stl),
            "cache_stl": str(target_stl),
            "cache_dir": str(cache_dir),
            "stdout_log": str(stdout_log),
            "stderr_log": str(stderr_log),
            "summary_log": str(summary_log),
            "retention": prune_stats,
        }

    def estimate_batch_runtime(
        self,
        *,
        project_id: str,
        selected_params: Dict[str, Any],
        sweeps: Dict[str, Dict[str, Any]],
        sweep_mode: str,
        batch_id: Optional[str] = None,
        validation_state: Optional[Dict[str, Any]] = None,
        sample_limit: int = 200,
    ) -> Dict[str, Any]:
        state = dict(validation_state or {})
        if not state:
            state = self.evaluate_batch_definition(
                project_id=project_id,
                selected_params=selected_params,
                sweeps=sweeps,
                sweep_mode=sweep_mode,
            )
        version_count_preview = int(state.get("version_count_preview", 0) or 0)
        project_paths = self.repo.project_paths(project_id, ensure=True)
        dataset = TidyDatasetWriter(project_paths.project_dir, library_root=self.settings.library_root)
        durations = dataset.list_recent_success_durations(limit=sample_limit, batch_id=batch_id)
        sample_count = len(durations)
        sorted_durations = sorted(float(item) for item in durations)
        median_seconds = float(statistics.median(sorted_durations)) if sorted_durations else None
        eta_seconds = None if median_seconds is None else float(max(version_count_preview, 0) * median_seconds)
        return {
            "version_count_preview": version_count_preview,
            "eta_seconds": eta_seconds,
            "median_seconds_per_version": median_seconds,
            "sample_count": sample_count,
            "basis_stats": {
                "sample_count": sample_count,
                "median_seconds": median_seconds,
                "p25_seconds": _percentile(sorted_durations, 0.25),
                "p75_seconds": _percentile(sorted_durations, 0.75),
            },
        }

    def list_versions(self, project_id: str, batch_id: Optional[str] = None) -> List[Dict[str, Any]]:
        project_paths = self.repo.project_paths(project_id, ensure=True)
        dataset = TidyDatasetWriter(project_paths.project_dir, library_root=self.settings.library_root)
        if batch_id:
            latest_success = dataset.latest_successful_run_per_version(batch_id)
            if latest_success:
                return [
                    {
                        "version_id": str(row["version_id"]),
                        "batch_id": batch_id,
                        "status": str(row["status"]),
                        "created_at": row["started_at"],
                        "finished_at": row["finished_at"],
                        "run_id": str(row["run_id"]),
                    }
                    for row in latest_success
                ]
        else:
            rows: List[Dict[str, Any]] = []
            for batch in self.repo.list_batches(project_id):
                latest_success = dataset.latest_successful_run_per_version(batch.batch_id)
                rows.extend(
                    {
                        "version_id": str(item["version_id"]),
                        "batch_id": batch.batch_id,
                        "status": str(item["status"]),
                        "created_at": item["started_at"],
                        "finished_at": item["finished_at"],
                        "run_id": str(item["run_id"]),
                    }
                    for item in latest_success
                )
            if rows:
                return sorted(rows, key=lambda item: (str(item["batch_id"]), str(item["version_id"])))

        project_db = project_paths.dataset_dir / "project.sqlite"
        if not project_db.exists():
            return []
        with closing(sqlite3.connect(str(project_db))) as conn:
            if batch_id:
                rows = conn.execute(
                    """
                    SELECT version_id, batch_id, status, created_at, finished_at
                    FROM versions
                    WHERE project_id = ? AND batch_id = ?
                    ORDER BY version_id
                    """,
                    (project_id, batch_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT version_id, batch_id, status, created_at, finished_at
                    FROM versions
                    WHERE project_id = ?
                    ORDER BY batch_id, version_id
                    """,
                    (project_id,),
                ).fetchall()
        return [
            {
                "version_id": str(row[0]),
                "batch_id": str(row[1]),
                "status": str(row[2]),
                "created_at": row[3],
                "finished_at": row[4],
                "run_id": None,
            }
            for row in rows
        ]

    def list_runs(
        self,
        *,
        project_id: str,
        batch_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        project_paths = self.repo.project_paths(project_id, ensure=True)
        dataset = TidyDatasetWriter(project_paths.project_dir, library_root=self.settings.library_root)
        return dataset.list_runs(batch_id=batch_id, status=status)

    def pin_run(self, *, project_id: str, run_id: str, tag: Optional[str] = None) -> Dict[str, Any]:
        project_paths = self.repo.project_paths(project_id, ensure=True)
        dataset = TidyDatasetWriter(project_paths.project_dir, library_root=self.settings.library_root)
        return dataset.set_run_pin(run_id, pinned=True, tag=tag)

    def unpin_run(self, *, project_id: str, run_id: str) -> Dict[str, Any]:
        project_paths = self.repo.project_paths(project_id, ensure=True)
        dataset = TidyDatasetWriter(project_paths.project_dir, library_root=self.settings.library_root)
        return dataset.set_run_pin(run_id, pinned=False, tag=None)

    def cleanup_test_data(
        self,
        *,
        project_id: str,
        delete_exports: bool,
        dry_run: bool,
    ) -> Dict[str, Any]:
        project_paths = self.repo.project_paths(project_id, ensure=True)
        dataset = TidyDatasetWriter(project_paths.project_dir, library_root=self.settings.library_root)
        return dataset.cleanup_unpinned_runs(delete_exports=delete_exports, dry_run=dry_run)

    def sync_global_db(self, max_items_per_project: int = 100) -> Dict[str, Any]:
        results: List[Dict[str, Any]] = []
        total_processed = 0
        total_synced = 0
        total_failed = 0
        for project in self.repo.list_projects():
            writer = TidyDatasetWriter(
                self.repo.project_paths(project.project_id, ensure=True).project_dir,
                library_root=self.settings.library_root,
            )
            summary = writer.retry_pending_global_writes(max_items=max_items_per_project)
            results.append({"project_id": project.project_id, **summary})
            total_processed += int(summary.get("processed", 0))
            total_synced += int(summary.get("synced", 0))
            total_failed += int(summary.get("failed", 0))
        return {
            "projects": results,
            "processed": total_processed,
            "synced": total_synced,
            "failed": total_failed,
        }

    def create_project(self, project_name: str, constraints: Dict[str, Any]) -> Project:
        existing = self.repo.list_projects()
        project_id = _next_prefixed_id([project.project_id for project in existing], "P")
        project_root = self.repo.project_paths(project_id, ensure=False).project_dir
        project = Project(
            project_id=project_id,
            name=project_name.strip() or project_id,
            root_path=str(project_root),
            constraints=ProjectConstraints.from_dict(
                {
                    "project_id": project_id,
                    "fixed_params": dict(constraints.get("fixed_params", {}) or {}),
                    "limits": dict(constraints.get("limits", {}) or {}),
                    "param_states": [
                        item for item in list(constraints.get("param_states", []) or []) if isinstance(item, dict)
                    ],
                    "runner_mode": str(constraints.get("runner_mode") or "AkabakImportFixedSource"),
                    "notes": constraints.get("notes"),
                }
            ),
        )
        self.repo.init_project(project)
        TidyDatasetWriter(project_root, library_root=self.settings.library_root).register_project(project)
        return project

    def create_batch(
        self,
        *,
        project_id: str,
        batch_name: str,
        selected_params: Dict[str, Any],
        sweeps: Dict[str, Dict[str, Any]],
        sweep_mode: str,
        sim_export_params: Dict[str, Any],
    ) -> PlanningSummary:
        project = self.repo.load_project(project_id)
        batches = self.repo.list_batches(project_id)
        batch_id = _next_prefixed_id([batch.batch_id for batch in batches], "B")
        locked_keys = set(self.compatibility.runner_locked_keys(project.constraints.runner_mode))

        selected: Dict[str, ParamSelection] = {}
        for key, value in selected_params.items():
            if str(key) in locked_keys:
                continue
            selected[str(key)] = ParamSelection(value=value)

        normalized_sweeps: Dict[str, SweepSpec] = {}
        for key, payload in sweeps.items():
            if str(key) in locked_keys:
                continue
            normalized_sweeps[str(key)] = SweepSpec.from_dict(dict(payload), key=str(key))

        batch = Batch(
            batch_id=batch_id,
            project_id=project_id,
            selected_params=selected,
            sweeps=normalized_sweeps,
            sweep_mode=sweep_mode if sweep_mode in {"single", "combined"} else "single",
            runner_mode=project.constraints.runner_mode,
            extra={"batch_name": batch_name.strip() or batch_id},
        )

        sim_settings = dict(sim_export_params or {})
        if sim_settings:
            batch.sim_export_settings = batch.sim_export_settings.from_dict(sim_settings)

        return materialize_batch_plan(project, batch, projects_root=self.settings.library_root)

    def resolve_versions(self, project_id: str, batch_id: str) -> Dict[str, Any]:
        project = self.repo.load_project(project_id)
        batch = self.repo.load_batch(project_id, batch_id)
        existing_ids = self.repo.existing_version_ids(project_id)
        resolved = resolve_versions(project.constraints, batch, existing_version_ids=existing_ids, strict=False)
        return {
            "version_count": len(resolved.versions),
            "versions": [asdict(version) for version in resolved.versions],
            "issues": [issue.to_dict() for issue in resolved.issues],
        }

    def run_batch(
        self,
        project_id: str,
        batch_id: str,
        *,
        continue_on_error: bool = True,
        dry_run: Optional[bool] = None,
    ) -> RuntimeSummary:
        project = self.repo.load_project(project_id)
        batch = self.repo.load_batch(project_id, batch_id)
        if dry_run is None:
            tools = [self.settings.ath_exe, self.settings.akabak_exe, self.settings.vacs_exe]
            dry_run = not all(_is_executable_path(path) for path in tools)
        return run_batch_pipeline(
            project=project,
            batch=batch,
            projects_root=self.settings.library_root,
            template_cfg_path=self.settings.template_cfg,
            ath_executable=self.settings.ath_exe if not dry_run else None,
            akabak_executable=self.settings.akabak_exe if not dry_run else None,
            vacs_executable=self.settings.vacs_exe if not dry_run else None,
            continue_on_error=continue_on_error,
            dry_run=bool(dry_run),
            git_commit=_detect_git_commit(),
            app_version="0.1-rebuild",
            settings_hash=_settings_hash(self.settings),
            ath_export_root=ATH_PREVIEW_EXPORT_ROOT,
        )

    def export_version(
        self,
        *,
        project_id: str,
        batch_id: str,
        version_id: str,
        export_stl: bool,
        export_abec: bool,
    ) -> Dict[str, Any]:
        project = self.repo.load_project(project_id)
        project_paths = self.repo.project_paths(project_id, ensure=True)
        dataset = TidyDatasetWriter(project_paths.project_dir, library_root=self.settings.library_root)
        set_params, unset_params = dataset.reconstruct_cfg_parameters(version_id)
        metadata = dataset.load_version_metadata(version_id)

        export_dir = project_paths.project_dir / "exports" / batch_id / version_id
        export_dir.mkdir(parents=True, exist_ok=True)
        logs_dir = export_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        template_text = "; autogenerated template\n"
        if self.settings.template_cfg:
            template_text = Path(self.settings.template_cfg).read_text(encoding="utf-8")

        cfg_text = render_cfg_text(
            template_text=template_text,
            parameters=set_params,
            version_id=version_id,
            runner_mode=project.constraints.runner_mode,
            omit_keys=unset_params,
        )
        stl_todo_added = False
        if export_stl:
            cfg_text, stl_todo_added = _apply_stl_export_hook(cfg_text)

        cfg_path = export_dir / f"{version_id}_export.cfg"
        cfg_path.write_text(cfg_text, encoding="utf-8")

        if (export_stl or export_abec) and not self.settings.ath_exe:
            raise ValueError("ATH executable is required for STL/ABEC export regeneration but is not configured.")

        ath_result: Optional[Dict[str, Any]] = None
        if (export_stl or export_abec) and self.settings.ath_exe:
            runner = AthRunner(self.settings.ath_exe)
            result = runner.run_cfg(
                cfg_path,
                version_logs_dir=logs_dir,
                workdir=export_dir,
            )
            ath_result = {
                "ok": result.ok,
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "stdout_log": result.stdout_log,
                "stderr_log": result.stderr_log,
                "summary_log": result.summary_log,
            }
            if not result.ok:
                raise RuntimeError(f"ATH export run failed for {version_id} (exit_code={result.exit_code})")

        exported_abec: Optional[str] = None
        abec_source: Optional[str] = None
        le_driver_sync: Optional[Dict[str, Any]] = None
        if export_abec:
            target_abec = export_dir / "Project.abec"
            generated = _select_generated_abec(export_dir)
            if generated is None:
                raise RuntimeError(
                    f"ABEC export requested for {version_id}, but no .abec artifact was generated in {export_dir}."
                )
            if generated.resolve() != target_abec.resolve():
                shutil.copy2(generated, target_abec)
            abec_source = str(generated)
            exported_abec = str(target_abec)
            sync_result = repair_post_ath_le_binding(
                abec_path=target_abec,
                ath_executable=self.settings.ath_exe,
            )
            le_driver_sync = sync_result.to_dict()
            if not sync_result.ok:
                raise RuntimeError(
                    "Failed post-ATH LE repair in ABEC export folder: "
                    f"status={sync_result.status} error={sync_result.error or 'n/a'}"
                )

        exported_stl: List[str] = []
        if export_stl:
            for candidate in export_dir.glob("*.stl"):
                exported_stl.append(str(candidate))

        manifest = {
            "project_id": project_id,
            "batch_id": batch_id,
            "version_id": version_id,
            "created_at": _now_iso(),
            "export_dir": str(export_dir),
            "cfg_path": str(cfg_path),
            "unset_params": unset_params,
            "param_count": len(set_params),
            "ath_result": ath_result,
            "exported_abec": exported_abec,
            "exported_abec_source": abec_source,
            "le_driver_sync": le_driver_sync,
            "exported_stl": exported_stl,
            "stl_export_todo": stl_todo_added,
            "stl_directive": ATH_STL_EXPORT_DIRECTIVE,
            "stl_todo_note": (
                "Exact ATH STL export directive is not yet known; TODO placeholder appended to CFG."
                if stl_todo_added
                else None
            ),
            "version_metadata": metadata,
        }
        manifest_path = export_dir / "export_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return manifest
