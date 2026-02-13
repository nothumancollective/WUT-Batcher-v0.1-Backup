"""CFG rendering with runner compatibility enforcement."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from app.ath_knowledge import AthKnowledgeBundle, load_ath_knowledge
from app.constants import DEFAULT_RUNNER_MODE, MANDATORY_SOURCE_BLOCK


_ASSIGN_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*=")


def _catalog_keys(bundle: AthKnowledgeBundle) -> List[str]:
    keys: List[str] = []
    for item in bundle.catalog.get("parameters", []):
        if isinstance(item, dict):
            key = item.get("key")
            if isinstance(key, str):
                keys.append(key)
    return keys


def _runner_locked_keys(bundle: AthKnowledgeBundle, runner_mode: str) -> set[str]:
    restrictions = bundle.ruleset.get("runner_restrictions", {})
    if not isinstance(restrictions, dict):
        return set()
    if restrictions.get("runner_mode") != runner_mode:
        return set()
    raw = restrictions.get("locked_or_hidden_keys", [])
    if not isinstance(raw, list):
        return set()
    return {str(item) for item in raw}


def _format_value(value: Any, *, keep_float_trailing_zero: bool = False) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return f"{value:.1f}" if keep_float_trailing_zero else str(int(value))
        return f"{value:g}"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _format_geometry_line(key: str, value: Any) -> str:
    return f"{key:<17}= {_format_value(value)}"


def _format_mandatory_line(key: str, value: Any) -> str:
    if key == "ABEC.AkabakMode":
        return f"{key:<19}= {_format_value(value)}"
    return f"{key:<12}= {_format_value(value, keep_float_trailing_zero=True)}"


def build_cfg_updates(
    parameters: Dict[str, Any],
    runner_mode: str = DEFAULT_RUNNER_MODE,
    bundle: AthKnowledgeBundle | None = None,
) -> Dict[str, Any]:
    bundle = bundle or load_ath_knowledge()
    allowed_keys = set(_catalog_keys(bundle))
    locked = _runner_locked_keys(bundle, runner_mode)
    mandatory_keys = {key for key, _ in MANDATORY_SOURCE_BLOCK}

    updates: Dict[str, Any] = {}
    for key, value in dict(parameters or {}).items():
        if key in mandatory_keys:
            continue
        if key in locked:
            continue
        if key in allowed_keys:
            updates[key] = value
    return updates


def render_cfg_text(
    template_text: str,
    parameters: Dict[str, Any],
    version_id: str,
    runner_mode: str = DEFAULT_RUNNER_MODE,
    bundle: AthKnowledgeBundle | None = None,
) -> str:
    bundle = bundle or load_ath_knowledge()
    updates = build_cfg_updates(parameters=parameters, runner_mode=runner_mode, bundle=bundle)
    locked = _runner_locked_keys(bundle, runner_mode)
    mandatory_keys = {key for key, _ in MANDATORY_SOURCE_BLOCK}
    suppressed = locked | mandatory_keys

    lines = template_text.splitlines()
    rendered: List[str] = []
    consumed_updates: set[str] = set()

    for raw_line in lines:
        match = _ASSIGN_RE.match(raw_line)
        if not match:
            rendered.append(raw_line)
            continue

        key = match.group(1).strip()
        if key in suppressed:
            continue
        if key in updates:
            rendered.append(_format_geometry_line(key, updates[key]))
            consumed_updates.add(key)
            continue
        rendered.append(raw_line)

    remaining_keys = sorted((set(updates.keys()) - consumed_updates))
    if remaining_keys:
        if rendered and rendered[-1].strip():
            rendered.append("")
        rendered.append("; --- appended geometry updates ---")
        for key in remaining_keys:
            rendered.append(_format_geometry_line(key, updates[key]))

    if rendered and rendered[-1].strip():
        rendered.append("")
    rendered.append("; ----- AKABAK import compatibility -----")
    for key, value in MANDATORY_SOURCE_BLOCK[:1]:
        rendered.append(_format_mandatory_line(key, value))
    rendered.append('; ----- Drive without needing AKABAK "Fixed Driving" -----')
    for key, value in MANDATORY_SOURCE_BLOCK[1:]:
        rendered.append(_format_mandatory_line(key, value))

    return "\n".join(rendered) + "\n"


def render_cfg_file(
    template_path: Path,
    output_path: Path,
    parameters: Dict[str, Any],
    version_id: str,
    runner_mode: str = DEFAULT_RUNNER_MODE,
    bundle: AthKnowledgeBundle | None = None,
) -> str:
    template_text = template_path.read_text(encoding="utf-8")
    cfg_text = render_cfg_text(
        template_text=template_text,
        parameters=parameters,
        version_id=version_id,
        runner_mode=runner_mode,
        bundle=bundle,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(cfg_text, encoding="utf-8")
    return cfg_text
