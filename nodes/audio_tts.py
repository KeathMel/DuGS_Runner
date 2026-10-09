"""
Text to Speech — send words to your own Piper server, get a voice file back.

This is the "mouth" node, and the mirror of Speech to Text. Whatever the AI
Agent wrote lands in a real audio file on disk, and the path goes into the
item so File nodes, Telegram, Discord or Home Assistant can pick it up.

    { "response": "alarm set for six" }  ->  [ Text to Speech ]
                                         ->  { "audio_path": "~/dugs_audio/speech_1738.wav" }

WHY THIS AND NOT THE HTTP NODE
==============================
The HTTP node decodes every reply as text. A WAV file is not text -- run it
through there and you get mojibake. This node keeps the reply as bytes and
writes them straight to disk, which is the whole job.

WHICH SERVER
============
Three request shapes cover everything people actually self-host, so pick the
one that matches and the rest of the settings stay put:

  raw text    Piper's own http_server. The words go up as the entire request
              body, WAV comes back.
                  Endpoint  http://localhost:5000/
                  python3 -m piper.http_server -m en_US-amy-medium

  openai json {"model","input","voice","response_format"} -- what Speaches,
              openedai-speech, LocalAI and Kokoro expose.
                  Endpoint  http://localhost:8000/v1/audio/speech
                  Voice     en_US-amy-medium

  form        text=... as a form post, for the odd wrapper that wants it.

WHERE THE FILE GOES
===================
Save folder plus Filename. Leave the filename blank and it gets a timestamp,
so a hundred runs give you a hundred files instead of one being overwritten
a hundred times. Turn on 'Also include base64' when the audio is going
straight out through a chat API rather than being read off disk.

SETTINGS
========
endpoint       : where your Piper server listens
text           : the words to speak
body_mode      : raw text | openai json | form
voice          : which voice, for the servers that take one
model          : model name, for the servers that take one
audio_format   : wav | mp3 | opus | flac  (your server has to support it)
extra_fields   : anything else your server takes
auth           : Authorization header value, if you put one in front of it
out_dir        : which folder the file is written to
filename       : what to call it; blank = timestamped
output_field   : which field the path lands in
include_base64 : also put the audio in the item, for chat APIs
max_chars      : refuse a wall of text, so one runaway item can't lock it up
timeout        : synthesis takes a moment
on_error       : error | skip
"""
import base64
import json as _json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from node_base import Node

_EXT = {"wav": ".wav", "mp3": ".mp3", "opus": ".opus", "ogg": ".ogg",
        "flac": ".flac", "aac": ".aac", "pcm": ".pcm"}


def _safe_name(name):
    """A filename that can't climb out of the folder it was aimed at."""
    name = os.path.basename(str(name or "").strip())
    keep = "-_. ()"
    cleaned = "".join(c for c in name if c.isalnum() or c in keep).strip()
    return cleaned


class TextToSpeechNode(Node):
    TYPE = "audio.tts"
    TITLE = "Text to Speech"
    CATEGORY = "ai"
    INPUTS = 1
    OUTPUTS = 1
    PARAMS = [
        {"key": "endpoint", "label": "Piper endpoint", "type": "text",
         "default": "http://localhost:5000/",
         "desc": "Where your own Piper server listens. Piper's own "
                 "http_server is port 5000; the OpenAI-compatible wrappers "
                 "are usually http://localhost:8000/v1/audio/speech.",
         "example": "http://localhost:5000/"},

        {"key": "text", "label": "Text to speak", "type": "multiline",
         "default": "{{ $json.response }}",
         "desc": "The words to say. Point this at whatever field the AI "
                 "wrote into. Expressions allowed.",
         "example": "{{ $json.response }}"},

        {"key": "body_mode", "label": "Request shape", "type": "select",
         "default": "raw text", "options": ["raw text", "openai json", "form"],
         "desc": "raw text = Piper's own http_server (body is the words). "
                 "openai json = Speaches / openedai-speech / LocalAI. "
                 "form = text=... as a form post."},

        {"key": "voice", "label": "Voice", "type": "text", "default": "",
         "desc": "Which voice to use, for servers that take one. Piper's own "
                 "http_server is started with its voice already chosen, so "
                 "leave this blank for 'raw text'.",
         "example": "en_US-amy-medium"},

        {"key": "model", "label": "Model", "type": "text", "default": "",
         "desc": "Model name, for servers that want one. Blank is fine for "
                 "Piper itself.",
         "example": "tts-1"},

        {"key": "audio_format", "label": "Audio format", "type": "select",
         "default": "wav", "options": ["wav", "mp3", "opus", "ogg", "flac"],
         "desc": "What to ask the server for, and the extension the file "
                 "gets. Piper only does wav on its own -- the wrappers do "
                 "the rest."},

        {"key": "extra_fields", "label": "Extra fields", "type": "kv_dict",
         "default": {}, "name_hint": "field", "value_hint": "value",
         "desc": "Anything else your server takes, like speed or "
                 "length_scale. Added to the JSON or the form."},

        {"key": "auth", "label": "Authorization header", "type": "text",
         "default": "",
         "desc": "Only if you put something in front of your Piper server. "
                 "Local-only? Leave it blank.",
         "example": "Bearer my-token"},

        {"key": "out_dir", "label": "Save folder", "type": "text",
         "default": "~/dugs_audio",
         "desc": "Which folder the voice file is written to. Created if it "
                 "isn't there yet. ~ works.",
         "example": "~/dugs_audio"},

        {"key": "filename", "label": "Filename", "type": "text", "default": "",
         "desc": "What to call the file. Leave blank for a timestamped name, "
                 "so runs don't overwrite each other.",
         "example": "reply_{{ $json.chat_id }}.wav"},

        {"key": "output_field", "label": "Output field", "type": "text",
         "default": "audio_path",
         "desc": "Which field the saved path lands in, so the next node can "
                 "use {{ $json.audio_path }}."},

        {"key": "include_base64", "label": "Also include base64", "type": "bool",
         "default": False,
         "desc": "On: the audio also rides along in the item as "
                 "audio_base64. Handy when it's going straight out through "
                 "a chat API instead of being read off disk."},

        {"key": "max_chars", "label": "Max characters", "type": "number",
         "default": 5000,
         "desc": "Refuse a wall of text. Synthesis is roughly linear, so one "
                 "runaway item shouldn't be able to lock the server up."},

        {"key": "timeout", "label": "Timeout (s)", "type": "number", "default": 120,
         "desc": "Synthesis takes a moment, more on CPU and for long text."},

        {"key": "on_error", "label": "If it fails", "type": "select",
         "default": "error", "options": ["error", "skip"],
         "desc": "error = put the reason on the item and carry on. "
                 "skip = drop the item entirely."},
    ]

    # ------------------------------------------------------------------
    def _request(self, endpoint, text, fields, mode, auth, fmt):
        """Build the POST the chosen server shape expects."""
        headers = {}
        if auth:
            headers["Authorization"] = auth

        if mode == "openai json":
            body = {"input": text}
            if fields.get("model"):
                body["model"] = fields["model"]
            if fields.get("voice"):
                body["voice"] = fields["voice"]
            if fmt:
                body["response_format"] = fmt
            for key, val in fields.get("extra", {}).items():
                body[key] = val
            data = _json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        elif mode == "form":
            form = {"text": text}
            for key in ("voice", "model"):
                if fields.get(key):
                    form[key] = fields[key]
            form.update(fields.get("extra", {}))
            data = urllib.parse.urlencode(form).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        else:  # raw text -- Piper's own http_server
            data = text.encode("utf-8")
            headers["Content-Type"] = "text/plain; charset=utf-8"
            query = {k: v for k, v in fields.get("extra", {}).items() if v != ""}
            if fields.get("voice"):
                query.setdefault("voice", fields["voice"])
            if query:
                joiner = "&" if "?" in endpoint else "?"
                endpoint = endpoint + joiner + urllib.parse.urlencode(query)

        headers["Content-Length"] = str(len(data))
        return urllib.request.Request(endpoint, data=data, method="POST",
                                      headers=headers)

    def run(self, items):
        endpoint_raw = str(self.p("endpoint", "")).strip()
        if not endpoint_raw:
            raise ValueError("Text to Speech needs an endpoint")

        mode = self.p("body_mode", "raw text")
        fmt = str(self.p("audio_format", "wav")).lower()
        out_field = str(self.p("output_field", "audio_path")).strip() or "audio_path"
        on_error = self.p("on_error", "error")
        auth = str(self.p("auth", "")).strip()
        want_b64 = bool(self.p("include_base64", False))

        try:
            timeout = float(self.p("timeout", 120))
        except (TypeError, ValueError):
            timeout = 120.0
        try:
            max_chars = int(self.p("max_chars", 5000))
        except (TypeError, ValueError):
            max_chars = 5000

        extra = self.p("extra_fields", {}) or {}
        if isinstance(extra, str):
            try:
                extra = _json.loads(extra)
            except Exception:
                extra = {}

        folder = os.path.expanduser(os.path.expandvars(
            str(self.p("out_dir", "~/dugs_audio")).strip() or "~/dugs_audio"))

        out = []
        for index, item in enumerate(items or [{"json": {}}]):
            j = dict(item.get("json", {})) if isinstance(item.get("json"), dict) else {}

            try:
                text = self.rexpr(self.p("text", ""), j)
                text = "" if text is None else str(text).strip()
                if not text:
                    raise ValueError("there's nothing to say -- is the Text "
                                     "field pointing at the right item field?")
                if max_chars > 0 and len(text) > max_chars:
                    raise ValueError("the text is %d characters, over the %d "
                                     "limit" % (len(text), max_chars))

                fields = {
                    "voice": self.rexpr(self.p("voice", ""), j),
                    "model": self.rexpr(self.p("model", ""), j),
                    "extra": {k: self.rexpr(v, j) for k, v in extra.items()},
                }

                req = self._request(self.rexpr(endpoint_raw, j), text, fields,
                                    mode, auth, fmt)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    blob = resp.read()
                    ctype = (resp.headers.get("Content-Type") or "").lower()

                if not blob:
                    raise ValueError("the server replied with no audio at all")
                # a JSON error page with a 200 on it is still an error
                if "json" in ctype or blob[:1] in (b"{", b"["):
                    raise ValueError("the server sent text, not audio: %s"
                                     % blob[:200].decode("utf-8", "replace"))

                name = _safe_name(self.rexpr(self.p("filename", ""), j))
                if not name:
                    name = "speech_%d_%d%s" % (int(time.time() * 1000), index,
                                               _EXT.get(fmt, ".wav"))
                elif not os.path.splitext(name)[1]:
                    name += _EXT.get(fmt, ".wav")

                os.makedirs(folder, exist_ok=True)
                path = os.path.join(folder, name)
                with open(path, "wb") as fh:
                    fh.write(blob)

                j[out_field] = path
                j["audio_bytes"] = len(blob)
                j["audio_format"] = fmt
                j["spoken_text"] = text
                if want_b64:
                    j["audio_base64"] = base64.b64encode(blob).decode("ascii")
                out.append({"json": j})

            except Exception as e:
                detail = str(e)
                if isinstance(e, urllib.error.HTTPError):
                    try:
                        detail += " -- " + e.read().decode("utf-8", "replace")[:300]
                    except Exception:
                        pass
                elif isinstance(e, urllib.error.URLError):
                    detail = ("couldn't reach %s (%s) -- is the Piper server "
                              "running?" % (endpoint_raw, e.reason))
                if on_error == "skip":
                    continue
                j["error"] = "Text to Speech: " + detail
                out.append({"json": j})

        return out
