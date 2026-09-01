"""
Memory Write — save a value into a Memory Bank.

A Memory Bank is DuGS's simple key/value store (make one on the home screen,
next to Tabels). This node writes a value under a key. You can give it a
time limit so the entry expires on its own.

For AI-driven summarising/compacting of a memory bank, use the Memory
Manage node instead — that's a separate step, not something this node does
on write.

APPEND
======
On means: write a brand NEW, separate entry — not grow whatever's already
under this key. Each append is its own item, so a chat log or an event
stream reads back as a proper list of entries (via Memory Read's "all"
mode), not one blob that keeps getting longer forever.

Off means: replace whatever's already under this key.

SETTINGS
========
bank        : which memory bank to write to
key         : the key to store under; {{ }} allowed
value       : what to store; {{ }} allowed
ttl_minutes : minutes until it expires (0 = never)
append      : write a new separate entry instead of replacing this key
"""
import os
import sys

from node_base import Node

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import storage


class MemoryWriteNode(Node):
    TYPE = "memory.write"
    TITLE = "Memory Write"
    CATEGORY = "data"
    INPUTS = 1
    OUTPUTS = 1
    PARAMS = [
        {"key": "bank", "label": "Memory Bank", "type": "memory", "default": "",
         "desc": "Which memory bank to write to."},
        {"key": "key", "label": "Key", "type": "text", "default": "",
         "desc": "The key to store the value under.", "example": "chat_history"},
        {"key": "value", "label": "Value", "type": "multiline", "default": "",
         "desc": "What to store. Expressions allowed.",
         "example": "{{ $json.message }}"},
        {"key": "ttl_minutes", "label": "Expire after (minutes)", "type": "number",
         "default": 0,
         "desc": "How long the entry lives. 0 means it never expires."},
        {"key": "append", "label": "Append (new entry, not an edit)", "type": "bool",
         "default": False,
         "desc": "On: write a brand new separate entry, every time. "
                 "Off: replace whatever's already under this key."},
    ]

    def run(self, items):
        bank = self.p("bank")
        if not bank:
            return items or [{"json": {}}]
        ttl = int(self.p("ttl_minutes", 0) or 0)
        ttl_seconds = ttl * 60 if ttl > 0 else None
        append = bool(self.p("append", False))

        # Write ONCE per workflow run
        key = self.rexpr(self.p("key", ""), {})
        value = self.rexpr(self.p("value", ""), {})
        
        stored, actual_key = storage.memory_set(
            bank, key, value, ttl_seconds=ttl_seconds, append=append)

        # Pass all items through, add memory info to first item only
        out = []
        for idx, it in enumerate(items or [{"json": {}}]):
            j = dict(it.get("json", {}))
            if idx == 0:  # Add to first item only
                j["memory_key"] = actual_key
                j["memory_value"] = stored
            out.append({"json": j})
        return out
