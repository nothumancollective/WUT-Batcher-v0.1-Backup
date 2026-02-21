"""LEGACY QUARANTINED: non-shipping JSON payload migration helpers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Tuple

from app.constants import DEFAULT_RUNNER_MODE, SUPPORTED_RUNNER_MODES


def migrate_constraints_payload(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    migrated = deepcopy(payload) if isinstance(payload, dict) else {}
    changed = False

    if "schema_version" not in migrated:
        migrated["schema_version"] = "1.1"
        changed = True
    if "template_family" not in migrated:
        migrated["template_family"] = "ath_geometry_v1"
        changed = True

    runner_mode = migrated.get("runner_mode")
    if not isinstance(runner_mode, str) or runner_mode not in SUPPORTED_RUNNER_MODES:
        migrated["runner_mode"] = DEFAULT_RUNNER_MODE
        changed = True

    if "notes" not in migrated:
        migrated["notes"] = None
        changed = True

    fixed_params = migrated.get("fixed_params")
    if not isinstance(fixed_params, dict):
        migrated["fixed_params"] = {}
        changed = True

    limits = migrated.get("limits")
    if not isinstance(limits, dict):
        migrated["limits"] = {}
        changed = True

    return migrated, changed


def _normalize_sweep_mode(payload: Dict[str, Any]) -> str:
    raw = payload.get("sweep_mode")
    if isinstance(raw, str) and raw in {"single", "combined"}:
        return raw

    legacy_mode = str(payload.get("mode", "oat")).lower()
    if legacy_mode in {"factorial", "both"}:
        return "combined"
    return "single"


def migrate_batch_payload(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    migrated = deepcopy(payload) if isinstance(payload, dict) else {}
    changed = False

    if "schema_version" not in migrated:
        migrated["schema_version"] = "1.1"
        changed = True

    expected_sweep_mode = _normalize_sweep_mode(migrated)
    if migrated.get("sweep_mode") != expected_sweep_mode:
        migrated["sweep_mode"] = expected_sweep_mode
        changed = True

    if "runner_mode" not in migrated:
        migrated["runner_mode"] = DEFAULT_RUNNER_MODE
        changed = True
    elif migrated.get("runner_mode") not in SUPPORTED_RUNNER_MODES:
        migrated["runner_mode"] = DEFAULT_RUNNER_MODE
        changed = True

    if not isinstance(migrated.get("selected_params"), dict):
        migrated["selected_params"] = {}
        changed = True

    sweeps = migrated.get("sweeps")
    if isinstance(sweeps, list):
        normalized: Dict[str, Dict[str, Any]] = {}
        for item in sweeps:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key", "")).strip()
            if not key:
                continue
            normalized[key] = {
                "start": item.get("start"),
                "end": item.get("end"),
                "steps": item.get("steps"),
                "spacing": item.get("spacing", "linear"),
            }
        migrated["sweeps"] = normalized
        changed = True
    elif not isinstance(sweeps, dict):
        migrated["sweeps"] = {}
        changed = True

    sim_export_settings = migrated.get("sim_export_settings")
    if not isinstance(sim_export_settings, dict):
        migrated["sim_export_settings"] = {}
        changed = True

    return migrated, changed
