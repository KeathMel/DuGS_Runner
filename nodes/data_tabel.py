"""
Tabel node: read from, insert into, or update rows in a Tabel spreadsheet.

Same idea as n8n's Google Sheets node — same shape of operations, same idea
of picking a "matching column" instead of everything being locked to a
row's internal id.

Operations:
  read    — outputs rows as items. Optional filter, with a choice of how to
            compare (equals, contains, not equals, greater/less than).
  insert  — takes incoming items, adds them as new rows, outputs the new
            rows. (This is the one that used to be called "append" — same
            thing, matching the more familiar name.)
  update  — takes incoming items, updates rows whose "Match on" column
            matches the item's value for that column. Only columns present
            in the incoming item get touched; everything else on the row
            stays as it was.
  upsert  — like update, but if no row matches, it inserts a new one
            instead of doing nothing. The one n8n calls "Upsert."
  delete  — takes incoming items, deletes rows whose "Match on" column
            matches, outputs what got deleted.
  clear   — removes every row, outputs nothing.

SAYING WHICH COLUMN GETS WHAT
=============================
By default a row is built by name-matching: a column called `amount` takes
the incoming item's `amount` field, and anything the item doesn't have is
left blank. Fine when the item already has the right shape, useless when it
doesn't -- an AI writing `{"Finances": "..."}` fills nothing at all.

Set "Columns" to `map each column` and you get a card per column: the
column name on the left, the value on the right, expressions allowed.

    amount    {{ $('Schwortz').item.json.amount }}
    what      {{ $('Schwortz').item.json.what }}
    date      {{ $('Date & Time 37').item.json.result }}

Only the columns you list are written. On update/upsert that means the rest
of the row keeps what it had, so you can change one number without having
to restate the whole row.


AN EMPTY TABEL IS NOT AN ERROR
==============================
A node that outputs zero items stops everything wired after it -- which is
right for an untaken branch, and completely wrong for "the table happens to
be empty today". An empty Finances table should not silently kill the rest
of the workflow.

So when an operation comes back with nothing, this node sends on ONE item
saying exactly that, and the chain carries on:

    { "tabel": "Finances", "operation": "read", "count": 0, "empty": true,
      "rows": [] }

Downstream you can branch on it -- IF `{{ $json.empty }}` is true -- or just
ignore it and let the AI see an empty list. If you actually want the old
"stop here" behaviour on a particular node, set "If it comes back empty" to
`stop the branch`.
"""
import os
import sys
from node_base import Node, resolve_expr

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tabel_store


def _compare(row_val, op, target):
    """One comparison, used by the read filter. Falls back to string
    comparison if the values aren't numbers, so 'greater than' on text still
    does *something* sensible (alphabetical) instead of erroring."""
    if op in ("greater_than", "less_than"):
        try:
            a, b = float(row_val), float(target)
        except (TypeError, ValueError):
            a, b = str(row_val), str(target)
    else:
        a, b = row_val, target

    if op == "equals":
        return str(a) == str(b)
    if op == "not_equals":
        return str(a) != str(b)
    if op == "contains":
        return str(target).lower() in str(a).lower()
    if op == "greater_than":
        try:
            return a > b
        except TypeError:
            return False
    if op == "less_than":
        try:
            return a < b
        except TypeError:
            return False
    return False


class TabelNode(Node):
    TYPE = "data.tabel"
    TITLE = "Tabel"
    CATEGORY = "data"
    INPUTS = 1
    OUTPUTS = 1
    PARAMS = [
        {"key": "tabel", "label": "Tabel", "type": "tabel", "default": "",
         "desc": "Which saved Tabel to work on."},
        {
            "key": "operation",
            "label": "Operation",
            "type": "select",
            "default": "read",
            "options": ["read", "insert", "update", "upsert", "delete", "clear"],
            "desc": "read = get rows. insert = always add new rows. update = "
                    "change matching rows only. upsert = update if found, "
                    "insert if not. delete = remove matching rows. clear = "
                    "wipe every row.",
        },

        # ---- read ----
        {"key": "filter_field", "label": "Filter column", "type": "text",
         "default": "", "desc": "Leave blank to read every row.",
         "show_if": {"operation": "read"}},
        {"key": "filter_operator", "label": "Compare", "type": "select",
         "default": "equals",
         "options": ["equals", "not_equals", "contains", "greater_than", "less_than"],
         "desc": "How the filter column is compared against the value below.",
         "show_if": {"operation": "read"}},
        {"key": "filter_value", "label": "Filter value", "type": "text",
         "default": "", "desc": "{{ }} allowed — resolved against the first "
                                "incoming item.",
         "show_if": {"operation": "read"}},

        # ---- insert / update / upsert: what goes in each column ----
        {"key": "mapping_mode", "label": "Columns", "type": "select",
         "default": "match the item's fields",
         "options": ["match the item's fields", "map each column"],
         "desc": "match the item's fields = a column takes the incoming "
                 "item's field of the same name (the old behaviour). "
                 "map each column = you say which value goes in which "
                 "column, below.",
         "show_if": {"operation": ["insert", "update", "upsert"]}},

        {"key": "column_values", "label": "Column values", "type": "kv_dict",
         "default": {}, "name_hint": "column", "value_hint": "{{ $json.x }}",
         "desc": "One row per column you want written. Left is the column "
                 "name exactly as it appears in the Tabel, right is the "
                 "value -- expressions allowed. Columns you leave out are "
                 "not touched.",
         "show_if": {"operation": ["insert", "update", "upsert"]}},

        # ---- update / upsert / delete: which column identifies a row ----
        {"key": "if_empty", "label": "If it comes back empty", "type": "select",
         "default": "carry on",
         "options": ["carry on", "stop the branch"],
         "desc": "carry on = send one item saying the result was empty, so "
                 "the nodes after this one still run (the default -- an "
                 "empty table is not a reason to stop). stop the branch = "
                 "output nothing, which collapses everything downstream."},

        {"key": "match_field", "label": "Match on", "type": "text",
         "default": "id",
         "desc": "The column that identifies which row is which — like "
                 "picking the 'Matching Column' in a spreadsheet node. "
                 "Defaults to 'id', but any column works: an email, an SKU, "
                 "whatever's unique in your data.",
         "show_if": {"operation": ["update", "upsert", "delete"]}},
    ]

    # ---- helpers ------------------------------------------------------------
    def _load(self):
        name = self.params.get("tabel")
        if not name:
            raise ValueError("Tabel node needs a tabel name")
        try:
            data = tabel_store.load_tabel(name)
        except FileNotFoundError:
            raise ValueError(f"Tabel '{name}' does not exist — create it in the Tabels tab first")
        return name, data

    def _mapped_row(self, cols, item_json):
        """The column -> value mapping, resolved against this item.

        Returns None when mapping is switched off, so callers fall back to
        the original name-matching behaviour.
        """
        if self.p("mapping_mode", "match the item's fields") != "map each column":
            return None
        raw = self.params.get("column_values") or {}
        if isinstance(raw, str):
            try:
                import json as _json
                raw = _json.loads(raw)
            except Exception:
                raw = {}
        if not isinstance(raw, dict):
            return None
        out = {}
        for col, val in raw.items():
            col = str(col).strip()
            if not col:
                continue
            if cols and col not in cols:
                raise ValueError(
                    f"Tabel has no column '{col}' -- it has {', '.join(cols)}")
            out[col] = resolve_expr(val, item_json, context=getattr(self, "_context", {}))
        return out

    def _empty_result(self, name, op, **extra):
        """What to send on when an operation found nothing at all."""
        if self.p("if_empty", "carry on") == "stop the branch":
            return []
        payload = {"tabel": name, "operation": op, "count": 0, "empty": True}
        if op == "read":
            payload["rows"] = []
        payload.update(extra)
        return [{"json": payload}]

    def _find_row(self, rows, match_field, value):
        for r in rows:
            if str(r.get(match_field, "")) == str(value):
                return r
        return None

    # ---- run ------------------------------------------------------------
    def run(self, items):
        name, data = self._load()
        op = (self.params.get("operation") or "read").lower()
        # "append" kept as a silent alias so existing saved workflows built
        # before the rename still run unchanged
        if op == "append":
            op = "insert"

        cols = data.get("columns", [])
        rows = data.get("rows", [])

        if op == "read":
            field = (self.params.get("filter_field") or "").strip()
            operator = self.params.get("filter_operator", "equals")
            value = self.params.get("filter_value", "")
            if items and value:
                value = resolve_expr(str(value), items[0].get("json", {}))
            if field and value != "":
                rows = [r for r in rows if _compare(r.get(field, ""), operator, value)]
            if not rows:
                return self._empty_result(name, "read")
            return [{"json": dict(r)} for r in rows]

        if op == "insert":
            added = []
            for it in (items or []):
                j = it.get("json", {})
                mapped = self._mapped_row(cols, j)
                if mapped is None:
                    row = {c: j.get(c) for c in cols}
                else:
                    # unlisted columns still exist on the row, just empty
                    row = {c: mapped.get(c, "") for c in cols}
                rows.append(row)
                added.append(row)
            data["rows"] = rows
            tabel_store.save_tabel(name, data)
            data = tabel_store.load_tabel(name)   # reload for auto-assigned ids
            saved_rows = data["rows"][-len(added):] if added else []
            if not saved_rows:
                return self._empty_result(name, "insert")
            return [{"json": dict(r)} for r in saved_rows]

        if op in ("update", "upsert"):
            match_field = (self.params.get("match_field") or "id").strip() or "id"
            out = []
            to_insert = []
            for it in (items or []):
                j = it.get("json", {})
                mapped = self._mapped_row(cols, j)
                mval = mapped.get(match_field) if mapped else j.get(match_field)
                row = self._find_row(rows, match_field, mval) if mval is not None else None
                if row is not None:
                    if mapped is None:
                        for c in cols:
                            if c in j:
                                row[c] = j[c]
                    else:
                        # only the columns you listed -- the rest of the row
                        # keeps whatever it already had
                        for c, v in mapped.items():
                            row[c] = v
                    out.append({"json": dict(row)})
                elif op == "upsert":
                    if mapped is None:
                        new_row = {c: j.get(c) for c in cols}
                    else:
                        new_row = {c: mapped.get(c, "") for c in cols}
                    rows.append(new_row)
                    to_insert.append(new_row)
            data["rows"] = rows
            tabel_store.save_tabel(name, data)
            if to_insert:
                # reload so freshly-inserted rows carry their assigned ids too
                data = tabel_store.load_tabel(name)
                out.extend({"json": dict(r)} for r in data["rows"][-len(to_insert):])
            if not out:
                return self._empty_result(name, op)
            return out

        if op == "delete":
            match_field = (self.params.get("match_field") or "id").strip() or "id"
            to_delete = set()
            for it in (items or []):
                j = it.get("json", {})
                mapped = self._mapped_row(cols, j)
                v = mapped.get(match_field) if mapped else j.get(match_field)
                if v is not None:
                    to_delete.add(str(v))
            deleted = [r for r in rows if str(r.get(match_field, "")) in to_delete]
            data["rows"] = [r for r in rows if str(r.get(match_field, "")) not in to_delete]
            tabel_store.save_tabel(name, data)
            if not deleted:
                return self._empty_result(name, "delete")
            return [{"json": dict(r)} for r in deleted]

        if op == "clear":
            removed = len(rows)
            data["rows"] = []
            tabel_store.save_tabel(name, data)
            # clear never has rows to hand on, so without this everything
            # wired after a Clear could never run
            return self._empty_result(name, "clear", cleared=removed)

        raise ValueError(f"Unknown Tabel operation: {op}")
