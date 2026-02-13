"""Constraint engine for ATH geometry visibility/validity/sweepability."""



from __future__ import annotations



import ast

import re

from dataclasses import dataclass

from typing import Any, Dict, Iterable, List, Sequence, Set



from app.ath_knowledge import AthKnowledgeBundle, load_ath_knowledge

from app.constants import DEFAULT_RUNNER_MODE





ALLOWED_AST_NODES = (

    ast.Expression,

    ast.BoolOp,

    ast.BinOp,

    ast.UnaryOp,

    ast.Compare,

    ast.Call,

    ast.Name,

    ast.Load,

    ast.Constant,

    ast.And,

    ast.Or,

    ast.Not,

    ast.Mod,

    ast.Add,

    ast.Sub,

    ast.Mult,

    ast.Div,

    ast.Eq,

    ast.NotEq,

    ast.Gt,

    ast.GtE,

    ast.Lt,

    ast.LtE,

)



ALLOWED_FUNCS = {"isDefined", "get", "isEmptyList", "len", "isExprConstant", "get_value"}

RESERVED_WORDS = {"and", "or", "not", "True", "False", "None"}

FUNC_NAMES = {"isDefined", "get", "isEmptyList", "len", "isExprConstant"}



SHOW_HIDE_LOCK_REQUIRE_RE = re.compile(r"^(show|hide|lock|require)\(([^)]+)\)$")

WARN_RE = re.compile(r'^warn\("(.+)"\)$')





@dataclass(frozen=True)

class Issue:

    rule_id: str

    severity: str

    category: str  # ath | runner

    message: str

    hint: str



    def to_dict(self) -> Dict[str, str]:

        return {

            "rule_id": self.rule_id,

            "severity": self.severity,

            "category": self.category,

            "message": self.message,

            "hint": self.hint,

        }





def _catalog_order(bundle: AthKnowledgeBundle) -> List[str]:

    params = bundle.catalog.get("parameters", [])

    keys: List[str] = []

    for item in params:

        if isinstance(item, dict):

            key = item.get("key")

            if isinstance(key, str):

                keys.append(key)

    return keys





def _extract_values(constraints: Dict[str, Any]) -> Dict[str, Any]:

    if not isinstance(constraints, dict):

        return {}

    fixed = constraints.get("fixed_params")

    if isinstance(fixed, dict):

        return dict(fixed)

    return dict(constraints)





def _normalize_logic(expr: str) -> str:

    normalized = expr.replace("&&", " and ").replace("||", " or ")

    normalized = re.sub(r"(?<![=!<>])!(?!=)", " not ", normalized)

    normalized = re.sub(r"\btrue\b", "True", normalized, flags=re.IGNORECASE)

    normalized = re.sub(r"\bfalse\b", "False", normalized, flags=re.IGNORECASE)

    return normalized.strip()





def _translate_identifiers(expr: str) -> str:

    src = _normalize_logic(expr)

    out: List[str] = []

    i = 0

    n = len(src)



    while i < n:

        ch = src[i]

        if ch in {"'", '"'}:

            quote = ch

            out.append(ch)

            i += 1

            while i < n:

                out.append(src[i])

                if src[i] == "\\" and i + 1 < n:

                    i += 1

                    out.append(src[i])

                    i += 1

                    continue

                if src[i] == quote:

                    i += 1

                    break

                i += 1

            continue



        if ch.isalpha() or ch == "_":

            j = i + 1

            while j < n and (src[j].isalnum() or src[j] in {"_", ".", "-"}):

                j += 1

            token = src[i:j]

            k = j

            while k < n and src[k].isspace():

                k += 1

            is_func = token in FUNC_NAMES and k < n and src[k] == "("

            if token in RESERVED_WORDS or is_func:

                out.append(token)

            else:

                out.append(f'get_value("{token}")')

            i = j

            continue



        out.append(ch)

        i += 1



    return "".join(out)





def _safe_eval_when(expr: str, values: Dict[str, Any]) -> bool:

    translated = _translate_identifiers(expr)

    try:

        tree = ast.parse(translated, mode="eval")

    except SyntaxError:

        return False



    for node in ast.walk(tree):

        if not isinstance(node, ALLOWED_AST_NODES):

            return False

        if isinstance(node, ast.Call):

            if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_FUNCS:

                return False



    def get_value(key: str) -> Any:

        return values.get(key)



    def is_defined(value: Any) -> bool:

        return value is not None



    def get_field(value: Any, field_name: str) -> Any:

        if isinstance(value, dict):

            return value.get(field_name)

        return None



    def is_empty_list(value: Any) -> bool:

        return isinstance(value, list) and len(value) == 0



    def list_len(value: Any) -> int:

        try:

            return len(value)  # type: ignore[arg-type]

        except Exception:

            return 0



    def is_expr_constant(value: Any) -> bool:

        if value is None:

            return True

        if isinstance(value, (int, float, bool)):

            return True

        if not isinstance(value, str):

            return True

        return re.search(r"\bp\b", value) is None



    local_ctx = {

        "get_value": get_value,

        "isDefined": is_defined,

        "get": get_field,

        "isEmptyList": is_empty_list,

        "len": list_len,

        "isExprConstant": is_expr_constant,

        "True": True,

        "False": False,

        "None": None,

    }



    try:

        return bool(eval(compile(tree, "<compat_rule>", "eval"), {"__builtins__": {}}, local_ctx))

    except Exception:

        return False





def _parse_action(action: str) -> tuple[str, str] | None:

    match = SHOW_HIDE_LOCK_REQUIRE_RE.match(action.strip())

    if match:

        verb = match.group(1)

        value = match.group(2).strip()

        return verb, value

    warning = WARN_RE.match(action.strip())

    if warning:

        return "warn", warning.group(1)

    return None





def _apply_visibility_rules(

    keys: Sequence[str],

    rules: Sequence[Dict[str, Any]],

    values: Dict[str, Any],

) -> Set[str]:

    visible: Set[str] = set(keys)

    for rule in rules:

        if rule.get("scope") != "visibility":

            continue

        when = str(rule.get("when", "true"))

        if not _safe_eval_when(when, values):

            continue

        for action in rule.get("then", []):

            parsed = _parse_action(str(action))

            if parsed is None:

                continue

            verb, key = parsed

            if verb == "show":

                visible.add(key)

            elif verb == "hide":

                visible.discard(key)

    return visible





def _apply_sweepability_rules(

    rules: Sequence[Dict[str, Any]],

    values: Dict[str, Any],

    visible: Set[str],

) -> Set[str]:

    sweepable: Set[str] = set()

    locked: Set[str] = set()

    for rule in rules:

        if rule.get("scope") != "sweepability":

            continue

        when = str(rule.get("when", "true"))

        if not _safe_eval_when(when, values):

            continue

        for action in rule.get("then", []):

            parsed = _parse_action(str(action))

            if parsed is None:

                continue

            verb, key = parsed

            if verb == "show":

                if key in visible:

                    sweepable.add(key)

            elif verb == "hide":

                sweepable.discard(key)

            elif verb == "lock":

                locked.add(key)

                sweepable.discard(key)



    return {key for key in sweepable if key not in locked and key in visible}





def _build_validity_issues(

    rules: Sequence[Dict[str, Any]],

    values: Dict[str, Any],

) -> List[Issue]:

    issues: List[Issue] = []

    for rule in rules:

        if rule.get("scope") != "validity":

            continue

        when = str(rule.get("when", "true"))

        if not _safe_eval_when(when, values):

            continue



        severity = str(rule.get("severity", "warn"))

        rule_id = str(rule.get("id", "unknown_rule"))

        actions = rule.get("then", [])

        if not isinstance(actions, list):

            actions = []



        action_messages: List[str] = []

        for action in actions:

            parsed = _parse_action(str(action))

            if parsed is None:

                continue

            verb, payload = parsed

            if verb == "warn":

                action_messages.append(payload)

            elif verb == "require":

                action_messages.append(f"{payload} ist erforderlich.")



        if not action_messages:

            action_messages.append("Regel verletzt.")



        for message in action_messages:

            issues.append(

                Issue(

                    rule_id=rule_id,

                    severity=severity,

                    category="ath",

                    message=message,

                    hint="Parameterwerte anpassen und erneut pruefen.",

                )

            )

    return issues





def _apply_runner_restrictions(

    visible: Set[str],

    sweepable: Set[str],

    values: Dict[str, Any],

    ruleset: Dict[str, Any],

    runner_mode: str,

) -> List[Issue]:

    issues: List[Issue] = []

    restrictions = ruleset.get("runner_restrictions", {})

    if not isinstance(restrictions, dict):

        return issues

    if restrictions.get("runner_mode") != runner_mode:

        return issues



    locked_or_hidden = restrictions.get("locked_or_hidden_keys", [])

    if not isinstance(locked_or_hidden, list):

        locked_or_hidden = []

    locked_keys = {str(item) for item in locked_or_hidden}



    visible.difference_update(locked_keys)

    sweepable.difference_update(locked_keys)



    for key in sorted(locked_keys):

        if key in values:

            issues.append(

                Issue(

                    rule_id="runner_fixed_source_block",

                    severity="fatal",

                    category="runner",

                    message=f"{key} ist im RunnerMode '{runner_mode}' gesperrt.",

                    hint=(

                        "Entfernen Sie den Wert aus den Constraints. "

                        "Der feste Source-Block wird automatisch gesetzt."

                    ),

                )

            )

    return issues





def _sorted_keys(keys: Iterable[str], order: Sequence[str]) -> List[str]:

    index = {key: pos for pos, key in enumerate(order)}

    return sorted(keys, key=lambda key: (index.get(key, 10_000_000), key))





def visible_params(

    constraints: Dict[str, Any],

    runner_mode: str = DEFAULT_RUNNER_MODE,

    bundle: AthKnowledgeBundle | None = None,

) -> List[str]:

    bundle = bundle or load_ath_knowledge()

    keys = _catalog_order(bundle)

    values = _extract_values(constraints)

    rules = bundle.ruleset.get("rules", [])

    visible = _apply_visibility_rules(keys, rules, values)

    sweepable = set()  # not used here, but required for restriction hook

    _apply_runner_restrictions(visible, sweepable, values, bundle.ruleset, runner_mode)

    return _sorted_keys(visible, keys)





def sweepable_params(

    constraints: Dict[str, Any],

    runner_mode: str = DEFAULT_RUNNER_MODE,

    bundle: AthKnowledgeBundle | None = None,

) -> List[str]:

    bundle = bundle or load_ath_knowledge()

    keys = _catalog_order(bundle)

    values = _extract_values(constraints)

    rules = bundle.ruleset.get("rules", [])

    visible = _apply_visibility_rules(keys, rules, values)

    sweepable = _apply_sweepability_rules(rules, values, visible)

    _apply_runner_restrictions(visible, sweepable, values, bundle.ruleset, runner_mode)

    return _sorted_keys(sweepable, keys)





def validity_report(

    constraints: Dict[str, Any],

    runner_mode: str = DEFAULT_RUNNER_MODE,

    bundle: AthKnowledgeBundle | None = None,

) -> Dict[str, Any]:

    bundle = bundle or load_ath_knowledge()

    keys = _catalog_order(bundle)

    values = _extract_values(constraints)

    rules = bundle.ruleset.get("rules", [])



    visible = _apply_visibility_rules(keys, rules, values)

    sweepable = _apply_sweepability_rules(rules, values, visible)

    issues = _build_validity_issues(rules, values)

    issues.extend(_apply_runner_restrictions(visible, sweepable, values, bundle.ruleset, runner_mode))



    issues_sorted = sorted(

        issues,

        key=lambda item: (item.severity, item.category, item.rule_id, item.message),

    )



    return {

        "catalog_version": bundle.catalog_version,

        "ruleset_version": bundle.ruleset_version,

        "runner_mode": runner_mode,

        "issues": [issue.to_dict() for issue in issues_sorted],

        "fatal": [issue.to_dict() for issue in issues_sorted if issue.severity == "fatal"],

        "warn": [issue.to_dict() for issue in issues_sorted if issue.severity == "warn"],

        "info": [issue.to_dict() for issue in issues_sorted if issue.severity == "info"],

    }

