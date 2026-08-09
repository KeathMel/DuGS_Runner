"""
Memory Manage — AI-driven cleanup for a Memory Bank.

Memory Write just writes entries — with Append on, every write becomes its
own separate entry, so a bank can fill up with a long spread-out log over
time (a chat history, an event stream). This node is the maintenance step:
once the run has spent more than your token threshold, it takes everything
currently in the bank, asks the AI to boil it down, and either replaces the
whole thing with one summary entry or adds the summary alongside what's
already there.

Run it after a Memory Write in the workflow, or on its own schedule — it's
a separate node on purpose, so writing to memory and cleaning it up aren't
tangled into one setting on one node.

SETTINGS
========
bank            : which memory bank to manage
credential      : saved AI credential (api key, and optional base_url / model)
token_threshold : only compact once the run has used more than this many
                  AI tokens — so it doesn't fire on every single run
system_prompt   : your instructions for how the AI should summarise
compact_mode    : replace everything in the bank with the summary, or add
                  the summary as one more entry alongside what's there
summary_key     : the key the resulting summary is stored under
"""
import os
import sys

from node_base import Node

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import storage
import ai_helper


class MemoryManageNode(Node):
    TYPE = "memory.manage"
    TITLE = "Memory Manage"
    CATEGORY = "data"
    INPUTS = 1
    OUTPUTS = 1
    PARAMS = [
        {"key": "bank", "label": "Memory Bank", "type": "memory", "default": "",
         "desc": "Which memory bank to manage."},
        {"key": "credential", "label": "AI credential", "type": "select",
         "default": "", "options_from": "credentials",
         "desc": "A saved credential holding the AI api key (and optional "
                 "base_url / model)."},
        {"key": "token_threshold", "label": "Compact after N tokens",
         "type": "number", "default": 2000,
         "desc": "Only compact once the run has used more than this many "
                 "AI tokens — so it doesn't fire on every single run."},
        {"key": "system_prompt", "label": "Compaction instructions",
         "type": "multiline",
         "default": "Summarise the following, keeping only the important facts. "
                    "Be concise.",
         "desc": "How the AI should boil down everything in the bank."},
        {"key": "compact_mode", "label": "Result", "type": "select",
         "default": "replace", "options": ["replace", "add"],
         "desc": "replace = wipe the bank and leave just the summary. "
                 "add = keep everything and add the summary as one more entry."},
        {"key": "summary_key", "label": "Summary key", "type": "text",
         "default": "summary",
         "desc": "The key the resulting summary is stored under."},
    ]

    def _credential(self):
        name = self.p("credential", "")
        if not name:
            return None
        try:
            d = storage.load_credential(name) or {}
        except Exception:
            return None
        key = (d.get("api_key") or d.get("token") or d.get("key") or "").strip()
        if not key:
            return None
        return {
            "api_key": key,
            "base_url": d.get("base_url") or "https://api.openai.com/v1",
            "model": d.get("model") or "gpt-4o-mini",
        }

    def run(self, items):
        bank = self.p("bank")
        if not bank:
            return items or [{"json": {}}]

        threshold = int(self.p("token_threshold", 2000) or 0)
        used = ai_helper.tokens_used()
        if used <= threshold:
            note = f"not yet — {used}/{threshold} tokens used this run"
            return [{"json": {**(it.get("json", {})), "compaction": note}}
                   for it in (items or [{"json": {}}])]

        cred = self._credential()
        if cred is None:
            note = "compact skipped: no valid AI credential"
            return [{"json": {**(it.get("json", {})), "compaction": note}}
                   for it in (items or [{"json": {}}])]

        current = storage.memory_all(bank)   # {key: value, ...}, live entries only
        if not current:
            note = "nothing to compact — bank is empty"
            return [{"json": {**(it.get("json", {})), "compaction": note}}
                   for it in (items or [{"json": {}}])]

        blob = "\n\n".join(f"[{k}]\n{v}" for k, v in current.items())
        try:
            summary, spent = ai_helper.chat(
                cred["api_key"], prompt=blob,
                system=self.p("system_prompt", ""),
                model=cred["model"], base_url=cred["base_url"])
        except Exception as e:
            note = f"compact failed: {e}"
            return [{"json": {**(it.get("json", {})), "compaction": note}}
                   for it in (items or [{"json": {}}])]

        summary_key = self.p("summary_key", "summary") or "summary"
        mode = self.p("compact_mode", "replace")
        if mode == "replace":
            storage.memory_clear(bank)
        storage.memory_set(bank, summary_key, summary)

        note = f"compacted {len(current)} entries into '{summary_key}' " \
               f"(+{spent} tokens, {mode})"
        out = []
        for it in (items or [{"json": {}}]):
            j = dict(it.get("json", {}))
            j["compaction"] = note
            j["summary"] = summary
            out.append({"json": j})
        return out
