# UI Risk Layer (Project Page)

## Purpose
The UI Risk Layer adds experiment-backed hints on top of the normative compatibility system.

- Normative rules: `app/knowledge/ath/ruleset.v1.json` via `CompatibilityService` (can be fatal and block flows where defined).
- UI Risk Layer (legacy quarantine): `tools/legacy/ui_risk_layer.py` (visual-only warn/fatal outlines + tooltip guidance, no new hard blocking).

## Data Sources
- `reports/ath_experiments/range_suggestions.v1.2.json`
- `reports/ath_experiments/compat_rule_candidates.v1.json`

If files are missing or invalid, the layer auto-disables and the UI remains stable.

## Evaluation Model
1. Range hints (`per_key`):
- Inside recommended `rec_p05..rec_p95`: no highlight
- Outside recommended but inside safe `safe_min..safe_max`: `warn`
- Outside safe: `fatal`

2. Candidate combo rules:
- Uses safe DSL translation (`gt/eq/and/isDefined` -> compat expression) and `compat_engine` AST evaluation.
- No Python `eval()` is used.
- Applies only to set and visible fields.

3. Anti-spurious guardrail:
- Only rule candidates with sufficient confidence (`>= 0.7`) or explicit multi-group robustness are applied.
- These are UI hints, not normative blockers.

## UI Behavior
- `riskLevel="warn"`: subtle amber outline.
- `riskLevel="fatal"`: subtle muted-red outline.
- Only strongest level per field is shown.
- Tooltip lists top reasons and short hint.
- Empty/unset or hidden fields remain neutral.
