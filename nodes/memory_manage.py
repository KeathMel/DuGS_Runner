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
        {"key": "monitor_node", "label": "Monitor AI Node", "type": "text",
         "default": "",
         "desc": "Name of the AI node to monitor (e.g., 'OpenRouter AI 4')."},
        {"key": "provider", "label": "AI provider", "type": "select",
         "default": "generic", "options": ["generic", "deepseek", "openrouter"],
         "desc": "Which AI backend handles the compaction call — matches the "
                 "AI Agent (DeepSeek) and OpenRouter AI nodes exactly, right "
                 "down to which credential/model fields show below."},

        # ---- credential: same 3-part shape as AI Agent / OpenRouter AI --
        # a saved credential, OR a manual paste, so a mis-saved credential
        # still works without having to go fix it first
        {"key": "credential", "label": "Credential (saved token)", "type": "select",
         "default": "", "options_from": "credentials",
         "desc": "A saved credential holding the AI api key."},
        {"key": "api_key", "label": "or paste API key", "type": "text", "default": "",
         "desc": "Pasted here directly, used if no saved credential is picked "
                 "above (or if the credential itself doesn't resolve)."},

        # ---- model: single text field, user types the model name
        {"key": "model", "label": "Model", "type": "text",
         "default": "gpt-4o-mini",
         "desc": "Model name for your AI provider (e.g., gpt-4o-mini for generic/OpenAI, meta-llama/llama-3.3-70b-instruct for OpenRouter, deepseek-v4-flash for DeepSeek)."},

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

    # the real, fixed endpoints the AI Agent and OpenRouter AI nodes each use
    # -- picking a provider here means "call it exactly the way that node
    # would", not a guessed generic URL
    _PROVIDER_DEFAULTS = {
        "deepseek": {"base_url": "https://api.deepseek.com",
                    "model": "deepseek-v4-flash", "extra_headers": None},
        "openrouter": {"base_url": "https://openrouter.ai/api/v1",
                       "model": "openai/gpt-4o-mini",
                       "extra_headers": {"HTTP-Referer": "https://github.com/KeathMel/DuGS_LINUX"}},
    }

    def _resolve_key(self):
        """Same fallback chain as the AI Agent node: prefer a saved
        credential; fall back to a pasted key; if the credential field
        itself holds a raw key (mis-pasted there instead of below), use it
        directly rather than failing."""
        cred = (self.p("credential") or "").strip()
        if cred:
            if cred.startswith("sk-") or len(cred) > 40:
                return cred
            try:
                d = storage.load_credential(cred) or {}
                tok = (d.get("api_key") or d.get("token") or d.get("key") or "").strip()
                if tok:
                    return tok
            except Exception:
                pass
        return (self.p("api_key") or "").strip()

    def _credential(self):
        api_key = self._resolve_key()
        if not api_key:
            return None
        name = self.p("credential", "")
        d = {}
        if name and not (name.startswith("sk-") or len(name) > 40):
            try:
                d = storage.load_credential(name) or {}
            except Exception:
                d = {}
        return {
            "api_key": api_key,
            # no fallback injected here on purpose -- "" means neither the
            # saved credential nor anything else specified one, which the
            # provider-preset logic in run() needs to be able to tell apart
            # from "it did"
            "base_url": d.get("base_url") or "",
            "model": d.get("model") or "",
        }

    def _safe_json(self, item):
        """Ensure item['json'] is always a dict, never a string."""
        if not isinstance(item, dict):
            return {}
        json_val = item.get("json", {})
        return json_val if isinstance(json_val, dict) else {}

    def run(self, items):
        bank = self.p("bank")
        if not bank:
            return items or [{"json": {}}]

        threshold = int(self.p("token_threshold", 2000) or 0)
        monitor_node = self.p("monitor_node", "").strip()
        
        # Token count. When a node name is given, read it out of that node's
        # OWN output via the engine's cross-node context -- NOT out of `items`.
        # Memory Manage is usually wired downstream of Respond to Webhook, so
        # the items reaching it are the response body and never carry
        # tokens_used at all. self._context is {node_name: [items...]} for
        # every node that has produced output so far this run, which is the
        # same mechanism {{ $('Node').item.json.x }} uses.
        if monitor_node:
            used = 0
            context = getattr(self, "_context", {}) or {}
            node_items = context.get(monitor_node)
            if node_items is None:
                note = (f"monitor node '{monitor_node}' not found this run — "
                        f"check the name matches the node exactly")
                return [{"json": {**self._safe_json(it), "compaction": note}}
                       for it in (items or [{"json": {}}])]
            # the AI node runs once per item, so sum every call it made
            for it in node_items:
                j = self._safe_json(it)
                tok = j.get("tokens_used")
                if isinstance(tok, (int, float)):
                    used += tok
        else:
            # Fall back to global token count
            used = ai_helper.tokens_used()
        
        if used <= threshold:
            src = f"'{monitor_node}'" if monitor_node else "this run"
            note = f"not yet — {used}/{threshold} tokens used by {src}"
            return [{"json": {**self._safe_json(it), "compaction": note}}
                   for it in (items or [{"json": {}}])]

        cred = self._credential()
        if cred is None:
            note = "compact skipped: no valid AI credential"
            return [{"json": {**self._safe_json(it), "compaction": note}}
                   for it in (items or [{"json": {}}])]

        current = storage.memory_all(bank)   # {key: value, ...}, live entries only
        if not current:
            note = "nothing to compact — bank is empty"
            return [{"json": {**self._safe_json(it), "compaction": note}}
                   for it in (items or [{"json": {}}])]

        blob = "\n\n".join(f"[{k}]\n{v}" for k, v in current.items())

        provider = self.p("provider", "generic")
        preset = self._PROVIDER_DEFAULTS.get(provider)
        chosen_model = (self.p("model") or "").strip()

        if preset:
            # deepseek/openrouter have exactly one correct endpoint each
            call_base_url = preset["base_url"]
            call_model = chosen_model or cred.get("model") or preset["model"]
            call_headers = preset["extra_headers"]
        else:
            # generic: no provider-specific defaults apply
            call_base_url = cred.get("base_url") or "https://api.openai.com/v1"
            call_model = chosen_model or cred.get("model") or "gpt-4o-mini"
            call_headers = None

        try:
            summary, spent = ai_helper.chat(
                cred["api_key"], prompt=blob,
                system=self.p("system_prompt", ""),
                model=call_model, base_url=call_base_url,
                extra_headers=call_headers)
        except Exception as e:
            error_msg = str(e)
            # Provide better error messages for common issues
            if "not found" in error_msg.lower() or "unknown model" in error_msg.lower():
                note = f"Model '{call_model}' not found on {provider}. Check model name."
            elif "invalid api key" in error_msg.lower() or "unauthorized" in error_msg.lower():
                note = f"API key invalid for {provider}. Check credential."
            elif "insufficient tokens" in error_msg.lower() or "not enough tokens" in error_msg.lower():
                note = f"Not enough tokens in run to compact. Increase token_threshold."
            else:
                note = f"Compact failed: {error_msg[:100]}"
            
            return [{"json": {**self._safe_json(it), "compaction": note}}
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
            j = self._safe_json(it)
            j["compaction"] = note
            j["summary"] = summary
            j["tokens_used"] = spent
            out.append({"json": j})
        return out
