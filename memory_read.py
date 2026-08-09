"""
Memory Read — read a value back out of a Memory Bank.

A Memory Bank is DuGS's simple key/value store (make one on the home screen,
next to Tabels). This node pulls a saved value out by its key. Expired
entries count as missing, so you never read stale data.

MODES
=====
  key  — read one key, output its value
  all  — output entries as items. Choose which ones:
           all      — everything in the bank
           oldest   — just the oldest N entries
           newest   — just the newest N entries
         Handy for capping how much of a growing log (built with Memory
         Write's Append) reaches an AI node at once — feed it just the
         last 10 messages instead of the whole history, for example.

SETTINGS
========
bank      : which memory bank to read from
mode      : key | all
key       : which key to read (mode = key); {{ }} allowed
which     : all | oldest | newest (mode = all)
count     : how many to take, for oldest/newest (mode = all)
field     : what field name to put the value under in the output item
"""
import os
import sys

from node_base import Node

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import storage


class MemoryReadNode(Node):
    TYPE = "memory.read"
    TITLE = "Memory Read"
    CATEGORY = "data"
    INPUTS = 1
    OUTPUTS = 1
    PARAMS = [
        {"key": "bank", "label": "Memory Bank", "type": "memory", "default": "",
         "desc": "Which memory bank to read from."},
        {"key": "mode", "label": "Mode", "type": "select", "default": "key",
         "options": ["key", "all"],
         "desc": "Read one key, or output a list of entries."},
        {"key": "key", "label": "Key", "type": "text", "default": "",
         "desc": "Which entry to read.", "example": "chat_history",
         "show_if": {"mode": "key"}},
        {"key": "which", "label": "Which entries", "type": "select",
         "default": "all", "options": ["all", "oldest", "newest"],
         "desc": "Everything, or just a capped slice from one end of the "
                 "list — e.g. only the last 10 messages of a growing log, "
                 "not the whole history.",
         "show_if": {"mode": "all"}},
        {"key": "count", "label": "How many", "type": "number", "default": 5,
         "desc": "How many entries to take, for oldest/newest.",
         "show_if": {"mode": "all", "which": ["oldest", "newest"]}},
        {"key": "field", "label": "Output field", "type": "text",
         "default": "value",
         "desc": "The field name the value is placed under in the output."},
    ]

    def run(self, items):
        bank = self.p("bank")
        if not bank:
            return items
        mode = self.p("mode", "key")
        field = self.p("field", "value") or "value"

        if mode == "all":
            which = self.p("which", "all")
            if which == "all":
                data = storage.memory_all(bank)
                return [{"json": {"key": k, field: v}} for k, v in data.items()] or \
                       [{"json": {}}]
            count = int(self.p("count", 5) or 5)
            rows = storage.memory_all_sorted(bank, order=which, limit=count)
            return [{"json": {"key": k, field: v}} for k, v, _ts in rows] or \
                   [{"json": {}}]

        out = []
        src = items or [{"json": {}}]
        for it in src:
            key = self.rexpr(self.p("key", ""), it.get("json", {}))
            val = storage.memory_get(bank, key)
            new = dict(it.get("json", {}))
            new[field] = val
            new["key"] = key
            out.append({"json": new})
        return out
