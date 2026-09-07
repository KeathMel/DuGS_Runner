"""
File Read — read a file off disk and put its contents into the item.

The counterpart to Create/Edit/Delete. Give it a path; the text lands in a
field you choose, so the next node can use {{ $json.content }} (or whatever
you named it) straight away.

The 'read as' setting decides what you get back:

  text  : the whole file as a string (the usual one)
  json  : the file parsed as JSON, so you get real fields to reference
  lines : the file split into a list of lines
  items : one OUTPUT ITEM PER LINE — the file becomes a stream the rest of
          the workflow loops over, the same shape Split Out produces

'If it is missing' decides what a missing file means. Sometimes a missing
file is a genuine error; often it just means "nothing yet", and you want the
workflow to carry on with an empty string rather than stop.

SETTINGS
========
path        : which file to read
read_as     : text | json | lines | items
output_field: which field the contents land in
if_missing  : error | empty | skip
max_bytes   : refuse anything bigger, so a stray path can't eat all the RAM
encoding    : how to decode the bytes
"""
import json as _json
import os
import sys

from node_base import Node

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FileReadNode(Node):
    TYPE = "file.read"
    TITLE = "Read File"
    CATEGORY = "action"
    INPUTS = 1
    OUTPUTS = 1
    PARAMS = [
        {"key": "path", "label": "File path", "type": "text", "default": "",
         "desc": "Which file to read. ~ works. Expressions allowed.",
         "example": "~/notes/{{ $json.name }}.txt"},

        {"key": "read_as", "label": "Read as", "type": "select",
         "default": "text", "options": ["text", "json", "lines", "items"],
         "desc": "text = one string. json = parsed into real fields. "
                 "lines = a list of lines. "
                 "items = one output item per line, like Split Out."},

        {"key": "output_field", "label": "Output field", "type": "text",
         "default": "content",
         "desc": "Which field the contents land in, so the next node can "
                 "reference it.",
         "example": "content",
         "show_if": {"read_as": ["text", "lines", "items"]}},

        {"key": "json_into_item", "label": "Merge JSON into the item",
         "type": "bool", "default": True,
         "desc": "On: the file's keys become fields on the item directly. "
                 "Off: the parsed object goes into the output field.",
         "show_if": {"read_as": "json"}},

        {"key": "if_missing", "label": "If it is missing", "type": "select",
         "default": "error", "options": ["error", "empty", "skip"],
         "desc": "error = stop and say so. "
                 "empty = carry on with nothing in the field. "
                 "skip = drop this item from the output entirely."},

        {"key": "trim", "label": "Trim whitespace", "type": "bool",
         "default": False,
         "desc": "Strip leading and trailing whitespace, including the "
                 "trailing newline most files end with."},

        {"key": "max_bytes", "label": "Size limit (bytes)", "type": "number",
         "default": 5242880,
         "desc": "Refuse to read anything larger. Stops a wrong path from "
                 "pulling a huge file into memory. 0 means no limit."},

        {"key": "encoding", "label": "Encoding", "type": "text",
         "default": "utf-8",
         "desc": "How to decode the bytes. Anything undecodable is replaced "
                 "rather than crashing the run."},
    ]

    def _resolve_path(self, raw, j):
        p = self.rexpr(raw, j)
        # an expression that matched nothing comes back as None. str() would
        # turn that into the literal "None" and read a file called None in
        # the working directory — report "no path" instead.
        if p is None:
            return ""
        if not isinstance(p, str):
            p = str(p)
        p = p.strip()
        if not p:
            return ""
        return os.path.abspath(os.path.expanduser(os.path.expandvars(p)))

    def run(self, items):
        read_as = self.p("read_as", "text")
        field = (self.p("output_field", "content") or "content").strip() or "content"
        if_missing = self.p("if_missing", "error")
        trim = bool(self.p("trim", False))
        merge_json = bool(self.p("json_into_item", True))
        encoding = (self.p("encoding", "utf-8") or "utf-8").strip() or "utf-8"
        try:
            max_bytes = int(self.p("max_bytes", 5242880) or 0)
        except (TypeError, ValueError):
            max_bytes = 5242880

        out = []
        for it in (items or [{"json": {}}]):
            src = it.get("json", {})
            j = dict(src) if isinstance(src, dict) else {}

            path = self._resolve_path(self.p("path", ""), j)
            if not path:
                j["error"] = "Read File: no path given"
                out.append({"json": j})
                continue

            if os.path.isdir(path):
                j["error"] = f"Read File: '{path}' is a folder, not a file"
                out.append({"json": j})
                continue

            if not os.path.exists(path):
                # a missing file is often "nothing yet" rather than a failure,
                # so this is the caller's decision, not ours
                if if_missing == "skip":
                    continue
                if if_missing == "empty":
                    j["file_path"] = path
                    j["file_exists"] = False
                    j[field] = [] if read_as in ("lines", "items") else ""
                    out.append({"json": j})
                    continue
                j["error"] = f"Read File: '{path}' does not exist"
                out.append({"json": j})
                continue

            try:
                size = os.path.getsize(path)
                if max_bytes and size > max_bytes:
                    j["error"] = (f"Read File: '{path}' is {size} bytes, over the "
                                  f"{max_bytes} limit")
                    out.append({"json": j})
                    continue

                with open(path, "r", encoding=encoding, errors="replace") as f:
                    text = f.read()
                if trim:
                    text = text.strip()

                j["file_path"] = path
                j["file_exists"] = True
                j["bytes_read"] = size

                if read_as == "json":
                    try:
                        parsed = _json.loads(text) if text.strip() else {}
                    except Exception as e:
                        j["error"] = f"Read File: '{path}' is not valid JSON: {e}"
                        out.append({"json": j})
                        continue
                    if merge_json and isinstance(parsed, dict):
                        # never let file contents quietly overwrite the fields
                        # this node just set — an "error" key in the file
                        # would look like the read itself failed
                        reserved = {"file_path", "file_exists", "bytes_read", "error"}
                        for k, v in parsed.items():
                            if k not in reserved:
                                j[k] = v
                    else:
                        j[field] = parsed
                    out.append({"json": j})
                    continue

                lines = text.splitlines()

                if read_as == "lines":
                    j[field] = lines
                    j["line_count"] = len(lines)
                    out.append({"json": j})
                    continue

                if read_as == "items":
                    # one item per line: the file becomes a stream the rest of
                    # the workflow can loop over, same shape as Split Out
                    for i, line in enumerate(lines):
                        row = dict(j)
                        row[field] = line
                        row["line_number"] = i + 1
                        out.append({"json": row})
                    if not lines:
                        j[field] = ""
                        j["line_count"] = 0
                        out.append({"json": j})
                    continue

                j[field] = text
                j["line_count"] = len(lines)

            except Exception as e:
                j["error"] = f"Read File failed: {type(e).__name__}: {e}"

            out.append({"json": j})
        return out
