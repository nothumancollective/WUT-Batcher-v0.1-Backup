"""LEGACY QUARANTINED: non-shipping UI-only risk hints."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from app.compat_engine import _safe_eval_when


_KEY_TOKEN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_IDENT_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_.]*)\b")
_FUNC_NAMES = {
    "and",
    "or",
    "not",
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "isDefined",
}
_CONFIDENCE_MAP = {"low": 0.4, "medium": 0.7, "high": 0.9}
_DIM_PROXY_KEYS = ("Length", "Morph.TargetWidth", "Morph.TargetHeight")


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    raw = value.strip().replace(",", ".")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _is_constant_expr(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (int, float, bool)):
        return True
    if not isinstance(value, str):
        return False
    return re.search(r"\bp\b", value, flags=re.IGNORECASE) is None


def _fmt_num(value: Optional[float]) -> str:
    if value is None:
        return "-"
    if abs(value - int(value)) < 1e-9:
        return str(int(value))
    return f"{value:.4g}"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _confidence_score(raw: Any) -> float:
    if isinstance(raw, (int, float)):
        return float(raw)
    token = str(raw or "").strip().lower()
    if token in _CONFIDENCE_MAP:
        return _CONFIDENCE_MAP[token]
    try:
        return float(token)
    except ValueError:
        return 0.0


def _message_from_action(action: Any) -> str:
    text = str(action or "").strip()
    if not text:
        return "Risk rule matched."
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        return text
    body = tree.body
    if not isinstance(body, ast.Call) or not body.args:
        return text
    first = body.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return text


def _attr_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        root = _attr_name(node.value)
        if not root:
            return ""
        return f"{root}.{node.attr}"
    return ""


def _rewrite_function_dsl(expr: str) -> Optional[str]:
    if not str(expr or "").strip():
        return None
    source = str(expr).strip()
    source = re.sub(r"\band\s*\(", "and_(", source)
    source = re.sub(r"\bor\s*\(", "or_(", source)
    source = re.sub(r"\bnot\s*\(", "not_(", source)
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError:
        return None

    def emit_operand(node: ast.AST, *, key_hint: bool) -> Optional[str]:
        if isinstance(node, ast.Constant):
            value = node.value
            if isinstance(value, str):
                token = value.strip()
                if key_hint and _KEY_TOKEN_RE.match(token):
                    return token
                return repr(token)
            return repr(value)
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            token = _attr_name(node)
            return token or None
        if isinstance(node, ast.Call):
            return emit_call(node)
        return None

    def emit_call(node: ast.Call) -> Optional[str]:
        if not isinstance(node.func, ast.Name):
            return None
        name = node.func.id
        args = list(node.args)

        if name in {"and_", "or_"}:
            if len(args) < 2:
                return None
            op = " and " if name == "and_" else " or "
            parts: List[str] = []
            for arg in args:
                rendered = emit_operand(arg, key_hint=False)
                if not rendered:
                    return None
                parts.append(rendered)
            return "(" + op.join(parts) + ")"

        if name == "not_":
            if len(args) != 1:
                return None
            rendered = emit_operand(args[0], key_hint=False)
            if not rendered:
                return None
            return f"(not {rendered})"

        if name in {"eq", "ne", "gt", "gte", "lt", "lte"}:
            if len(args) != 2:
                return None
            op_map = {"eq": "==", "ne": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
            left = emit_operand(args[0], key_hint=True)
            right = emit_operand(args[1], key_hint=False)
            if not left or not right:
                return None
            return f"({left} {op_map[name]} {right})"

        if name == "isDefined":
            if len(args) != 1:
                return None
            arg = emit_operand(args[0], key_hint=True)
            if not arg:
                return None
            return f"isDefined({arg})"

        return None

    rendered = emit_operand(tree.body, key_hint=False)
    return rendered


def _extract_draft_values(draft_payload: Mapping[str, Any]) -> Tuple[Dict[str, Any], Set[str]]:
    values: Dict[str, Any] = {}
    set_keys: Set[str] = set()
    fixed = draft_payload.get("fixed_params")
    if isinstance(fixed, dict):
        for key, value in fixed.items():
            values[str(key)] = value
            set_keys.add(str(key))
    limits = draft_payload.get("limits")
    if isinstance(limits, dict):
        for key, value in limits.items():
            values[str(key)] = value
            set_keys.add(str(key))

    param_states = draft_payload.get("param_states")
    if isinstance(param_states, list):
        for item in param_states:
            if not isinstance(item, dict):
                continue
            key = str(item.get("param_name", "")).strip()
            if not key:
                continue
            is_set = bool(item.get("is_set"))
            if is_set:
                values[key] = item.get("value")
                set_keys.add(key)
            elif key in values:
                values.pop(key, None)
                set_keys.discard(key)
    return values, set_keys


def _to_evidence_ref(raw: Any) -> str:
    if isinstance(raw, dict):
        label = str(raw.get("label", "")).strip()
        if label:
            return label
        if "type" in raw:
            return str(raw.get("type"))
    if isinstance(raw, list):
        if raw:
            return str(raw[0])
        return ""
    return str(raw or "")


@dataclass(frozen=True)
class _RangeHint:
    safe_min: Optional[float]
    safe_max: Optional[float]
    rec_p05: Optional[float]
    rec_p95: Optional[float]
    notes: str


@dataclass(frozen=True)
class _CandidateRule:
    rule_id: str
    kind: str
    when_raw: str
    when_expr: str
    message: str
    confidence: float
    evidence_ref: str
    verification_plan: str


class UiRiskLayer:
    """UI-only risk evaluator backed by experiment reports."""

    def __init__(
        self,
        *,
        range_path: Optional[Path] = None,
        candidates_path: Optional[Path] = None,
        min_confidence: float = 0.7,
    ) -> None:
        base = _repo_root()
        self.range_path = Path(range_path) if range_path else (base / "reports" / "ath_experiments" / "range_suggestions.v1.2.json")
        self.candidates_path = (
            Path(candidates_path) if candidates_path else (base / "reports" / "ath_experiments" / "compat_rule_candidates.v1.json")
        )
        self.min_confidence = float(min_confidence)
        self._ranges: Dict[str, _RangeHint] = {}
        self._candidates: List[_CandidateRule] = []
        self.enabled = False
        self.reload()

    def reload(self) -> None:
        self._ranges = self._load_ranges(self.range_path)
        self._candidates = self._load_candidates(self.candidates_path)
        self.enabled = bool(self._ranges or self._candidates)

    def _load_ranges(self, path: Path) -> Dict[str, _RangeHint]:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        per_key = payload.get("per_key")
        if not isinstance(per_key, dict):
            return {}
        result: Dict[str, _RangeHint] = {}
        for key, raw in per_key.items():
            if not isinstance(raw, dict):
                continue
            result[str(key)] = _RangeHint(
                safe_min=_to_float(raw.get("safe_min")),
                safe_max=_to_float(raw.get("safe_max")),
                rec_p05=_to_float(raw.get("rec_p05")),
                rec_p95=_to_float(raw.get("rec_p95")),
                notes=str(raw.get("notes", "")),
            )
        return result

    def _load_candidates(self, path: Path) -> List[_CandidateRule]:
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        raw_candidates = payload.get("candidates")
        if not isinstance(raw_candidates, list):
            return []

        result: List[_CandidateRule] = []
        for raw in raw_candidates:
            if not isinstance(raw, dict):
                continue
            kind = str(raw.get("kind", "")).strip().lower()
            if kind not in {"warn", "fatal"}:
                continue
            confidence = _confidence_score(raw.get("confidence"))
            evidence = raw.get("evidence", {})
            robust_multi_group = False
            refs = {}
            if isinstance(evidence, dict):
                refs = evidence.get("refs", {})
                if isinstance(refs, dict):
                    robust_multi_group = bool(refs.get("consistent_multi_group"))
            if confidence < self.min_confidence and not robust_multi_group:
                continue

            when_raw = str(raw.get("when", "")).strip()
            when_expr = _rewrite_function_dsl(when_raw)
            if not when_expr:
                continue
            result.append(
                _CandidateRule(
                    rule_id=str(raw.get("id", "ui_risk_candidate")),
                    kind=kind,
                    when_raw=when_raw,
                    when_expr=when_expr,
                    message=_message_from_action(raw.get("then")),
                    confidence=confidence,
                    evidence_ref=_to_evidence_ref(refs),
                    verification_plan=str(raw.get("verification_plan", "")).strip(),
                )
            )
        return result

    def _range_issues(
        self,
        *,
        values: Mapping[str, Any],
        set_keys: Set[str],
        visible_keys: Set[str],
    ) -> List[Dict[str, Any]]:
        issues: List[Dict[str, Any]] = []
        for key in sorted(set_keys):
            if key not in visible_keys:
                continue
            hint = self._ranges.get(key)
            if hint is None:
                continue
            raw_value = values.get(key)
            if not _is_constant_expr(raw_value):
                continue
            number = _to_float(raw_value)
            if number is None:
                continue

            outside_safe = False
            if hint.safe_min is not None and number < hint.safe_min:
                outside_safe = True
            if hint.safe_max is not None and number > hint.safe_max:
                outside_safe = True

            outside_rec = False
            if hint.rec_p05 is not None and number < hint.rec_p05:
                outside_rec = True
            if hint.rec_p95 is not None and number > hint.rec_p95:
                outside_rec = True

            if outside_safe:
                issues.append(
                    {
                        "rule_id": "ui_risk_range_safe_bound",
                        "severity": "fatal",
                        "source": "ui_risk_layer",
                        "scope": "project_ui_hint",
                        "field_key": key,
                        "message": (
                            f"{key}={_fmt_num(number)} is outside safe range "
                            f"[{_fmt_num(hint.safe_min)}, {_fmt_num(hint.safe_max)}]."
                        ),
                        "confidence": 0.9,
                        "evidence_type": "experiment",
                        "evidence_ref": hint.notes,
                        "suggestion": (
                            f"Use recommended range [{_fmt_num(hint.rec_p05)}, {_fmt_num(hint.rec_p95)}]."
                        ),
                    }
                )
                continue

            if outside_rec:
                issues.append(
                    {
                        "rule_id": "ui_risk_range_recommended",
                        "severity": "warn",
                        "source": "ui_risk_layer",
                        "scope": "project_ui_hint",
                        "field_key": key,
                        "message": (
                            f"{key}={_fmt_num(number)} is outside recommended range "
                            f"[{_fmt_num(hint.rec_p05)}, {_fmt_num(hint.rec_p95)}]."
                        ),
                        "confidence": 0.75,
                        "evidence_type": "experiment",
                        "evidence_ref": hint.notes,
                        "suggestion": (
                            f"Stay inside safe bounds [{_fmt_num(hint.safe_min)}, {_fmt_num(hint.safe_max)}]."
                        ),
                    }
                )
                continue

            # Value is within recommended range; mark as UI-ok candidate.
            issues.append(
                {
                    "rule_id": "ui_risk_range_recommended_ok",
                    "severity": "ok",
                    "source": "ui_risk_layer",
                    "scope": "project_ui_hint",
                    "field_key": key,
                    "message": (
                        f"{key}={_fmt_num(number)} is within recommended range "
                        f"[{_fmt_num(hint.rec_p05)}, {_fmt_num(hint.rec_p95)}]."
                    ),
                    "confidence": 0.8,
                    "evidence_type": "experiment",
                    "evidence_ref": hint.notes,
                    "suggestion": "",
                }
            )
        return issues

    def _candidate_affected_keys(
        self,
        *,
        candidate: _CandidateRule,
        values: Mapping[str, Any],
        set_keys: Set[str],
        visible_keys: Set[str],
    ) -> List[str]:
        quoted = [match.group(1).strip() for match in re.finditer(r"'([^']+)'", candidate.when_raw)]
        identifiers = [token for token in _IDENT_RE.findall(candidate.when_raw) if token not in _FUNC_NAMES]
        keys: Set[str] = set()

        for token in quoted:
            if token in set_keys:
                keys.add(token)
        for token in identifiers:
            if token in set_keys:
                keys.add(token)

        if "observed.max_dimension_mm" in identifiers:
            for key in _DIM_PROXY_KEYS:
                if key not in set_keys:
                    continue
                number = _to_float(values.get(key))
                if number is None:
                    continue
                if number > 2000.0:
                    keys.add(key)

        return sorted(key for key in keys if key in visible_keys)

    def _candidate_issues(
        self,
        *,
        values: Mapping[str, Any],
        set_keys: Set[str],
        visible_keys: Set[str],
    ) -> List[Dict[str, Any]]:
        issues: List[Dict[str, Any]] = []
        for candidate in self._candidates:
            matched = _safe_eval_when(candidate.when_expr, dict(values))
            if not matched:
                continue
            keys = self._candidate_affected_keys(
                candidate=candidate,
                values=values,
                set_keys=set_keys,
                visible_keys=visible_keys,
            )
            for key in keys:
                issues.append(
                    {
                        "rule_id": candidate.rule_id,
                        "severity": candidate.kind,
                        "source": "ui_risk_layer",
                        "scope": "project_ui_hint",
                        "field_key": key,
                        "message": candidate.message,
                        "confidence": candidate.confidence,
                        "evidence_type": "experiment",
                        "evidence_ref": candidate.evidence_ref,
                        "suggestion": candidate.verification_plan,
                    }
                )
        return issues

    def evaluate(
        self,
        draft_payload: Mapping[str, Any],
        *,
        visible_keys: Optional[Iterable[str]] = None,
    ) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
        values, set_keys = _extract_draft_values(draft_payload)
        if not set_keys:
            return []

        vis = set(str(item) for item in (visible_keys or set_keys))
        if not vis:
            vis = set(set_keys)

        dim_candidates = [(_to_float(values.get(key)), key) for key in _DIM_PROXY_KEYS if key in set_keys]
        dim_numbers = [(value, key) for value, key in dim_candidates if value is not None]
        if dim_numbers:
            max_dim = max(value for value, _ in dim_numbers)
            values["observed.max_dimension_mm"] = max_dim

        issues = []
        issues.extend(self._range_issues(values=values, set_keys=set_keys, visible_keys=vis))
        issues.extend(self._candidate_issues(values=values, set_keys=set_keys, visible_keys=vis))

        dedup: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
        for issue in issues:
            key = str(issue.get("field_key", ""))
            dedup_key = (
                key,
                str(issue.get("rule_id", "")),
                str(issue.get("severity", "")),
                str(issue.get("message", "")),
            )
            if dedup_key not in dedup:
                dedup[dedup_key] = issue
        return list(dedup.values())
