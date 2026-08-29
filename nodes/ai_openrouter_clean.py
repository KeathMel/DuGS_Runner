

"""
OpenRouter AI node — sends a prompt to any model via OpenRouter API 
and appends the reply back onto the item.
"""
import json
import urllib.request
import urllib.error
from node_base import Node

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


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
         "default": ""},
        {"key": "max_tokens", "label": "Max tokens", "type": "number", "default": 1024},
    ]

    def _resolve_key(self):
        """Prefer a saved credential; fall back to a pasted key."""
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
        try:
            max_tokens = int(self.params.get("max_tokens", 1024) or 1024)
        except (TypeError, ValueError):
            max_tokens = 1024

        if not api_key:
            return [{"json": {"error": "OpenRouter node: no API key provided"}}]

        shape_obj = None
        if shape_raw:
            try:
                shape_obj = json.loads(shape_raw)
            except json.JSONDecodeError as e:
                return [{"json": {"error": f"OpenRouter node: output shape is not valid JSON ({e})"}}]

        out = []
        for item in items:
            j = item.get("json", {})

            # resolve {{ }} expressions
            user_msg = self.rexpr(input_tpl, j) if input_tpl else ""
            if not isinstance(user_msg, str):
                user_msg = json.dumps(user_msg)
            sys_msg = self.rexpr(system_prompt, j) if system_prompt else ""
            if not isinstance(sys_msg, str):
                sys_msg = json.dumps(sys_msg)

            user_msg = user_msg.strip()
            sys_msg = sys_msg.strip()

            if shape_obj is not None:
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
                out.append({"json": {**j, "error": f"OpenRouter request failed: {e}"}})
                continue

            if shape_obj is not None:
                parsed = self._parse_json(reply_text)
                if parsed is None:
                    out.append({"json": {**j, "error": "OpenRouter node: reply was not valid JSON",
                                          "raw_reply": reply_text, "tokens_used": tokens_used}})
                else:
                    out.append({"json": {**j, **parsed, "tokens_used": tokens_used}})
            else:
                out.append({"json": {**j, "reply": reply_text, "tokens_used": tokens_used}})

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

        choices = payload.get("choices", [])
        reply = ""
        if choices:
            msg = choices[0].get("message", {})
            reply = (msg.get("content") or "").strip()
        
        # Extract token usage from API response
        usage = payload.get("usage", {})
        tokens_used = usage.get("total_tokens", 0)
        
        return reply, tokens_used

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
