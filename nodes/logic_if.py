"""
IF node: routes items into two outputs based on a condition.
  output 0 (true branch)  — items that PASS the condition
  output 1 (false branch) — items that FAIL

Supports {{ $json.field }} expressions in the value param.

Operators:
  equals / not equals
  greater than / less than / greater or equal / less or equal
  contains / not contains (string)
  exists / not exists (field presence)
  is empty / is not empty
  regex match
"""
import re
from node_base import Node, resolve_expr


class IfNode(Node):
    TYPE = "logic.if"
    TITLE = "IF"
    CATEGORY = "logic"
    INPUTS = 1
    OUTPUTS = 2   # [true_branch, false_branch]
    PARAMS = [
        {"key": "field", "label": "Field (or {{ $json.x }})", "type": "text", "default": ""},
        {
            "key": "operator",
            "label": "Operator",
            "type": "select",
            "default": "equals",
            "options": [
                "equals",
                "not equals",
                "greater than",
                "less than",
                "greater or equal",
                "less or equal",
                "contains",
                "not contains",
                "exists",
                "not exists",
                "is empty",
                "is not empty",
                "regex match",
            ],
        },
        {"key": "value", "label": "Value (or {{ $json.x }})", "type": "text", "default": ""},
        {
            "key": "type",
            "label": "Compare as",
            "type": "select",
            "default": "auto",
            "options": ["auto", "string", "number", "boolean"],
        },
        {
            "key": "on_missing",
            "label": "If the field is missing",
            "type": "select",
            "default": "false",
            "options": ["false", "true", "error"],
            "desc": "What to do when the field being checked isn't on the "
                    "item at all. false = send it down the false branch "
                    "(safest, and the default). true = send it down the true "
                    "branch. error = stop and report it, for when a missing "
                    "field means something upstream is genuinely broken and "
                    "you'd rather know than have it quietly pick a branch.",
        },
    ]

    def run(self, items):
        field_expr = self.params.get("field", "")
        op = self.params.get("operator", "equals")
        value_expr = self.params.get("value", "")
        compare_as = self.params.get("type", "auto")

        true_items, false_items = [], []
        for item in items:
            j = item.get("json", {})

            # resolve field: if it's an expression get the value, else do a dict lookup
            if "{{" in str(field_expr):
                # self.rexpr (not the bare resolve_expr) so cross-node
                # references like {{ $('Other Node').item.json.x }} actually
                # resolve -- the bare call gets no context and silently
                # returns None for every $('...') lookup, which then made
                # "not equals" pass for a field that was never really read.
                actual = self.rexpr(field_expr, j)
                # an expression that resolves to nothing means the path did
                # not exist on the item
                missing = actual is None
            else:
                field_name = str(field_expr).strip()
                actual = j.get(field_name)
                missing = field_name not in j

            # resolve comparison value
            cmp_val = self.rexpr(value_expr, j) if isinstance(value_expr, str) else value_expr

            # type coercion
            actual, cmp_val = self._coerce(actual, cmp_val, compare_as)

            passed = self._test(actual, op, cmp_val, j, field_expr, missing)
            (true_items if passed else false_items).append(item)

        return [true_items, false_items]

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
        else:  # auto: try number coercion if both look numeric
            try:
                a2 = float(actual); c2 = float(cmp_val)
                actual, cmp_val = a2, c2
            except (TypeError, ValueError):
                pass
        return actual, cmp_val

    # Operators whose whole job is to report that something ISN'T there.
    # Forcing these to False on a missing field would make them useless, so
    # they are the only ones exempt from the rule below.
    _ABSENCE_OPS = {"not exists", "is empty"}

    def _test(self, actual, op, cmp_val, j, field_expr, missing=False):
        field_name = str(field_expr).strip()

        # A field that isn't there doesn't slide through as None any more.
        # What SHOULD happen is your call, because it genuinely differs per
        # workflow: usually false is right, sometimes true, and sometimes a
        # missing field means something upstream broke and you want to hear
        # about it rather than have a branch silently chosen for you.
        if missing and op not in self._ABSENCE_OPS:
            mode = str(self.params.get("on_missing", "false")).lower()
            if mode == "error":
                shown = field_name or "(blank)"
                raise ValueError(
                    f"IF node: the field {shown} is missing on this item, "
                    f"and this node is set to error when that happens.")
            return mode == "true"

        if op == "equals":           return actual == cmp_val
        if op == "not equals":       return actual != cmp_val
        if op == "greater than":     return actual is not None and actual > cmp_val
        if op == "less than":        return actual is not None and actual < cmp_val
        if op == "greater or equal": return actual is not None and actual >= cmp_val
        if op == "less or equal":    return actual is not None and actual <= cmp_val
        if op == "contains":         return cmp_val in str(actual or "")
        if op == "not contains":     return cmp_val not in str(actual or "")
        if op == "exists":           return field_name in j
        if op == "not exists":       return field_name not in j
        if op == "is empty":         return actual in (None, "", [], {})
        if op == "is not empty":     return actual not in (None, "", [], {})
        if op == "regex match":
            try: return bool(re.search(str(cmp_val), str(actual or "")))
            except re.error: return False
        return False
