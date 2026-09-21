"""
OpenRouter AI node — sends a prompt to any model via OpenRouter API 
and appends the reply back onto the item. Now with structured output.
"""
import json
import urllib.request
import urllib.error
import re
from node_base import Node

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


def _parse_variables(raw):
    """The output variables, from whichever form they were written in.

    Written in the node, one per line, optionally with an instruction:

        reply
        todo: a task to add, as JSON, or leave empty
        timer: when the timer should end, or leave empty

    The older forms still work, so saved workflows are untouched:
        ["reply", "todo"]
        [{"name": "reply"}, {"name": "todo"}]

    Returns [{"name": ..., "instruction": ...}, ...]. Names are made safe to
    use in {{ $json.name }} (spaces become _) and duplicates are dropped.
    """
    if not raw:
        return []
    data = raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        data = s
        if s.startswith("["):
            try:
                data = json.loads(s)
            except json.JSONDecodeError:
                data = s

    pairs = []
    if isinstance(data, str):
        for line in data.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, _, instr = line.partition(":")
            pairs.append((name, instr))
    elif isinstance(data, list):
        for v in data:
            if isinstance(v, dict):
                pairs.append((str(v.get("name") or ""), str(v.get("instruction") or "")))
            elif isinstance(v, str):
                pairs.append((v, ""))

    out, seen = [], set()
    for name, instr in pairs:
        name = re.sub(r"\s+", "_", name.strip())
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "instruction": instr.strip()})
    return out


def _build_json_schema(variables):
    """Example JSON output from variables."""
    return {v["name"]: "value" for v in variables}


# One [fieldname] instruction per LINE. [ \t] rather than \s on purpose: \s
# also matches newlines, which is how an instruction used to run on across
# the rest of the prompt.
_FIELD_LINE = re.compile(r"^[ \t]*\[(\w+)\][ \t]*(.*)$", re.MULTILINE)


def _extract_field_instructions(system_prompt):
    """[fieldname] instruction lines in the system prompt -- one line each.

    This used to run each instruction on until the next [field] or the end
    of the prompt, so the LAST field swallowed everything written after it --
    and that whole chunk was then cut out of the prompt, taking the
    assistant's actual personality with it. Now an instruction is exactly
    the rest of its own line.
    """
    return {m.group(1): m.group(2).strip()
            for m in _FIELD_LINE.finditer(system_prompt or "")}


def _enhance_system_prompt(system_prompt, variables, field_instructions):
    """The final system prompt: your prompt, then the list of fields to fill."""
    if not variables:
        return system_prompt
    names = {v["name"] for v in variables}
    lines = []
    for v in variables:
        # an instruction written next to the variable wins, then a [field]
        # line in the prompt, then a generic nudge
        instr = (v.get("instruction") or field_instructions.get(v["name"])
                 or "fill this in appropriately")
        lines.append(f"  - {v['name']}: {instr}")
    schema_str = json.dumps(_build_json_schema(variables), indent=2)
    enforce = ("Fill in every one of these fields:\n" + "\n".join(lines) +
               "\n\nRespond with ONLY a single valid JSON object with exactly "
               "these keys -- no explanation, no markdown fences, no extra "
               "text:\n\n" + schema_str)
    # remove only the [field] lines for fields that were actually declared;
    # any other bracketed line is part of your prompt and stays put
    cleaned = _FIELD_LINE.sub(
        lambda m: "" if m.group(1) in names else m.group(0), system_prompt or "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return (cleaned + "\n\n" + enforce) if cleaned else enforce


def _validate_response(parsed_json, variables):
    """Did the reply fill in every declared field?"""
    if not isinstance(parsed_json, dict):
        return False, [v["name"] for v in variables]
    missing = [v["name"] for v in variables if v["name"] not in parsed_json]
    return len(missing) == 0, missing


class OpenRouterAINode(Node):
    TYPE = "ai.openrouter"
    TITLE = "OpenRouter AI"
    CATEGORY = "ai"
    INPUTS = 1
    OUTPUTS = 1
    PARAMS = [
        {"key": "credential", "label": "Credential (saved token)", "type": "select",
         "default": "", "options_from": "credentials"},
        {"key": "api_key", "label": "or paste OpenRouter API Key", "type": "text", "default": ""},
        {"key": "model", "label": "Model Slug", "type": "text",
         "default": "meta-llama/llama-3.3-70b-instruct"},
        {"key": "input", "label": "Input (supports {{ }})", "type": "multiline",
         "default": "{{ $json }}"},
        {"key": "system_prompt", "label": "System prompt (optional)", "type": "multiline", "default": ""},
        {"key": "output_shape", "label": "Output JSON example (optional)", "type": "multiline",
         "default": "",
         "desc": "Only used when Structured output is OFF. Must be valid JSON."},
        {"key": "structured", "label": "Structured output", "type": "bool", "default": False,
         "desc": "On: the AI fills in the fields listed below, and each one "
                 "lands on the item as its own value ({{ $json.todo }} etc)."},
        {"key": "output_variables", "label": "Output variables (one per line)",
         "type": "multiline", "default": "",
         "desc": "One field per line. Add ': instruction' to tell the AI "
                 "what goes in it. Every field becomes its own value on the "
                 "item, so later nodes can each use a different one.",
         "example": "reply: your spoken reply\ntodo: a task as JSON, or empty\n"
                    "timer: when the timer ends, or empty",
         "show_if": {"structured": True}},
        {"key": "max_tokens", "label": "Max tokens", "type": "number", "default": 1024},
    ]

    def _resolve_key(self):
        cred = (self.params.get("credential") or "").strip()
        if cred:
            if cred.startswith("sk-or-") or cred.startswith("sk-") or len(cred) > 40:
                return cred
            try:
                from storage import load_credential
                data = load_credential(cred)
                tok = (data.get("token") or data.get("api_key") or "").strip()
                if tok:
                    return tok
            except Exception:
                pass
        return (self.params.get("api_key") or "").strip()

    def run(self, items):
        api_key = self._resolve_key()
        model = self.params.get("model", "meta-llama/llama-3.3-70b-instruct").strip()
        system_prompt = self.params.get("system_prompt", "") or ""
        input_tpl = self.params.get("input", "") or ""
        shape_raw = (self.params.get("output_shape") or "").strip()
        structured_mode = bool(self.params.get("structured", False))
        output_variables_str = self.params.get("output_variables", "") or ""
        try:
            max_tokens = int(self.params.get("max_tokens", 1024) or 1024)
        except (TypeError, ValueError):
            max_tokens = 1024

        if not api_key:
            return [{"json": {"error": "OpenRouter node: no API key provided"}}]

        shape_obj = None
        variables = []
        if structured_mode:
            variables = _parse_variables(output_variables_str)
            if variables:
                shape_obj = _build_json_schema(variables)
            else:
                structured_mode = False
        elif shape_raw:
            try:
                shape_obj = json.loads(shape_raw)
            except json.JSONDecodeError as e:
                return [{"json": {"error": f"OpenRouter node: output shape is not valid JSON ({e})"}}]

        out = []
        for item in items:
            j = item.get("json", {})

            user_msg = self.rexpr(input_tpl, j) if input_tpl else ""
            if not isinstance(user_msg, str):
                user_msg = json.dumps(user_msg)
            sys_msg = self.rexpr(system_prompt, j) if system_prompt else ""
            if not isinstance(sys_msg, str):
                sys_msg = json.dumps(sys_msg)

            user_msg = user_msg.strip()
            sys_msg = sys_msg.strip()

            if structured_mode and shape_obj:
                field_instructions = _extract_field_instructions(sys_msg)
                sys_msg = _enhance_system_prompt(sys_msg, variables, field_instructions)
            elif shape_obj is not None:
                shape_str = json.dumps(shape_obj, indent=2)
                enforce = (
                    "You must respond with ONLY a single valid JSON object that "
                    "matches exactly this shape (same keys), and nothing else — "
                    "no explanation, no markdown code fences:\n" + shape_str
                )
                sys_msg = (sys_msg + "\n\n" + enforce).strip() if sys_msg else enforce

            if not user_msg:
                out.append({"json": {**j, "error": "OpenRouter node: input is empty, nothing sent"}})
                continue

            messages = []
            if sys_msg:
                messages.append({"role": "system", "content": sys_msg})
            messages.append({"role": "user", "content": user_msg})

            body = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": messages,
                "stream": False,
            }
            if shape_obj is not None:
                body["response_format"] = {"type": "json_object"}

            try:
                reply_text, tokens_used = self._call(api_key, body)
            except Exception as e:
                out.append({"json": {**j, "error": f"OpenRouter node request failed: {e}"}})
                continue

            if shape_obj is None:
                out.append({"json": {**j, "reply": reply_text, "tokens_used": tokens_used}})
                continue

            parsed = self._parse_json(reply_text)
            # only an OBJECT can be merged onto the item -- a model answering
            # with a bare list or string used to crash the merge with
            # "'list' object is not a mapping"
            if not isinstance(parsed, dict):
                out.append({"json": {**j, "error": "OpenRouter node: reply was not a JSON object",
                                     "raw_reply": reply_text, "tokens_used": tokens_used}})
                continue

            new = {**j, **parsed, "tokens_used": tokens_used}
            if structured_mode:
                ok, missing = _validate_response(parsed, variables)
                if not ok:
                    # keep everything the AI DID fill in, rather than throwing
                    # the whole reply away over one empty field. The missing
                    # ones stay absent, so an IF node's "if the field is
                    # missing" setting decides what happens to them.
                    new["missing_fields"] = missing
            out.append({"json": new})

        return out

    def _call(self, api_key, body):
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            OPENROUTER_API_URL, data=data, method="POST",
            headers={
                "content-type": "application/json",
                "authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://github.com/KeathMel/DuGS_LINUX",
                "X-Title": "DuGS Workflow Engine",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")
            raise RuntimeError(f"HTTP {e.code}: {detail[:300]}")

        usage = payload.get("usage", {})
        tokens_used = usage.get("total_tokens", 0)

        choices = payload.get("choices", [])
        if choices:
            msg = choices[0].get("message", {})
            return (msg.get("content") or "").strip(), tokens_used
        return "", tokens_used

    def _parse_json(self, text):
        if not text:
            return None
        t = text.strip()
        if t.startswith("```"):
            parts = t.split("```")
            t = parts[1] if len(parts) > 1 else text
            if t.startswith("json"):
                t = t[4:]
            t = t.strip().rstrip("`").strip()
        try:
            return json.loads(t)
        except json.JSONDecodeError:
            start = t.find("{"); end = t.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(t[start:end + 1])
                except json.JSONDecodeError:
                    return None
            return None
