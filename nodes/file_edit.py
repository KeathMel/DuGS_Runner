"""
File Edit — read a file, change it, write it back.

Four modes, all writing to the same path they read from:

  replace   : find some text, swap it for something else
  append    : add to the end of what's already there
  prepend   : add to the start
  overwrite : throw the old content away, write the new content

Every text field takes {{ }} expressions, so the path, the thing you're
searching for and the thing you're writing can all come from the item.

The node never silently does nothing: if 'find' isn't in the file, that
comes back as an error on the item rather than a quiet no-op, so a typo in
a search string doesn't look like a successful run.

SETTINGS
========
path              : which file to edit
mode              : replace | append | prepend | overwrite
find              : the text to look for (mode = replace)
replace_with      : what to put in its place (mode = replace)
count             : how many matches to swap, 0 = all (mode = replace)
content           : the text to add / write (append, prepend, overwrite)
create_if_missing : make the file if it isn't there yet
"""
import os
import sys

from node_base import Node

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FileEditNode(Node):
    TYPE = "file.edit"
    TITLE = "File Edit"
    CATEGORY = "action"
    INPUTS = 1
    OUTPUTS = 1
    PARAMS = [
        {"key": "path", "label": "File path", "type": "text", "default": "",
         "desc": "The file to edit. ~ works. Expressions allowed.",
         "example": "~/notes/log.txt"},

        {"key": "format", "label": "File format", "type": "select",
         "default": "text", "options": ["text", "json"],
         "desc": "text = the file is plain text, everything is string work. "
                 "json = the file holds JSON, and it stays real JSON: the "
                 "node parses it, changes it, and writes it back formatted "
                 "instead of pasting your object in as one long string."},

        {"key": "match_key", "label": "Match on (json replace)", "type": "text",
         "default": "id",
         "desc": "In JSON mode with mode=replace: the key used to find which "
                 "entry to update. An entry with the same value for this key "
                 "is replaced; if none matches it gets added."},

        {"key": "mode", "label": "Mode", "type": "select", "default": "replace",
         "options": ["replace", "append", "prepend", "overwrite"],
         "desc": "replace = swap some text. append/prepend = add to the end "
                 "or the start. overwrite = throw the old content away."},

        {"key": "find", "label": "Find", "type": "multiline", "default": "",
         "desc": "The text to search for. Must actually be in the file, or "
                 "the node reports an error instead of doing nothing.",
         "example": "status: pending",
         "show_if": {"mode": "replace"}},
        {"key": "replace_with", "label": "Replace with", "type": "multiline",
         "default": "",
         "desc": "What to put in its place. Blank deletes the found text.",
         "example": "status: done",
         "show_if": {"mode": "replace"}},
        {"key": "count", "label": "How many matches", "type": "number",
         "default": 0,
         "desc": "How many occurrences to swap. 0 means every one of them.",
         "show_if": {"mode": "replace"}},

        {"key": "content", "label": "Content", "type": "multiline", "default": "",
         "desc": "The text to add or write. Expressions allowed.",
         "example": "{{ $json.reply }}",
         "show_if": {"mode": ["append", "prepend", "overwrite"]}},

        {"key": "create_if_missing", "label": "Create the file if it doesn't exist",
         "type": "bool", "default": True,
         "desc": "On: a missing file is created (and its folders too). "
                 "Off: a missing file is an error."},
    ]

    # ---- JSON mode ---------------------------------------------------------
    def _json_edit(self, original, mode, j, path):
        """Edit the file as real JSON. Returns (new_text, changes, error).

        The whole point: an AI that hands you {"title": "x"} should end up as
        an entry in the file, not as a line of text jammed onto the end. So
        the file is parsed, changed as data, and written back formatted.
        """
        import json as _json

        # what's already in the file
        if original.strip():
            try:
                data = _json.loads(original)
            except ValueError as e:
                return None, 0, f"File Edit: '{os.path.basename(path)}' isn't valid JSON ({e})"
        else:
            data = []          # empty/new file starts as a list

        if mode == "overwrite":
            payload, err = self._json_payload(j)
            if err:
                return None, 0, err
            return _json.dumps(payload, indent=2, ensure_ascii=False), 1, None

        payload, err = self._json_payload(j)
        if err:
            return None, 0, err

        if mode in ("append", "prepend"):
            if not isinstance(data, list):
                return None, 0, ("File Edit: append/prepend needs the file to "
                                 "hold a JSON list, but it holds a "
                                 f"{type(data).__name__}")
            add = payload if isinstance(payload, list) else [payload]
            data = (data + add) if mode == "append" else (add + data)
            return _json.dumps(data, indent=2, ensure_ascii=False), len(add), None

        if mode == "replace":
            key = str(self.p("match_key", "id") or "id")
            if not isinstance(data, list):
                # a plain object: merge the new keys over the old ones
                if isinstance(data, dict) and isinstance(payload, dict):
                    data.update(payload)
                    return _json.dumps(data, indent=2, ensure_ascii=False), 1, None
                return None, 0, "File Edit: replace needs a JSON list or object"
            if not isinstance(payload, dict):
                return None, 0, "File Edit: replace needs the content to be a JSON object"
            target = payload.get(key)
            if target is None:
                return None, 0, f"File Edit: the content has no '{key}' to match on"
            for i, entry in enumerate(data):
                if isinstance(entry, dict) and entry.get(key) == target:
                    data[i] = payload
                    return _json.dumps(data, indent=2, ensure_ascii=False), 1, None
            # nothing matched -- add it rather than failing, which is what
            # "save this task" means when the task is new
            data.append(payload)
            return _json.dumps(data, indent=2, ensure_ascii=False), 1, None

        return None, 0, f"File Edit: unknown mode '{mode}'"

    def _json_payload(self, j):
        """The Content field, as real JSON data."""
        import json as _json
        raw = self.rexpr(self.p("content", ""), j)
        if isinstance(raw, (dict, list)):
            return raw, None          # already data, nothing to parse
        text = str(raw or "").strip()
        if not text:
            return None, "File Edit: Content is empty"
        # models like to wrap JSON in ```json fences
        if text.startswith("```"):
            parts = text.split("```")
            text = parts[1] if len(parts) > 1 else text
            if text.lstrip().startswith("json"):
                text = text.lstrip()[4:]
            text = text.strip()
        try:
            return _json.loads(text), None
        except ValueError as e:
            return None, f"File Edit: Content isn't valid JSON ({e})"

    def _resolve_path(self, raw, j):
        p = self.rexpr(raw, j)
        if not isinstance(p, str):
            p = str(p)
        p = p.strip()
        if not p:
            return ""
        return os.path.abspath(os.path.expanduser(os.path.expandvars(p)))

    def run(self, items):
        mode = self.p("mode", "replace")
        create_missing = bool(self.p("create_if_missing", True))

        out = []
        for it in (items or [{"json": {}}]):
            j = dict(it.get("json", {})) if isinstance(it.get("json", {}), dict) else {}

            path = self._resolve_path(self.p("path", ""), j)
            if not path:
                j["error"] = "File Edit: no path given"
                out.append({"json": j})
                continue

            exists = os.path.isfile(path)
            if not exists and not create_missing:
                j["error"] = f"File Edit: '{path}' does not exist"
                out.append({"json": j})
                continue
            if os.path.isdir(path):
                j["error"] = f"File Edit: '{path}' is a folder, not a file"
                out.append({"json": j})
                continue

            try:
                original = ""
                if exists:
                    with open(path, "r", encoding="utf-8") as f:
                        original = f.read()

                # ---- JSON mode: keep the file real JSON -----------------
                if self.p("format", "text") == "json":
                    new_text, swapped, err = self._json_edit(original, mode, j, path)
                    if err:
                        j["error"] = err
                        out.append({"json": j})
                        continue

                elif mode == "replace":
                    find = self.rexpr(self.p("find", ""), j)
                    if not isinstance(find, str):
                        find = str(find)
                    if not find:
                        j["error"] = "File Edit: 'Find' is empty"
                        out.append({"json": j})
                        continue
                    if find not in original:
                        j["error"] = (f"File Edit: '{find[:60]}' not found in "
                                      f"'{os.path.basename(path)}'")
                        out.append({"json": j})
                        continue
                    repl = self.rexpr(self.p("replace_with", ""), j)
                    if not isinstance(repl, str):
                        repl = str(repl)
                    try:
                        n = int(self.p("count", 0) or 0)
                    except (TypeError, ValueError):
                        n = 0
                    swapped = original.count(find) if n <= 0 else min(n, original.count(find))
                    new_text = (original.replace(find, repl) if n <= 0
                                else original.replace(find, repl, n))

                elif mode == "append":
                    add = self.rexpr(self.p("content", ""), j)
                    if not isinstance(add, str):
                        add = str(add)
                    new_text = original + add
                    swapped = 0

                elif mode == "prepend":
                    add = self.rexpr(self.p("content", ""), j)
                    if not isinstance(add, str):
                        add = str(add)
                    new_text = add + original
                    swapped = 0

                elif mode == "overwrite":
                    add = self.rexpr(self.p("content", ""), j)
                    if not isinstance(add, str):
                        add = str(add)
                    new_text = add
                    swapped = 0

                else:
                    j["error"] = f"File Edit: unknown mode '{mode}'"
                    out.append({"json": j})
                    continue

                # write through a temp file then swap it in, so a crash
                # mid-write can never leave the original half-destroyed
                parent = os.path.dirname(path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(new_text)
                os.replace(tmp, path)

                j["file_path"] = path
                j["file_mode"] = mode
                j["file_created"] = not exists
                j["bytes_written"] = len(new_text.encode("utf-8"))
                j["bytes_before"] = len(original.encode("utf-8"))
                j["changed"] = new_text != original
                if mode == "replace":
                    j["replacements"] = swapped

            except Exception as e:
                j["error"] = f"File Edit failed: {type(e).__name__}: {e}"

            out.append({"json": j})
        return out
