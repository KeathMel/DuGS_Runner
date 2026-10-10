"""
Calculator — does the maths itself, so an AI never has to.

Language models are unreliable at arithmetic: they'll confidently answer
383 * 3483 and be wrong. The fix is to let the model write the SUM and let
real code work out the answer. Point this node's Expression at whatever
field holds the maths and it computes it exactly.

    {{ $json.math }}   ->   383 * 3483   ->   1334  (no: 1333989, exactly)

SAFE BY DESIGN
==============
It does NOT use Python's eval. The expression is parsed and walked one node
at a time, and anything that isn't arithmetic is refused -- no imports, no
attribute access, no function calls beyond the small list below. So a model
(or anyone poking your webhook) can't smuggle code in through a sum.

WHAT IT UNDERSTANDS
===================
  + - * /        add, subtract, multiply, divide
  //  %  **      floor divide, remainder, power
  ( )            brackets, and a leading minus
  x              treated as * so "5 x 3" works
  ,              thrown away, so "1,000 + 5" works

  round(n, d)  abs(n)  min(a, b)  max(a, b)  sqrt(n)
  floor(n)  ceil(n)  int(n)  float(n)  sum(a, b, ...)
  pi  e

SETTINGS
========
expression  : the sum to work out; {{ }} allowed
precision   : decimal places to round to, blank or -1 to leave it alone
field       : the field name the answer lands in
output_mode : keep the whole item, or send on just the answer
on_error    : what to do when the expression doesn't make sense

A NOTE ON "just the answer"
===========================
Normally a node passes the whole item through and adds its own field, which
is usually what you want. But a long way down a chain that item can be
carrying a memory summary, a file's contents and everything else picked up
on the way, and all you wanted back was a number. "just the answer" drops
the lot and emits only {field: value}.

Only do that when the nodes after it read their data with
{{ $('Some Node').item.json.x }} rather than {{ $json.x }} -- a cross-node
reference still works, because it reads that node's own output, not the item
flowing past.
"""
import ast
import math
import operator
import re

from node_base import Node


# only these operations are allowed -- anything else is refused
_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}

_FUNCS = {
    "round": round, "abs": abs, "min": min, "max": max,
    "sqrt": math.sqrt, "floor": math.floor, "ceil": math.ceil,
    "int": int, "float": float, "sum": lambda *a: sum(a),
    "log": math.log, "log10": math.log10, "exp": math.exp,
}
_NAMES = {"pi": math.pi, "e": math.e}

# a power big enough to hang the process is not worth supporting
_MAX_POW = 10 ** 6


def _evaluate(node):
    """Walk one parsed node. Anything unexpected raises, which is the point."""
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError(f"only numbers are allowed, got {node.value!r}")
        return node.value

    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ValueError("that operator isn't allowed here")
        left, right = _evaluate(node.left), _evaluate(node.right)
        # ** with a huge exponent can lock the process up for minutes
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_POW:
            raise ValueError("that power is too large to work out")
        return op(left, right)

    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise ValueError("that sign isn't allowed here")
        return op(_evaluate(node.operand))

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            raise ValueError("that function isn't allowed here")
        if node.keywords:
            raise ValueError("named arguments aren't allowed here")
        return _FUNCS[node.func.id](*[_evaluate(a) for a in node.args])

    if isinstance(node, ast.Name):
        if node.id not in _NAMES:
            raise ValueError(f"'{node.id}' isn't something I can work out")
        return _NAMES[node.id]

    raise ValueError("that isn't a sum")


def _clean(text):
    """Tidy up how a person (or a model) actually writes maths."""
    t = str(text or "").strip()
    # models like wrapping things in code fences or an equals sign
    if t.startswith("```"):
        parts = t.split("```")
        t = parts[1] if len(parts) > 1 else t
        if t.lstrip().lower().startswith("math"):
            t = t.lstrip()[4:]
        t = t.strip()
    t = t.lstrip("=").strip()
    if t.endswith("="):
        t = t[:-1].strip()
    # thousands separators: 1,000 -> 1000, but leave min(1, 2) alone
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)
    # "5 x 3" and the proper multiplication sign
    t = re.sub(r"(?<=[\d\s)])[x×](?=[\s(\d])", "*", t)
    t = t.replace("÷", "/").replace("−", "-")
    t = t.replace("^", "**")
    return t


def calculate(expression):
    """The sum, worked out. Raises ValueError if it isn't a valid sum."""
    cleaned = _clean(expression)
    if not cleaned:
        raise ValueError("there's no sum to work out")
    try:
        tree = ast.parse(cleaned, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"that isn't a sum I can read ({e.msg})")
    return _evaluate(tree)


class CalculatorNode(Node):
    TYPE = "core.calculator"
    TITLE = "Calculator"
    CATEGORY = "data"
    INPUTS = 1
    OUTPUTS = 1
    PARAMS = [
        {"key": "expression", "label": "Expression", "type": "text",
         "default": "",
         "desc": "The sum to work out. Point this at whatever field holds "
                 "the maths -- the AI writes the sum, this node gets the "
                 "answer right.",
         "example": "{{ $json.math }}"},
        {"key": "precision", "label": "Decimal places", "type": "number",
         "default": -1,
         "desc": "Round the answer to this many decimal places. -1 (or "
                 "blank) leaves it exactly as calculated."},
        {"key": "field", "label": "Output field", "type": "text",
         "default": "result",
         "desc": "The field name the answer lands in."},
        {"key": "output_mode", "label": "Send on", "type": "select",
         "default": "the whole item",
         "options": ["the whole item", "just the answer"],
         "desc": "the whole item = everything that came in, plus the answer "
                 "(the normal one). just the answer = only the output field, "
                 "nothing else -- handy when the item has picked up a pile of "
                 "memory and file contents on its way here and all you want "
                 "back is the number."},

        {"key": "on_error", "label": "If it isn't a valid sum", "type": "select",
         "default": "error", "options": ["error", "zero", "skip"],
         "desc": "error = put the reason on the item (default). "
                 "zero = answer 0 and carry on. "
                 "skip = leave the item untouched."},
    ]

    def run(self, items):
        field = self.p("field", "result") or "result"
        on_error = self.p("on_error", "error")
        try:
            precision = int(self.p("precision", -1))
        except (TypeError, ValueError):
            precision = -1

        lean = self.p("output_mode", "the whole item") == "just the answer"

        out = []
        for it in (items or [{"json": {}}]):
            j = dict(it.get("json", {})) if isinstance(it.get("json"), dict) else {}
            raw = self.rexpr(self.p("expression", ""), j)

            try:
                value = calculate(raw)
                if precision >= 0:
                    value = round(value, precision)
                    if precision == 0:
                        value = int(value)
                # 6.0 reads better as 6, and downstream comparisons behave
                elif isinstance(value, float) and value.is_integer():
                    value = int(value)
                if lean:
                    out.append({"json": {field: value}})
                    continue
                j[field] = value
                j["expression"] = _clean(raw)
            except Exception as e:
                if on_error == "skip":
                    # nothing was worked out, so in lean mode there is nothing
                    # to send: an empty item, not a stale field pretending to
                    # be this run's answer
                    out.append({"json": {} if lean else j})
                    continue
                if on_error == "zero":
                    if lean:
                        out.append({"json": {field: 0}})
                        continue
                    j[field] = 0
                    j["expression"] = _clean(raw)
                else:
                    if lean:
                        out.append({"json": {"error": f"Calculator: {e}"}})
                        continue
                    j["error"] = f"Calculator: {e}"
            out.append({"json": j})
        return out
