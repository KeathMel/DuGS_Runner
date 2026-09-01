"""
File Delete — remove a file from disk. Actually deletes it; there's no
trash folder and nothing to undo, so the guards below exist on purpose.

By default this only touches ordinary files. Deleting a folder wipes
everything inside it, so that needs its own switch turned on deliberately
rather than happening by accident because a path resolved to a directory.

'Missing is fine' decides whether a path that isn't there counts as an
error or as nothing-to-do. Off is the default: if you told it to delete
something and that something isn't there, you probably want to know.

SETTINGS
========
path        : the file to delete
missing_ok  : a path that doesn't exist is fine, not an error
allow_dir   : let it delete a folder and everything inside it
"""
import os
import shutil
import sys

from node_base import Node

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FileDeleteNode(Node):
    TYPE = "file.delete"
    TITLE = "Delete File"
    CATEGORY = "action"
    INPUTS = 1
    OUTPUTS = 1
    PARAMS = [
        {"key": "path", "label": "File path", "type": "text", "default": "",
         "desc": "The file to delete. ~ works. Expressions allowed. "
                 "This is permanent — there is no trash folder.",
         "example": "~/notes/{{ $json.name }}.txt"},

        {"key": "missing_ok", "label": "Missing file is fine", "type": "bool",
         "default": False,
         "desc": "On: a path that isn't there is treated as nothing to do. "
                 "Off: it's reported as an error."},

        {"key": "allow_dir", "label": "Allow deleting a folder", "type": "bool",
         "default": False,
         "desc": "Off: only ordinary files can be deleted, a folder is an "
                 "error. On: a folder AND everything inside it is removed. "
                 "Leave this off unless you specifically mean it."},
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
        missing_ok = bool(self.p("missing_ok", False))
        allow_dir = bool(self.p("allow_dir", False))

        out = []
        for it in (items or [{"json": {}}]):
            j = dict(it.get("json", {})) if isinstance(it.get("json", {}), dict) else {}

            path = self._resolve_path(self.p("path", ""), j)
            if not path:
                j["error"] = "Delete File: no path given"
                out.append({"json": j})
                continue

            # a bare "/" or a home directory almost certainly means an
            # expression resolved to nothing — refuse rather than wipe a disk
            if path in ("/", os.path.expanduser("~")):
                j["error"] = f"Delete File: refusing to delete '{path}'"
                out.append({"json": j})
                continue

            if not os.path.exists(path):
                if missing_ok:
                    j["file_path"] = path
                    j["deleted"] = False
                    j["existed"] = False
                    out.append({"json": j})
                else:
                    j["error"] = f"Delete File: '{path}' does not exist"
                    out.append({"json": j})
                continue

            is_dir = os.path.isdir(path)
            if is_dir and not allow_dir:
                j["error"] = (f"Delete File: '{path}' is a folder — turn on "
                              f"'Allow deleting a folder' if you mean it")
                out.append({"json": j})
                continue

            try:
                size = 0
                if is_dir:
                    shutil.rmtree(path)
                else:
                    size = os.path.getsize(path)
                    os.remove(path)

                j["file_path"] = path
                j["deleted"] = True
                j["existed"] = True
                j["was_folder"] = is_dir
                if not is_dir:
                    j["bytes_deleted"] = size

            except Exception as e:
                j["error"] = f"Delete File failed: {type(e).__name__}: {e}"

            out.append({"json": j})
        return out
