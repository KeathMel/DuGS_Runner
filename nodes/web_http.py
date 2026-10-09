"""
HTTP Request node: call a web API and put the response into the item stream.
Pure stdlib (urllib) — no installs needed.

Supports {{ $json.field }} expressions in URL, headers, body and file paths.

Output per item:
  { "status": 200, "body": <parsed JSON or raw text>, "headers": {...}, "url": "..." }

Options:
  - response_field: if set, puts the body under this key on the EXISTING item
    (useful for enrichment: keep your data, add the API response)

FILES, IN AND OUT
=================
JSON is the common case and stays the default, but plenty of endpoints deal
in files -- a voice note going up to Whisper, a WAV coming back from Piper,
an image, a PDF. Two settings cover it:

  Send as      json body     the Body field, as JSON              (default)
               raw file      the file's bytes ARE the request body
               multipart     the file as a form upload, like a browser does

  Response     auto          text and JSON parse as before; anything binary
                             is written to disk and you get the path  (default)
               save to file  always write it to disk, whatever it is
               text          force the old behaviour, decode as text

'auto' only changes what used to happen for binary replies, and what used to
happen was mojibake -- a WAV decoded as if it were UTF-8. Now you get
`file_path` on the item and the bytes intact on disk.
"""
import json
import mimetypes
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from node_base import Node, resolve_expr

# Content types that are genuinely text. Anything else coming back gets
# treated as a file rather than decoded into a string.
_TEXT_HINTS = ("text/", "json", "xml", "javascript", "x-www-form-urlencoded",
               "yaml", "csv", "html")

_EXT_BY_TYPE = {
    "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/wave": ".wav",
    "audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/ogg": ".ogg",
    "audio/opus": ".opus", "audio/flac": ".flac", "audio/mp4": ".m4a",
    "audio/aac": ".aac", "audio/webm": ".webm",
    "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
    "image/webp": ".webp", "image/svg+xml": ".svg",
    "application/pdf": ".pdf", "application/zip": ".zip",
    "application/octet-stream": ".bin",
}


def _guess_type(filename):
    guessed, _ = mimetypes.guess_type(filename)
    if guessed:
        return guessed
    ext = os.path.splitext(filename)[1].lower()
    return {
        ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg",
        ".m4a": "audio/mp4", ".aac": "audio/aac", ".wav": "audio/wav",
        ".mp3": "audio/mpeg", ".flac": "audio/flac", ".webm": "audio/webm",
        ".amr": "audio/amr",
    }.get(ext, "application/octet-stream")


def _is_text(ctype):
    c = (ctype or "").lower()
    if not c:
        return True              # no Content-Type at all: assume text, as before
    return any(h in c for h in _TEXT_HINTS)


def _safe_name(name):
    """A filename that can't climb out of the folder it was aimed at."""
    name = os.path.basename(str(name or "").strip())
    keep = "-_. ()"
    return "".join(c for c in name if c.isalnum() or c in keep).strip()


def _multipart(fields, file_field, filename, blob):
    """Build a multipart/form-data body the way a browser would."""
    boundary = "----DuGS" + uuid.uuid4().hex
    sep = ("--" + boundary + "\r\n").encode()
    out = bytearray()

    for key, value in (fields or {}).items():
        if value is None or value == "":
            continue
        out += sep
        out += ('Content-Disposition: form-data; name="%s"\r\n\r\n' % key).encode()
        out += str(value).encode("utf-8") + b"\r\n"

    if blob is not None:
        out += sep
        out += ('Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
                % (file_field, os.path.basename(filename) or "upload.bin")
                ).encode("utf-8")
        out += ("Content-Type: %s\r\n\r\n" % _guess_type(filename)).encode()
        out += blob + b"\r\n"

    out += ("--" + boundary + "--\r\n").encode()
    return bytes(out), "multipart/form-data; boundary=" + boundary


class HttpRequestNode(Node):
    TYPE = "web.http"
    TITLE = "HTTP Request"
    CATEGORY = "action"
    INPUTS = 1
    OUTPUTS = 1
    PARAMS = [
        {"key": "url", "label": "URL", "type": "text", "default": "https://"},
        {
            "key": "method",
            "label": "Method",
            "type": "select",
            "default": "GET",
            "options": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"],
        },
        {"key": "headers", "label": "Headers", "type": "kv_dict", "default": {},
         "name_hint": "Header-Name", "value_hint": "value"},
        {"key": "body", "label": "Body (JSON)", "type": "json", "default": None},

        # ---- sending a file -------------------------------------------
        {"key": "send_as", "label": "Send as", "type": "select",
         "default": "json body",
         "options": ["json body", "raw file", "multipart file"],
         "desc": "json body = the Body field above, as JSON (the usual one). "
                 "raw file = the file's bytes ARE the request body. "
                 "multipart file = a form upload, the way a browser posts a "
                 "file -- what most upload endpoints expect."},

        {"key": "file_path", "label": "File to send", "type": "text",
         "default": "",
         "desc": "Which file to upload, for the two file modes. ~ works. "
                 "Expressions allowed.",
         "example": "{{ $json.audio_path }}"},

        {"key": "file_field", "label": "Upload field name", "type": "text",
         "default": "file",
         "desc": "The multipart field name the server reads the upload from. "
                 "'file' suits most; some want 'audio_file' or 'upload'."},

        {"key": "form_fields", "label": "Extra form fields", "type": "kv_dict",
         "default": {}, "name_hint": "field", "value_hint": "value",
         "desc": "Sent alongside the file in multipart mode."},

        {"key": "max_upload_mb", "label": "Max upload (MB)", "type": "number",
         "default": 50,
         "desc": "Refuse to send anything bigger, so a wrong path can't try "
                 "to push a disk image at an API."},

        # ---- getting a file back --------------------------------------
        {"key": "response_as", "label": "Response", "type": "select",
         "default": "auto",
         "options": ["auto", "save to file", "text"],
         "desc": "auto = text and JSON as normal, anything binary written to "
                 "disk (default). save to file = always write it out. "
                 "text = force the old behaviour and decode everything as "
                 "text, mojibake included."},

        {"key": "save_dir", "label": "Save folder", "type": "text",
         "default": "~/dugs_downloads",
         "desc": "Where a returned file is written. Created if missing."},

        {"key": "save_name", "label": "Save as", "type": "text", "default": "",
         "desc": "What to call it. Blank gives a timestamped name with the "
                 "right extension for whatever came back.",
         "example": "reply_{{ $json.chat_id }}.wav"},

        {"key": "timeout", "label": "Timeout (s)", "type": "number", "default": 15},
        {
            "key": "response_mode",
            "label": "Response",
            "type": "select",
            "default": "replace item",
            "options": ["replace item", "add to item"],
        },
        {
            "key": "response_field",
            "label": "Add to item under key (if 'add to item')",
            "type": "text",
            "default": "response",
        },
    ]

    # ------------------------------------------------------------------
    def _load_upload(self, raw_path, max_bytes):
        path = os.path.expanduser(os.path.expandvars(str(raw_path or "").strip()))
        if not path:
            raise ValueError("this Send as mode needs a file, but 'File to "
                             "send' is empty")
        if not os.path.isfile(path):
            raise ValueError("no file at %s" % path)
        size = os.path.getsize(path)
        if 0 < max_bytes < size:
            raise ValueError("file is %.1f MB, over the %.0f MB limit"
                             % (size / 1048576.0, max_bytes / 1048576.0))
        with open(path, "rb") as fh:
            return fh.read(), path

    def _save_download(self, blob, ctype, url, folder, name):
        if not name:
            ext = _EXT_BY_TYPE.get((ctype or "").split(";")[0].strip().lower())
            if not ext:
                ext = os.path.splitext(urllib.parse.urlparse(url).path)[1] or ".bin"
            name = "download_%d%s" % (int(time.time() * 1000), ext)
        elif not os.path.splitext(name)[1]:
            name += _EXT_BY_TYPE.get((ctype or "").split(";")[0].strip().lower(), ".bin")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        with open(path, "wb") as fh:
            fh.write(blob)
        return path

    def run(self, items):
        method = (self.params.get("method") or "GET").upper()
        timeout = float(self.params.get("timeout") or 15)
        response_mode = self.params.get("response_mode", "replace item")
        response_field = self.params.get("response_field") or "response"
        send_as = self.params.get("send_as", "json body")
        response_as = self.params.get("response_as", "auto")

        try:
            max_upload = int(float(self.params.get("max_upload_mb", 50) or 50) * 1048576)
        except (TypeError, ValueError):
            max_upload = 50 * 1048576

        save_dir = os.path.expanduser(os.path.expandvars(
            str(self.params.get("save_dir") or "~/dugs_downloads").strip()
            or "~/dugs_downloads"))

        run_for = items if items else [{"json": {}}]
        out = []

        for item in run_for:
            j = item.get("json", {})

            # resolve expressions against current item
            url = resolve_expr(self.params.get("url", ""), j)
            if not url or url == "https://":
                raise ValueError("HTTP Request node needs a URL")

            raw_headers = self.params.get("headers") or {}
            if isinstance(raw_headers, str):
                try: raw_headers = json.loads(raw_headers)
                except Exception: raw_headers = {}
            headers = {k: str(resolve_expr(v, j)) for k, v in raw_headers.items()}

            data_bytes = None
            sent_file = None

            # ---- build the request body ------------------------------
            if send_as in ("raw file", "multipart file"):
                try:
                    blob, sent_file = self._load_upload(
                        resolve_expr(self.params.get("file_path", ""), j), max_upload)
                except Exception as e:
                    out.append({"json": {"error": str(e), "url": url}})
                    continue

                if send_as == "raw file":
                    data_bytes = blob
                    headers.setdefault("Content-Type", _guess_type(sent_file))
                else:
                    raw_form = self.params.get("form_fields") or {}
                    if isinstance(raw_form, str):
                        try: raw_form = json.loads(raw_form)
                        except Exception: raw_form = {}
                    form = {k: resolve_expr(v, j) for k, v in raw_form.items()}
                    field = str(self.params.get("file_field") or "file")
                    data_bytes, ctype = _multipart(form, field, sent_file, blob)
                    headers["Content-Type"] = ctype
                if method in ("GET", "HEAD"):
                    method = "POST"      # you cannot upload a file with a GET
            else:
                body = self.params.get("body")
                if body is not None and method not in ("GET", "HEAD"):
                    if isinstance(body, str):
                        body = resolve_expr(body, j)
                        try: body = json.loads(body)
                        except Exception: pass
                    data_bytes = json.dumps(body).encode("utf-8")
                    headers.setdefault("Content-Type", "application/json")

            req = urllib.request.Request(url, data=data_bytes, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read()
                    status = resp.status
                    resp_headers = dict(resp.headers.items())
            except urllib.error.HTTPError as e:
                raw = e.read()
                status = e.code
                resp_headers = dict(e.headers.items()) if e.headers else {}
            except Exception as e:
                out.append({"json": {"error": str(e), "url": url}})
                continue

            ctype = resp_headers.get("Content-Type") or resp_headers.get("content-type") or ""

            # ---- decide: text, or a file on disk ----------------------
            as_file = (response_as == "save to file"
                       or (response_as == "auto" and raw and not _is_text(ctype)))

            if as_file:
                try:
                    name = _safe_name(resolve_expr(self.params.get("save_name", ""), j))
                    path = self._save_download(raw, ctype, url, save_dir, name)
                    response_data = {"status": status, "file_path": path,
                                     "bytes": len(raw), "content_type": ctype,
                                     "headers": resp_headers, "url": url}
                except Exception as e:
                    response_data = {"status": status,
                                     "error": "could not save the reply: %s" % e,
                                     "url": url}
            else:
                text = raw.decode("utf-8", errors="replace")
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    parsed = text
                response_data = {"status": status, "body": parsed,
                                 "headers": resp_headers, "url": url}

            if sent_file:
                response_data["sent_file"] = sent_file

            if response_mode == "add to item":
                new_json = dict(j)
                new_json[response_field] = response_data
                out.append({"json": new_json})
            else:
                out.append({"json": response_data})

        return out
