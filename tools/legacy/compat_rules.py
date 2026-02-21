"""LEGACY QUARANTINED: non-shipping compatibility rule export helpers."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.ath_knowledge import AthKnowledgeBundle, load_ath_knowledge
from app.compat_schema import normalize_ruleset


@dataclass(frozen=True)
class CompatibilityRuleRecord:
    rule_id: str
    description: str
    scope: str
    condition: str
    action: List[str]
    severity: str
    evidence: Dict[str, Any]
    kind: str
    applies_to: List[str]
    verification_plan: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "rule_id": self.rule_id,
            "description": self.description,
            "scope": self.scope,
            "condition": self.condition,
            "action": list(self.action),
            "severity": self.severity,
            "kind": self.kind,
            "applies_to": list(self.applies_to),
            "evidence": dict(self.evidence),
        }
        if self.verification_plan:
            payload["verification_plan"] = self.verification_plan
        return payload


def _scope_from_applies_to(applies_to: List[str]) -> str:
    if "project" in applies_to and "batch" in applies_to:
        return "project"
    if "batch" in applies_to:
        return "batch"
    return "version"


def load_compatibility_rules(bundle: AthKnowledgeBundle | None = None) -> List[CompatibilityRuleRecord]:
    bundle = bundle or load_ath_knowledge()
    ruleset = normalize_ruleset(bundle.ruleset, bundle.catalog)
    rules_raw = ruleset.get("rules", [])
    records: List[CompatibilityRuleRecord] = []

    for rule in rules_raw:
        if not isinstance(rule, dict):
            continue
        rule_id = str(rule.get("id", "")).strip()
        if not rule_id:
            continue
        applies_to = [str(item) for item in list(rule.get("applies_to", []) or [])]
        if not applies_to:
            applies_to = ["version"]
        evidence = rule.get("evidence")
        if not isinstance(evidence, dict):
            evidence = {
                "type": "hypothesis",
                "refs": [],
                "confidence": 0.5,
                "notes": "No evidence payload found in ruleset.",
            }
        records.append(
            CompatibilityRuleRecord(
                rule_id=rule_id,
                description=str(rule.get("rationale", "")).strip(),
                scope=_scope_from_applies_to(applies_to),
                condition=str(rule.get("when", "true")),
                action=[str(item) for item in list(rule.get("then", []) or [])],
                severity=str(rule.get("severity", "warn")),
                kind=str(rule.get("kind", "validity")),
                applies_to=applies_to,
                evidence=evidence,
                verification_plan=(
                    str(rule.get("verification_plan"))
                    if isinstance(rule.get("verification_plan"), str)
                    else None
                ),
            )
        )

    restrictions = ruleset.get("runner_restrictions", {})
    if isinstance(restrictions, dict):
        runner_mode = str(restrictions.get("runner_mode", "")).strip()
        locked = restrictions.get("locked_or_hidden_keys", [])
        if runner_mode and isinstance(locked, list) and locked:
            evidence = restrictions.get("evidence")
            if not isinstance(evidence, dict):
                evidence = {
                    "type": "hypothesis",
                    "refs": [],
                    "confidence": 0.5,
                    "notes": "Runner restriction evidence missing.",
                }
            records.append(
                CompatibilityRuleRecord(
                    rule_id="runner_fixed_source_block",
                    description=str(restrictions.get("rationale", "")).strip(),
                    scope="project",
                    condition=f"runner_mode == '{runner_mode}'",
                    action=[f"lock({str(key)})" for key in locked],
                    severity="fatal",
                    kind=str(restrictions.get("kind", "runner")),
                    applies_to=[str(item) for item in list(restrictions.get("applies_to", ["project"]))],
                    evidence=evidence,
                    verification_plan=(
                        str(restrictions.get("verification_plan"))
                        if isinstance(restrictions.get("verification_plan"), str)
                        else None
                    ),
                )
            )
    return records


def dump_compatibility_rules(path: str | Path, bundle: AthKnowledgeBundle | None = None) -> None:
    bundle = bundle or load_ath_knowledge()
    ruleset = normalize_ruleset(bundle.ruleset, bundle.catalog)
    records = load_compatibility_rules(bundle=bundle)
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.1",
        "ruleset_version": str(ruleset.get("ruleset_version", "")),
        "rule_count": len(records),
        "rules": [record.to_dict() for record in records],
        "semantic_facts": list(ruleset.get("semantic_facts", [])),
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

