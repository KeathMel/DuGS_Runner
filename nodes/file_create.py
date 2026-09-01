"""
File Create — write a brand new file.

Give it a path and some content. Folders along the way get made for you,
so you don't have to create the directory first.

The 'if it already exists' setting decides what happens when there's
already a file sitting at that path:

  error     : stop and report it — nothing is touched (the safe default)
  overwrite : replace whatever was there
  skip      : leave the existing file alone, carry on quietly

SETTINGS
========
path        : where to write the file
content     : what goes in it
if_exists   : error | overwrite | skip
make_dirs   : create any missing folders in the path
"""
import os
import sys

from node_base import Node

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FileCreateNode(Node):
    TYPE = "file.create"
    TITLE = "Create File"
    CATEGORY = "action"
    INPUTS = 1
    OUTPUTS = 1
    PARAMS = [
        {"key": "path", "label": "File path", "type": "text", "default": "",
         "desc": "Where to write the file. ~ works. Expressions allowed.",
         "example": "~/notes/{{ $json.name }}.txt"},

        {"key": "content", "label": "Content", "type": "multiline", "default": "",
         "desc": "What goes in the file. Expressions allowed. Blank makes "
                 "an empty file.",
         "example": "{{ $json.reply }}"},

        {"key": "if_exists", "label": "If it already exists", "type": "select",
         "default": "error", "options": ["error", "overwrite", "skip"],
         "desc": "error = stop and say so, nothing touched. "
                 "overwrite = replace it. "
                 "skip = leave the existing file alone and move on."},

        {"key": "make_dirs", "label": "Create missing folders", "type": "bool",
         "default": True,
         "desc": "On: any folders in the path that don't exist get made. "
                 "Off: a missing folder is an error."},
    ]

    def _resolve_path(self, raw, j):
        p = self.rexpr(raw, j)
        if not isinstance(p, str):
            p = str(p)
        p = p.strip()
        if not p:
            return ""
        return os.path.abspath(os.path.expanduser(os.path.expandvars(p)))

    def run(self, items):
        if_exists = self.p("if_exists", "error")
        make_dirs = bool(self.p("make_dirs", True))

        out = []
        for it in (items or [{"json": {}}]):
            j = dict(it.get("json", {})) if isinstance(it.get("json", {}), dict) else {}

            path = self._resolve_path(self.p("path", ""), j)
            if not path:
                j["error"] = "Create File: no path given"
                out.append({"json": j})
                continue

            if os.path.isdir(path):
                j["error"] = f"Create File: '{path}' is an existing folder"
                out.append({"json": j})
                continue

            existed = os.path.exists(path)
            if existed:
                if if_exists == "error":
                    j["error"] = f"Create File: '{path}' already exists"
                    out.append({"json": j})
                    continue
                if if_exists == "skip":
                    j["file_path"] = path
                    j["file_created"] = False
                    j["skipped"] = True
                    out.append({"json": j})
                    continue

            try:
                content = self.rexpr(self.p("content", ""), j)
                if not isinstance(content, str):
                    content = str(content)

                parent = os.path.dirname(path)
                if parent and not os.path.isdir(parent):
                    if not make_dirs:
                        j["error"] = f"Create File: folder '{parent}' does not exist"
                        out.append({"json": j})
                        continue
                    os.makedirs(parent, exist_ok=True)

                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(content)
                os.replace(tmp, path)

                j["file_path"] = path
                j["file_created"] = not existed
                j["overwritten"] = existed
                j["skipped"] = False
                j["bytes_written"] = len(content.encode("utf-8"))

            except Exception as e:
                j["error"] = f"Create File failed: {type(e).__name__}: {e}"

            out.append({"json": j})
        return out
