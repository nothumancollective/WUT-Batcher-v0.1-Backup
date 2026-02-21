# Rules

## Ruleset Versioning
- Source file stays compatible with `ath-geometry-constraints.v1`.
- Runtime normalization migrates in-memory to `ath-geometry-constraints.v1.1`.
- Migration is non-destructive and deterministic.

## Rule Fields (v1.1)
Each rule supports:
- `id`
- `scope` (`validity|visibility|sweepability`)
- `kind` (`validity|visibility|sweepability|runner|semantics`)
- `applies_to` (`project|batch|version` list)
- `when` (restricted DSL expression)
- `then` (actions)
- `severity` (`fatal|warn|info`)
- `rationale`
- `evidence` object:
  - `type` (`ath_doc|hypothesis`)
  - `refs` list
  - `confidence` (0.0-1.0)
  - `notes`
- `verification_plan` (required when `evidence.type == "hypothesis"`)

## Actions
Supported actions:
- `show(key)`
- `hide(key)`
- `lock(key)`
- `require(key)`
- `warn("message")`
- `note_ignored(key, because)`

Semantics notes:
- `note_ignored(...)` creates informational semantics issues.
- Example: if `Source.Contours` is set, rules emit
  - `note_ignored(Source.Shape, Source.Contours)`
  - `note_ignored(Source.Radius, Source.Contours)`
  - `note_ignored(Source.Curv, Source.Contours)`

## DSL Safety
Conditions are evaluated by a restricted AST evaluator.
- Allowed helper functions: `isDefined`, `get`, `isEmptyList`, `len`, `isExprConstant`.
- Arbitrary Python execution is not allowed.
- Explicit `unset` values (SQL `is_set=0`) are treated as not defined.

## Evidence Policy
- Prefer `ath_doc` evidence with exact references (`doc`, `section`, `page`, `quote_hint`).
- Use `hypothesis` only when no explicit reference is available.
- Hypothesis entries must include a concrete `verification_plan`.
- Hypothesis semantics can be regression-checked with `python -m app compat verify`.

## UI Risk Layer
- Experiment-backed UI hints are intentionally separated from normative rules.
- Source artifacts:
  - `reports/ath_experiments/range_suggestions.v1.2.json`
  - `reports/ath_experiments/compat_rule_candidates.v1.json`
- Runtime evaluator (legacy quarantine): `tools/legacy/ui_risk_layer.py`.
- These hints provide `warn|fatal` visuals on Project form fields and tooltip guidance, but do not add new hard flow blocks.
