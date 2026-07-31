"""
node_base.py — the contract every node follows.
"""

from __future__ import annotations
import re
from typing import Any

EXPR_RE = re.compile(r"\{\{\s*(.*?)\s*\}\}")
NODE_REF_RE = re.compile(r"^\$\((['\"])(.*?)\1\)\.(?:item\.)?json(.*)$")


def resolve_expr(value: Any, item_json: dict, context: dict | None = None) -> Any:
    """Interpolate {{ $json.field }} or {{ $('Node Name').item.json.field }} expressions."""
    if not isinstance(value, str):
        return value
    matches = EXPR_RE.findall(value)
    if not matches:
        return value

    if EXPR_RE.fullmatch(value.strip()):
        expr = matches[0].strip()
        return _eval_expr(expr, item_json, context)

    def replacer(m):
        result = _eval_expr(m.group(1).strip(), item_json, context)
        return str(result) if result is not None else ""

    return EXPR_RE.sub(replacer, value)


def _eval_expr(expr: str, item_json: dict, context: dict | None = None) -> Any:
    """Evaluate an expression like '$json.field' or '$(\'Node Name\').item.json.field'"""
    target_dict = item_json
    path_str = ""

    # Check for $('Node Name').item.json.path or $('Node Name').json.path
    node_match = NODE_REF_RE.match(expr)
    if node_match:
        node_name = node_match.group(2)
        path_str = node_match.group(3)
        context = context or {}
        node_items = context.get(node_name, [])
        if node_items and isinstance(node_items, list):
            target_dict = node_items[0].get("json", {}) if isinstance(node_items[0], dict) else {}
        else:
            target_dict = {}
    elif expr.startswith("$json"):
        path_str = expr[5:]
    else:
        # Fallback raw lookup
        path_str = expr

    # Traverse nested dot-notation paths
    val = target_dict
    if path_str:
        for part in path_str.lstrip(".").split("."):
            if not part:
                continue
            if isinstance(val, dict):
                val = val.get(part)
            elif isinstance(val, list):
                try:
                    val = val[int(part)]
                except (ValueError, IndexError):
                    val = None
            else:
                val = None
            if val is None:
                break
    return val


def make_item(data: dict | None = None) -> dict:
    return {"json": data or {}}


class Node:
    TYPE: str = "base"
    TITLE: str = "Base Node"
    CATEGORY: str = "core"
    INPUTS: int = 1
    OUTPUTS: int = 1
    PARAMS: list[dict] = []

    def __init__(self, name: str, params: dict | None = None):
        self.name = name
        self.params = params or {}
        self._context: dict = {}

    def rexpr(self, value: Any, item_json: dict) -> Any:
        ctx = getattr(self, "_context", {})
        return resolve_expr(value, item_json, context=ctx)

    def resolve(self, key: str, item_json: dict, default: Any = None) -> Any:
        val = self.params.get(key, default)
        ctx = getattr(self, "_context", {})
        return resolve_expr(val, item_json, context=ctx)

    def p(self, key: str, default: Any = None) -> Any:
        val = self.params.get(key, default)
        return default if val is None else val

    def run(self, items: list[dict]) -> list[dict] | list[list[dict]]:
        raise NotImplementedError(f"Node {self.TYPE} has no run() implemented")

    def __repr__(self):
        return f"<{self.TYPE} '{self.name}'>"
