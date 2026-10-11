"""
Filter node — keeps only the items that match the condition; drops the rest.

Unlike IF (which routes matching/non-matching to two different outputs), Filter
has a single output: items that pass the condition continue, items that fail are
discarded. This is the common "only keep the rows where status = active" case.

Conditions use the same field/operator/value style as the IF and Switch nodes,
so behaviour is consistent across the app.

Multiple conditions can be combined with `combine`:
    "all"  -> an item must match EVERY condition to pass (AND)
    "any"  -> an item passes if it matches ANY condition (OR)
"""
import re
from node_base import Node


class FilterNode(Node):
    TYPE = "logic.filter"
    TITLE = "Filter"
    CATEGORY = "logic"
    INPUTS = 1
    OUTPUTS = 1
    PARAMS = [
        {
            "key": "conditions",
            "label": "Conditions",
            "type": "filter_rules",
            "default": [
                {"field": "", "operator": "equals", "value": ""},
            ],
        },
        {
            "key": "combine",
            "label": "Combine conditions with",
            "type": "select",
            "default": "all",
            "options": ["all", "any"],
        },
        {
            "key": "invert",
            "label": "Invert (keep the NON-matching items)",
            "type": "bool",
            "default": False,
        },
    ]

    # --- shared operator logic (same as IF / Switch) ---------------------
    def _coerce(self, actual, cmp_val, compare_as):
        if compare_as == "number":
            try: actual = float(actual)
            except (TypeError, ValueError): pass
            try: cmp_val = float(cmp_val)
            except (TypeError, ValueError): pass
        elif compare_as == "string":
            actual = str(actual) if actual is not None else ""
            cmp_val = str(cmp_val) if cmp_val is not None else ""
        elif compare_as == "boolean":
            actual = bool(actual)
            cmp_val = str(cmp_val).lower() in ("true", "1", "yes") if isinstance(cmp_val, str) else bool(cmp_val)
        else:  # auto
            try:
                a2 = float(actual); c2 = float(cmp_val)
                actual, cmp_val = a2, c2
            except (TypeError, ValueError):
                pass
        return actual, cmp_val

    def _test(self, actual, op, cmp_val, j, field_name, missing=False):
        if op == "equals":           return actual == cmp_val
        if op == "not equals":       return actual != cmp_val
        if op == "greater than":     return actual is not None and actual > cmp_val
        if op == "less than":        return actual is not None and actual < cmp_val
        if op == "greater or equal": return actual is not None and actual >= cmp_val
        if op == "less or equal":    return actual is not None and actual <= cmp_val
        if op == "contains":         return cmp_val in str(actual or "")
        if op == "not contains":     return cmp_val not in str(actual or "")
        # 'exists' used to ask `field_name in j` -- but field_name is the
        # raw text you typed, so for an expression it was asking whether the
        # literal string "{{ $('X').item.json.y }}" was a key on the item.
        # It never is, so exists was ALWAYS false and not-exists ALWAYS true
        # for any expression. `missing` is already worked out properly above
        # for both the expression and the plain-field-name cases.
        if op == "exists":           return not missing
        if op == "not exists":       return missing
        if op == "is empty":         return actual in (None, "", [], {})
        if op == "is not empty":     return actual not in (None, "", [], {})
        if op == "regex match":
            try: return bool(re.search(str(cmp_val), str(actual or "")))
            except re.error: return False
        return False

    def _eval_condition(self, cond, j):
        field_expr = cond.get("field", "")
        op = cond.get("operator", "equals")
        value_expr = cond.get("value", "")
        compare_as = cond.get("type", "auto")

        if "{{" in str(field_expr):
            actual = self.rexpr(field_expr, j)
            field_name = str(field_expr).strip()
            # an expression resolving to nothing means the path isn't there
            missing = actual is None
        else:
            field_name = str(field_expr).strip()
            actual = j.get(field_name)
            missing = field_name not in j

        cmp_val = self.rexpr(value_expr, j) if isinstance(value_expr, str) else value_expr
        actual, cmp_val = self._coerce(actual, cmp_val, compare_as)
        return self._test(actual, op, cmp_val, j, field_name, missing)

    # --- main -----------------------------------------------------------
    def run(self, items):
        conds = self.params.get("conditions") or []
        if isinstance(conds, dict):
            conds = [conds]
        combine = self.params.get("combine", "all")
        invert = bool(self.params.get("invert", False))

        # no usable conditions -> pass everything through untouched
        usable = [c for c in conds if str(c.get("field", "")).strip()]
        if not usable:
            return list(items)

        kept = []
        for item in items:
            j = item.get("json", {})
            results = [self._eval_condition(c, j) for c in usable]
            passed = all(results) if combine == "all" else any(results)
            if invert:
                passed = not passed
            if passed:
                kept.append(item)
        return kept
