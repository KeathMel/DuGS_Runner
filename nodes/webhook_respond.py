"""
Respond to Webhook: sends an HTTP response back to whoever triggered the
workflow via a Webhook Trigger node, then the workflow stops.
"""
from node_base import Node, resolve_expr


class WebhookRespondSignal(Exception):
    def __init__(self, status, body):
        self.status = status
        self.body = body
        super().__init__(f"webhook respond: {status}")


class WebhookRespond(Node):
    TYPE = "webhook.respond"
    TITLE = "Respond to Webhook"
    CATEGORY = "action"
    INPUTS = 1
    OUTPUTS = 1
    PARAMS = [
        {"key": "status", "label": "Status code", "type": "number", "default": 200},
        {
            "key": "body_mode",
            "label": "Body",
            "type": "select",
            "default": "pass through item",
            "options": ["pass through item", "custom"],
        },
        {
            "key": "custom_body",
            "label": "Custom body (JSON, {{ $json.x }} allowed)",
            "type": "json",
            "default": {"ok": True},
        },
        {
            "key": "is_test_run",
            "label": "(internal) treat as real webhook response",
            "type": "bool",
            "default": False,
        },
    ]

    def run(self, items):
        status = int(self.params.get("status", 200) or 200)
        mode = self.params.get("body_mode", "pass through item")
        item = items[0] if items else {"json": {}}
        j = item.get("json", {})

        if mode == "custom":
            raw = self.params.get("custom_body", {})
            body = self._resolve_deep(raw, j)
        else:
            body = j

        if self.params.get("is_test_run"):
            raise WebhookRespondSignal(status, body)

        return [{"json": body}]

    def _resolve_deep(self, val, j):
        if isinstance(val, str):
            # FIXED: Use self.rexpr so cross-node references like $('Node Name') resolve!
            # Fall back to resolve_expr with self._context if rexpr isn't available.
            if hasattr(self, "rexpr"):
                return self.rexpr(val, j)
            context = getattr(self, "_context", {})
            return resolve_expr(val, j, context=context)
        if isinstance(val, dict):
            return {k: self._resolve_deep(v, j) for k, v in val.items()}
        if isinstance(val, list):
            return [self._resolve_deep(v, j) for v in val]
        return val
