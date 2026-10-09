"""
Speech to Text — send an audio file to your own Whisper server, get words back.

This is the "ears" node. A voice note comes in, text comes out, and every
node you already have (AI Agent, Keyword Match, Switch, Memory) works on it
exactly as if someone had typed it.

    voice.ogg  ->  [ Speech to Text ]  ->  { "text": "remind me at six" }

WHY THIS AND NOT THE HTTP NODE
==============================
The HTTP Request node sends JSON. Audio isn't JSON -- it's raw bytes that
have to go up as a multipart/form-data upload, the same way a browser posts
a file. That's the entire reason this node exists. Everything else it does
(picking the file up off disk, off a URL, or out of a base64 field, and
refusing anything silly-sized) is just the housekeeping that comes with
files instead of text.

WHICH SERVER
============
Nothing here is tied to one project, because they all speak the same shape:
a POST with the file attached under some field name. The defaults match the
OpenAI-compatible endpoint that most self-hosted Whisper builds expose
(faster-whisper-server, Speaches, whisper.cpp's server, LocalAI):

    Endpoint     http://localhost:8000/v1/audio/transcriptions
    File field   file
    Model        whisper-1   (or base / small / medium / large-v3)

If you end up running ahmetoner's whisper-asr-webservice instead, the only
two things to change are:

    Endpoint     http://localhost:9000/asr
    File field   audio_file

and it works. Anything else that server wants goes in Extra form fields.
Pure stdlib -- no pip install, so it runs on the phone build too.

SETTINGS
========
endpoint     : where your Whisper server listens
source       : is the audio a file on disk, a URL, or base64 in the item
audio        : the path / URL / field holding it
file_field   : the multipart field name the server expects
model        : which Whisper model to ask for
language     : force a language, or leave blank to auto-detect
prompt       : a hint for names and jargon Whisper keeps getting wrong
task         : transcribe (same language) or translate (into English)
extra_fields : anything else your server takes
auth         : Authorization header value, if you put one in front of it
max_mb       : refuse anything bigger, so a stray path can't eat the RAM
timeout      : long files take a while -- this is not a web page
output_field : which field the words land in
keep_item    : keep the incoming fields alongside the text
on_error     : error | empty | skip
"""
import base64
import json as _json
import mimetypes
import os
import urllib.error
import urllib.request
import uuid

from node_base import Node

_TEXT_KEYS = ("text", "transcription", "transcript", "result")


def _guess_type(filename):
    guessed, _ = mimetypes.guess_type(filename)
    if guessed:
        return guessed
    # mimetypes misses the ones voice notes actually arrive as
    ext = os.path.splitext(filename)[1].lower()
    return {
        ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg",
        ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".aac": "audio/aac",
        ".wav": "audio/wav", ".mp3": "audio/mpeg", ".flac": "audio/flac",
        ".webm": "audio/webm", ".amr": "audio/amr",
    }.get(ext, "application/octet-stream")


def _multipart(fields, file_field, filename, blob):
    """Build a multipart/form-data body the way a browser would."""
    boundary = "----DuGS" + uuid.uuid4().hex
    sep = ("--" + boundary + "\r\n").encode()
    out = bytearray()

    for key, value in fields.items():
        if value is None or value == "":
            continue
        out += sep
        out += ('Content-Disposition: form-data; name="%s"\r\n\r\n' % key).encode()
        out += str(value).encode("utf-8") + b"\r\n"

    out += sep
    out += ('Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
            % (file_field, os.path.basename(filename) or "audio.wav")).encode("utf-8")
    out += ("Content-Type: %s\r\n\r\n" % _guess_type(filename)).encode()
    out += blob + b"\r\n"
    out += ("--" + boundary + "--\r\n").encode()

    return bytes(out), "multipart/form-data; boundary=" + boundary


def _pick_text(payload):
    """Servers disagree on what they call the words. Find them anyway."""
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, dict):
        for key in _TEXT_KEYS:
            val = payload.get(key)
            if isinstance(val, str):
                return val.strip()
        # verbose_json: stitch the segments back together
        segs = payload.get("segments")
        if isinstance(segs, list):
            parts = [s.get("text", "") for s in segs if isinstance(s, dict)]
            if parts:
                return "".join(parts).strip()
    return ""


class SpeechToTextNode(Node):
    TYPE = "audio.stt"
    TITLE = "Speech to Text"
    CATEGORY = "ai"
    INPUTS = 1
    OUTPUTS = 1
    PARAMS = [
        {"key": "endpoint", "label": "Whisper endpoint", "type": "text",
         "default": "http://localhost:8000/v1/audio/transcriptions",
         "desc": "Where your own Whisper server listens. The default suits "
                 "the OpenAI-compatible builds; whisper-asr-webservice wants "
                 "http://localhost:9000/asr instead.",
         "example": "http://localhost:8000/v1/audio/transcriptions"},

        {"key": "source", "label": "The audio is", "type": "select",
         "default": "file path", "options": ["file path", "url", "base64 field"],
         "desc": "Where to get the audio from. 'base64 field' is for voice "
                 "notes that arrived through a webhook already encoded."},

        {"key": "audio", "label": "Audio", "type": "text",
         "default": "{{ $json.audio_path }}",
         "desc": "The path, URL, or field holding the audio. ~ works. "
                 "Expressions allowed.",
         "example": "{{ $json.audio_path }}"},

        {"key": "file_field", "label": "File field name", "type": "text",
         "default": "file",
         "desc": "The multipart field your server reads the upload from. "
                 "'file' for OpenAI-compatible servers, 'audio_file' for "
                 "whisper-asr-webservice."},

        {"key": "model", "label": "Model", "type": "text", "default": "whisper-1",
         "desc": "Which model to ask for. Leave blank if your server only "
                 "has the one loaded and doesn't want to be told.",
         "example": "large-v3"},

        {"key": "language", "label": "Language", "type": "text", "default": "",
         "desc": "Two-letter code to force a language. Blank auto-detects, "
                 "which is usually right but costs a moment.",
         "example": "de"},

        {"key": "prompt", "label": "Hint", "type": "multiline", "default": "",
         "desc": "Names and jargon Whisper keeps mangling. Feeding it the "
                 "spellings up front fixes most of them."},

        {"key": "task", "label": "Task", "type": "select",
         "default": "transcribe", "options": ["transcribe", "translate"],
         "desc": "transcribe = words in the language that was spoken. "
                 "translate = Whisper renders them into English."},

        {"key": "extra_fields", "label": "Extra form fields", "type": "kv_dict",
         "default": {}, "name_hint": "field", "value_hint": "value",
         "desc": "Anything else your server takes, like temperature or "
                 "response_format."},

        {"key": "auth", "label": "Authorization header", "type": "text",
         "default": "",
         "desc": "Only if you put something in front of your Whisper server. "
                 "Local-only? Leave it blank.",
         "example": "Bearer my-token"},

        {"key": "max_mb", "label": "Max size (MB)", "type": "number", "default": 25,
         "desc": "Refuse anything bigger. Audio is large, and a wrong path "
                 "shouldn't be able to eat all the memory."},

        {"key": "timeout", "label": "Timeout (s)", "type": "number", "default": 300,
         "desc": "Transcribing is slow -- minutes, not seconds, on CPU. "
                 "Give it room."},

        {"key": "output_field", "label": "Output field", "type": "text",
         "default": "text",
         "desc": "Which field the words land in, so the next node can use "
                 "{{ $json.text }}."},

        {"key": "keep_item", "label": "Keep the incoming fields", "type": "bool",
         "default": True,
         "desc": "On: the text is added to the item you already had. "
                 "Off: the item is replaced by just the transcription."},

        {"key": "on_error", "label": "If it fails", "type": "select",
         "default": "error", "options": ["error", "empty", "skip"],
         "desc": "error = put the reason on the item. empty = carry on with "
                 "an empty string. skip = drop the item entirely."},
    ]

    # ------------------------------------------------------------------
    def _load_audio(self, source, raw, max_bytes, timeout):
        """Return (bytes, a filename to label it with)."""
        if source == "base64 field":
            data = raw
            if "," in data[:120] and data.lstrip().startswith("data:"):
                data = data.split(",", 1)[1]        # strip a data: URI prefix
            blob = base64.b64decode(data)
            name = "audio.wav"
        elif source == "url":
            req = urllib.request.Request(raw, headers={"User-Agent": "DuGS"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                blob = resp.read(max_bytes + 1)
            name = os.path.basename(raw.split("?")[0]) or "audio.wav"
        else:
            path = os.path.expanduser(os.path.expandvars(raw))
            if not os.path.isfile(path):
                raise ValueError("no audio file at %s" % path)
            size = os.path.getsize(path)
            if size > max_bytes:
                raise ValueError("audio is %.1f MB, over the %.0f MB limit"
                                 % (size / 1048576.0, max_bytes / 1048576.0))
            with open(path, "rb") as fh:
                blob = fh.read()
            name = os.path.basename(path)

        if len(blob) > max_bytes:
            raise ValueError("audio is %.1f MB, over the %.0f MB limit"
                             % (len(blob) / 1048576.0, max_bytes / 1048576.0))
        if not blob:
            raise ValueError("the audio is empty")
        return blob, name

    def run(self, items):
        endpoint = str(self.p("endpoint", "")).strip()
        if not endpoint:
            raise ValueError("Speech to Text needs an endpoint")

        source = self.p("source", "file path")
        file_field = str(self.p("file_field", "file")).strip() or "file"
        out_field = str(self.p("output_field", "text")).strip() or "text"
        keep_item = bool(self.p("keep_item", True))
        on_error = self.p("on_error", "error")
        auth = str(self.p("auth", "")).strip()

        try:
            timeout = float(self.p("timeout", 300))
        except (TypeError, ValueError):
            timeout = 300.0
        try:
            max_bytes = int(float(self.p("max_mb", 25)) * 1048576)
        except (TypeError, ValueError):
            max_bytes = 25 * 1048576

        extra = self.p("extra_fields", {}) or {}
        if isinstance(extra, str):
            try:
                extra = _json.loads(extra)
            except Exception:
                extra = {}

        out = []
        for item in (items or [{"json": {}}]):
            j = dict(item.get("json", {})) if isinstance(item.get("json"), dict) else {}
            base = j if keep_item else {}

            try:
                raw = self.rexpr(self.p("audio", ""), j)
                raw = "" if raw is None else str(raw).strip()
                if not raw:
                    raise ValueError("no audio given -- is the Audio field "
                                     "pointing at the right item field?")

                blob, filename = self._load_audio(source, raw, max_bytes, timeout)

                fields = {"model": self.rexpr(self.p("model", ""), j),
                          "language": self.rexpr(self.p("language", ""), j),
                          "prompt": self.rexpr(self.p("prompt", ""), j),
                          "task": self.p("task", "transcribe")}
                for key, val in extra.items():
                    fields[key] = self.rexpr(val, j)

                body, content_type = _multipart(fields, file_field, filename, blob)

                headers = {"Content-Type": content_type,
                           "Content-Length": str(len(body))}
                if auth:
                    headers["Authorization"] = auth

                req = urllib.request.Request(
                    self.rexpr(endpoint, j), data=body, method="POST", headers=headers)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    reply = resp.read().decode("utf-8", errors="replace")

                try:
                    payload = _json.loads(reply)
                except ValueError:
                    payload = reply                 # response_format=text

                text = _pick_text(payload)
                if not text:
                    raise ValueError("the server replied, but there were no "
                                     "words in it: %s" % reply[:200])

                base[out_field] = text
                base["audio_file"] = filename
                base["audio_bytes"] = len(blob)
                if isinstance(payload, dict) and payload.get("language"):
                    base["detected_language"] = payload["language"]
                if isinstance(payload, dict) and payload.get("duration"):
                    base["audio_duration"] = payload["duration"]
                out.append({"json": base})

            except Exception as e:
                detail = str(e)
                if isinstance(e, urllib.error.HTTPError):
                    try:
                        detail += " -- " + e.read().decode("utf-8", "replace")[:300]
                    except Exception:
                        pass
                elif isinstance(e, urllib.error.URLError):
                    detail = ("couldn't reach %s (%s) -- is the Whisper server "
                              "running?" % (endpoint, e.reason))
                if on_error == "skip":
                    continue
                if on_error == "empty":
                    base[out_field] = ""
                    base["stt_error"] = detail
                else:
                    base["error"] = "Speech to Text: " + detail
                out.append({"json": base})

        return out
