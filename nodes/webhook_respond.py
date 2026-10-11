"""
Respond to Webhook: sends an HTTP response back to whoever triggered the
workflow via a Webhook Trigger node, then the workflow stops.

SENDING A FILE BACK
===================
Set Body to `file` and point "File to send" at a path -- typically the wav
a Text to Speech node just wrote:

    {{ $json.audio_path }}

The reply is then the FILE ITSELF: real bytes, the right Content-Type, no
JSON wrapper and no base64. A client that plays whatever comes back just
plays it.

The node does not read the file; it hands the server the path and the
server streams it. That matters -- a few hundred KB of audio never enters
the item stream, so run logs stay readable instead of filling with binary,
and memory does not balloon on a long reply.
"""
import mimetypes
import os

from node_base import Node, resolve_expr

# the ones mimetypes gets wrong or does not know
_CTYPE = {
    ".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg",
    ".opus": "audio/ogg", ".flac": "audio/flac", ".m4a": "audio/mp4",
    ".aac": "audio/aac", ".webm": "audio/webm",
}


def guess_content_type(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in _CTYPE:
        return _CTYPE[ext]
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


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
            "options": ["pass through item", "custom", "file"],
            "desc": "pass through item = send the item as JSON. "
                    "custom = send the body you write below. "
                    "file = send a FILE back as the reply -- real bytes with "
                    "the right Content-Type, no JSON and no base64.",
        },

        {"key": "file_path", "label": "File to send", "type": "text",
         "default": "{{ $json.audio_path }}",
         "desc": "Which file becomes the reply. Normally the wav a Text to "
                 "Speech node just wrote. ~ works, expressions allowed.",
         "example": "{{ $json.audio_path }}",
         "show_if": {"body_mode": "file"}},

        {"key": "content_type", "label": "Content-Type", "type": "text",
         "default": "",
         "desc": "Leave blank to work it out from the file extension "
                 "(.wav -> audio/wav). Only set it if your client insists "
                 "on something specific.",
         "example": "audio/wav",
         "show_if": {"body_mode": "file"}},
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

        if mode == "file":
            path = self.rexpr(self.params.get("file_path", ""), j)
            path = os.path.expanduser(os.path.expandvars(str(path or "").strip()))
            problem = None
            if not path:
                problem = ("Respond to Webhook is set to send a file, but "
                           "'File to send' is empty -- point it at something "
                           "like {{ $json.audio_path }}")
            elif not os.path.isfile(path):
                problem = f"Respond to Webhook: no file at {path}"

            if problem:
                # Every other node can afford to just raise and let the error
                # ride downstream on the item. This one cannot: its entire
                # job is to answer the caller, and raising here means the
                # caller gets the generic "no Respond node was hit" ack and
                # no idea why. So it answers, with the reason.
                if self.params.get("is_test_run"):
                    raise WebhookRespondSignal(500, {"ok": False, "error": problem})
                raise ValueError(problem)

            ctype = str(self.rexpr(self.params.get("content_type", ""), j)
                        or "").strip() or guess_content_type(path)
            # the PATH travels, not the bytes -- the server reads the file
            body = {"__file__": path, "__content_type__": ctype,
                    "__bytes__": os.path.getsize(path)}
        elif mode == "custom":
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
