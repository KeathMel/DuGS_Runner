# DuGS Runner

The headless half of DuGS. It runs your workflows with no window, no mouse and
no desktop — on a server, in a container, on a Raspberry Pi, in Termux on a
phone. The desktop app is where you *build* a workflow; this is where it
*lives* once it works.

It is the same engine and the same node files as the app, with the editor cut
away. Anything that runs in the app runs here identically, because it is
literally the same `engine.py` and the same `nodes/` folder.

**Pure standard library.** No pip install, no virtualenv, no dependencies. If
a machine has Python 3.10+ it can run this.

---

## Contents

- [Quick start](#quick-start)
- [Running it in a container](#running-it-in-a-container)
- [Mounts: the thing that breaks first](#mounts-the-thing-that-breaks-first)
- [Configuration](#configuration)
- [HTTP endpoints](#http-endpoints)
- [Posting a file: voice notes and uploads](#posting-a-file-voice-notes-and-uploads)
- [Replying with a file](#replying-with-a-file)
- [Running the voice stack](#running-the-voice-stack)
- [Where data lives](#where-data-lives)
- [Credentials](#credentials)
- [Deploying from the app](#deploying-from-the-app)
- [Run logs](#run-logs)
- [How the engine executes a workflow](#how-the-engine-executes-a-workflow)
- [Writing a node](#writing-a-node)
- [The node library](#the-node-library)
- [Exposing it to the internet](#exposing-it-to-the-internet)
- [Troubleshooting](#troubleshooting)

---

## Quick start

Straight on the machine, no container:

```bash
git clone https://github.com/KeathMel/DuGS_Runner
cd DuGS_Runner
python3 dugs_runner.py
```

It prints what it found and what it is listening on:

```
====================================================
  DuGS runner
====================================================
[06:07] app dir  : /home/k/DuGS_Runner
[06:07] data dir : /home/k/DuGS_Runner
[06:07] projects : /home/k/DuGS_Runner/projects
[06:07] engine ready — 41 node types from /home/k/DuGS_Runner/nodes
[06:07] loaded 1 workflow(s)
[06:07] webhook  POST   /bruh                    -> Schwortz
[06:07] listening on http://0.0.0.0:5801
[06:07] watching projects/ — drop a workflow in and it starts by itself
```

Drop a workflow JSON into `projects/` and it starts running. No restart: the
folder is watched, and a new or changed file is picked up within a few seconds.

Port **5801**, deliberately not 5800, so the runner and the desktop app can sit
on the same machine without fighting over a port.

---

## Running it in a container

```bash
podman build --no-cache -t dugs-runner .
```

`--no-cache` matters more than it looks. Podman and Docker will happily reuse a
cached layer with the same tag after the source changed, and it fails
**silently** — the container starts, looks healthy, `curl` works, and the fix
you just made is not in there. Checking is one line:

```bash
podman exec dugs grep -c "the_thing_you_just_changed" /runner/engine.py
```

`0` means you are still on the old image.

Then run it. This is the full command with every mount that matters:

```bash
podman run -d --name dugs --replace --restart unless-stopped \
  -p 127.0.0.1:5801:5801 \
  -v $HOME/DuGS_Runner/projects:/data/projects \
  -v $HOME/DuGS_Runner/credentials:/data/credentials \
  -v $HOME/DuGS_Runner/memory_banks:/data/memory_banks \
  -v $HOME/DuGS_Runner/tabels:/data/tabels \
  -v $HOME/DuGS_Runner/runs:/data/runs \
  dugs-runner
```

`--restart unless-stopped` is not optional if you want it back after a reboot.
Without it the container dies with the machine and stays dead until you start
it by hand. For rootless podman you also want the user's services to survive
logout:

```bash
loginctl enable-linger $USER
```

`compose.yml` is there if you prefer `podman compose up -d` / `docker compose
up -d`; restart policy is already set in it.

### Why `127.0.0.1:5801:5801` and not `-p 5801:5801`

The runner has **no authentication on webhooks at all**. Anyone who can reach
the port can fire any workflow on it. Binding the published port to loopback
means only this machine can reach it, and anything from outside has to come
through a tunnel you control. Publish it on `0.0.0.0` only if you have
genuinely decided that is what you want.

---

## Mounts: the thing that breaks first

A container has its own filesystem. A node pointed at `/home/you/notes.json`
looks for that path **inside the container**, where it does not exist, and
fails with:

```
File Edit: '/home/you/notes.json' does not exist
```

The file is right there on your disk. The container simply cannot see it.

So every folder your **File** nodes read or write has to be mounted, at the
same absolute path inside as outside, so the paths in your nodes work
untouched:

```bash
-v $HOME/.config/MY_STUFF:/home/you/.config/MY_STUFF
```

One mount covers a whole tree, subfolders included, so mount the top folder
rather than each file. If you get *permission denied* on a bind mount under
SELinux, append `,z`:

```bash
-v $HOME/.config/MY_STUFF:/home/you/.config/MY_STUFF:z
```

This failure hides for a long time, because a File node on an untaken branch is
skipped and never touches the path. It surfaces the first time a workflow
actually routes to it.

---

## Configuration

Everything is an environment variable. Nothing is a config file.

| Variable | Default | What it does |
|---|---|---|
| `DUGS_DATA_DIR` | the source folder | Where `projects/`, `runs/`, `tabels/`, `credentials/`, `memory_banks/` live. The Dockerfile sets it to `/data`. |
| `DUGS_HOST` | `0.0.0.0` | Interface to bind. `127.0.0.1` to refuse everything but this machine. |
| `DUGS_PORT` | `5801` | Port to listen on. Defined once in the Dockerfile as a build arg and read everywhere else. |
| `DUGS_APP_DIR` | the source folder | Where `nodes/` is loaded from. |
| `DUGS_SECRET` | a built-in default | Key used to unscramble deployed credentials. **Must match the app's.** |

---

## HTTP endpoints

| Method | Path | What it does |
|---|---|---|
| `GET` | `/health` | Liveness check. Used by the container healthcheck. |
| `GET` | `/workflows` | The names of every workflow currently loaded. |
| `POST` | `/deploy` | Accepts a workflow JSON, saves it to `projects/`, registers its triggers immediately. This is what the app's Deploy button calls. |
| `POST` | `/run/<name>` | Runs a saved workflow by name whatever its trigger is. Request body becomes the start data. |
| `POST` `PUT` `DELETE` | *your webhook paths* | Any path a Webhook Trigger node registers. Matched exactly as written. |

A Webhook Trigger with path `/bruh` is reachable at `http://host:5801/bruh` —
**no prefix**. (The desktop app puts webhooks under `/hook/` instead, so the
same workflow is `http://host:5800/hook/bruh` there. Easy to mix up.)

The item a webhook run starts with looks like this:

```json
{ "method": "POST", "path": "/bruh", "query": {}, "headers": {...},
  "body": <parsed JSON, or raw text, or null> }
```

so `{{ $json.body.message }}` is how you reach what was sent.

If the workflow contains a **Respond to Webhook** node, its body becomes the
HTTP reply. If it does not, the caller gets `{"ok": true}`. Getting `{"ok":
true}` when you expected a real answer almost always means the run never
reached Respond — check the run log for a node that errored or a Switch that
matched nothing.

---

## Posting a file: voice notes and uploads

A webhook body does not have to be JSON. POST a `.webm` voice note straight
at a webhook path with `Content-Type: audio/webm` and the runner **writes it
to `uploads/` and hands the workflow the path**:

```json
{ "audio_path": "/data/uploads/upload_1760000000000.webm",
  "file_path":  "/data/uploads/upload_1760000000000.webm",
  "filename":   "upload_1760000000000.webm",
  "content_type": "audio/webm",
  "bytes": 29348,
  "is_binary": true }
```

so a Speech to Text node reads it with source **file path** and
`{{ $json.body.audio_path }}`.

This matters more than it sounds. Binary bodies used to be decoded as UTF-8
with `errors="replace"`, which turns every byte that is not valid UTF-8 into
`U+FFFD` — the audio was destroyed before any node could see it, and there
was nothing left to point a node at. Now the bytes reach disk untouched.

JSON and text bodies behave exactly as before. The routing rule is the
Content-Type: `json`, `text/*`, `xml`, `csv` and friends are text, everything
else is a file.

Uploads are working data, not an archive — anything older than 24 hours is
swept on the next upload. Mount `uploads/` if you want to listen to what
actually arrived:

```
-v $HOME/DuGS_Runner/uploads:/data/uploads
```

To branch on it, test `{{ $json.body.is_binary }}` with **exists** — truer
than matching the Content-Type string, which browsers send as
`audio/webm;codecs=opus`.

---

## Replying with a file

The mirror of the above. Set **Respond to Webhook**'s Body to `file` and
point "File to send" at a path — normally the wav a Text to Speech node just
wrote:

```
{{ $json.audio_path }}
```

The reply is then the file itself: real bytes, the right `Content-Type`, no
JSON wrapper and no base64. Leave Content-Type blank and it is worked out
from the extension.

The node hands over the **path**, not the bytes, and the server streams the
file. So a few hundred KB of audio never enters the item stream: run logs
stay readable and memory does not balloon on a long reply.

A client reading that reply must check the Content-Type before decoding.
Calling `.decode()` on a wav raises
`'utf-8' codec can't decode byte 0xac in position 29`.

---

## Running the voice stack

Whisper and Piper are not bundled; you run them and point the nodes at them.
Both of these survive a reboot with the same `--restart unless-stopped` the
runner uses.

**Whisper** — [Speaches](https://speaches.ai), OpenAI-compatible, models
download on first use:

```bash
podman network create dugs-net

podman run -d --name speaches --replace --restart unless-stopped \
  --network dugs-net -p 127.0.0.1:8000:8000 \
  -v speaches-hf-cache:/home/ubuntu/.cache/huggingface/hub \
  ghcr.io/speaches-ai/speaches:latest-cpu

curl -X POST "http://127.0.0.1:8000/v1/models/Systran/faster-whisper-small"
```

Swap `latest-cpu` for `latest-cuda` plus `--device=nvidia.com/gpu=all` if you
have an NVIDIA card and `nvidia-container-toolkit` with CDI set up. The CUDA
image is several GB, and podman stages pulls in `/var/tmp` — if root is
tight, `TMPDIR=~/tmp podman pull ...` puts the staging somewhere roomier.

**Piper** — no official image, so a four-line one:

```dockerfile
FROM python:3.12-slim
RUN pip install --no-cache-dir "piper-tts[http]"
ENV PIPER_VOICE=en_US-lessac-medium
RUN python3 -m piper.download_voices ${PIPER_VOICE} --data-dir /voices
EXPOSE 5000
CMD python3 -m piper.http_server -m ${PIPER_VOICE} --data-dir /voices --host 0.0.0.0 --port 5000
```

```bash
podman build -t localhost/piper .
podman run -d --name piper --replace --restart unless-stopped \
  --network dugs-net -p 127.0.0.1:5000:5000 localhost/piper
```

The voice is baked into the image, not set on the node — Piper's http_server
is launched with one voice and `/synthesize` takes only `{"text": ...}`. To
change it, edit `PIPER_VOICE` and rebuild. Samples:
[rhasspy.github.io/piper-samples](https://rhasspy.github.io/piper-samples/).

**Node settings.** Put the runner on `dugs-net` too, then containers reach
each other by name — `localhost` inside a container means that container:

| Node | Setting | Value |
|---|---|---|
| Speech to Text | Endpoint | `http://speaches:8000/v1/audio/transcriptions` |
| | Model | `Systran/faster-whisper-small` |
| Text to Speech | Endpoint | `http://piper:5000/synthesize` |
| | Request shape | `piper json` |

Whisper mangles names it has never heard. The **Hint** field exists for
exactly that — list your proper nouns and they stop coming out as nonsense.

### Surviving a reboot

`--restart unless-stopped` covers crashes, not power cycles. Rootless podman
needs two more things, once:

```bash
sudo loginctl enable-linger $USER
systemctl --user enable --now podman-restart.service
```

---

## Where data lives

Everything hangs off `DUGS_DATA_DIR`:

```
<DUGS_DATA_DIR>/
  projects/       workflow JSON — drop one in and it runs
  runs/           one JSON per execution (see Run logs)
  tabels/         spreadsheet-style tables
  memory_banks/   key/value stores the Memory nodes use
  credentials/    named secrets, scrambled
  uploads/        files POSTed to a webhook; swept after 24h
```

`nodes/` is loaded from `DUGS_APP_DIR` (the source folder), not the data dir —
it is code, not data.

A **memory bank** is one file:

```json
{ "name": "Schwortz",
  "entries": {
    "summary":            {"value": "...", "expires_at": null, "updated_at": 1760000000.0},
    "chat__1791519578220": {"value": "...", "expires_at": null, "updated_at": 1760000001.0}
  } }
```

Memory Write in *append* mode makes a **new** key with a millisecond timestamp
suffix rather than growing an existing value, so each append stays a separate
item you can read back on its own.

A **tabel** is also one file:

```json
{ "name": "Finances",
  "columns": ["DATE", "Current", "IN", "OUT", "REASON"],
  "rows": [ {"id": 1, "DATE": "2026-10-10", "Current": 1842.5, "IN": 0, "OUT": 42.8, "REASON": "Rewe"} ] }
```

Note that row `id` is **renumbered 1…N on every save**. If you match rows on
`id` and then delete one, every row after it shifts identity. Match on a column
you control instead if that matters.

---

## Credentials

A credential is a named secret — a DeepSeek token, say — that nodes reference
by name instead of embedding. They live in `credentials/` and are stored
scrambled:

```json
{"__enc__": "…"}
```

**Be clear-eyed about what that is.** It is an HMAC-SHA256 keystream over a key
from `DUGS_SECRET`, and with the built-in default key it is *obfuscation, not
security* — it stops a token being readable at a glance in a file or a git
diff. It does not protect against anyone who has the repo, because the default
key is in the repo.

For it to be real encryption you must set `DUGS_SECRET` yourself, to the same
value on the machine running the app and the machine running the runner:

```bash
export DUGS_SECRET='something long and random'
```

Mismatched secrets are a confusing failure: the runner loads the credential,
fails to unscramble it, and the AI node behaves as if it had no key.

Plain unencrypted credential files from older versions still load.

---

## Deploying from the app

The app's **Deploy** button does the whole thing in one call:

1. writes the workflow JSON into the runner's `projects/`
2. copies the **Tabels** and **Memory Banks** that workflow's nodes actually
   reference into `tabels/` and `memory_banks/`
3. copies the **credentials** those nodes reference, scrambled on the way out
4. the runner notices the new file and registers its triggers — no restart

**Undeploy** syncs back: memory banks and tabels the runner has been writing to
come home, so a conversation the runner had is not lost when you pull the
workflow back to the desktop.

You can also deploy by hand — `POST` the workflow JSON to `/deploy`, or just
copy the file into `projects/`.

---

## Run logs

Every execution writes one JSON file into `runs/`, named
`<Workflow>__<date>_<time>_<ms>.json`. It holds:

- `input` — the request that started it
- `result` — **every node's output**, keyed by node name
- `node_status` — per node: `items_out`, `ms`, and `tokens` where an AI node reported them
- `layout` — node positions and connections, so the run can be drawn on a canvas later
- `error` — if the run itself blew up

This is the first place to look when something misbehaves. `node_status` alone
usually tells you the story: a node with `items_out: 0` and no `ms` never ran
(dead branch), and a node that errored carries the reason on its output item.

---

## How the engine executes a workflow

Identical in the runner and the app — same `engine.py`.

### The shape of everything

An **item** is `{"json": {...}}`. Nodes receive a list of them and return a
list of them. A node with several outputs returns a list of lists, one per
output port.

### Expressions

Any text parameter can contain `{{ }}`:

- `{{ $json.field }}` — a field on the item flowing into this node
- `{{ $json.body.message }}` — nested works
- `{{ $('Some Node').item.json.field }}` — a field on **that node's own
  output**, wherever it is in the graph

The second form is the important one. It reads from the engine's stored results
rather than the item on the wire, which is why it still resolves after a loop,
after a node has stripped the item down, or across a branch.

### Starting

Start nodes are those with `INPUTS == 0` or no incoming connection. A webhook
run instead starts at one specific node with the request as its data.

### Waiting for inputs

A node runs when **every wire into every connected input port has delivered** —
not when the first one does. Where two wires meet on one input ("read the file,
or skip straight past it"), whichever arrived first used to win, and if the
empty one won the real data was thrown away. Now both are counted.

### Dead branches

An IF or Switch sends items down one branch; the others deliver zero items. A
node with at least one wired input that receives **nothing on all of them** is
marked `SKIPPED (branch not taken)`:

- its `run()` is never called, so it cannot raise a config error for work
  nobody asked for
- it still emits empty output on every port, so a Merge waiting downstream does
  not deadlock

A node that genuinely wants to fire on nothing opts out with
`RUNS_ON_EMPTY = True`.

If real items arrive at a node that was collapsed this way, it runs after all.
That matters on the pass a loop exits on, where the node after the loop had
already been marked done by an earlier empty delivery.

### Loops

Two ways, and you rarely need the first:

**A Loop node** (`core.loop`, `ALLOW_RERUN = True`) batches items and fires
repeatedly, resetting everything downstream of it on each pass.

**Any cycle in the graph.** Wire a node's output back round to it — through an
IF, normally — and the engine works out from the connections that those nodes
sit on a cycle and lets them run again. No special node, no checkbox.

Both are bounded:

- a back-edge carrying **zero items** does not re-trigger, so a loop ends
  naturally when its IF stops sending anything round
- a hard cap of **25 passes** per node as a backstop, overridable per workflow
  with a top-level `"max_loops": 50` in the JSON

`results` is never cleared between passes, which is why
`{{ $('Some Node').item.json.x }}` still sees earlier passes.

### Responding mid-run

A **Respond to Webhook** node records the reply and the run **keeps going** —
nodes wired after it, and nodes on other branches, still execute. The HTTP
reply is sent when the whole run finishes. The trade-off is deliberate: a slow
node after Respond makes the caller wait, in exchange for that node actually
running at all.

### Events

The engine calls an optional `on_event` callback as it goes. This is how the
app lights up live, and what the runner records:

| kind | carries |
|---|---|
| `start` | `nodes` — the start node names |
| `node_running` | `node`, `type`, `items_in`, `pass` |
| `node_done` | `node`, `items_out`, `ports`, `ms`, `sample`, `in_sample`, `pass` |
| `edge` | `from`, `out`, `to`, `in`, `items` |
| `node_error` | `node`, `error` |
| `done` | — |

`pass` counts how many times that node has executed **in this run**, so a
looped node's activations can be kept apart. `in_sample` is what went in, which
lets a viewer subtract it and show only what the node itself produced.

Exceptions thrown inside `on_event` are swallowed — a broken listener can never
break a run.

---

## Writing a node

Drop a `.py` file in `nodes/` and restart. That is the whole process.

```python
from node_base import Node


class ShoutNode(Node):
    TYPE = "text.shout"          # unique id, used in the workflow JSON
    TITLE = "Shout"              # what the canvas shows
    CATEGORY = "core"            # core | logic | data | action | ai | trigger | robotics
    INPUTS = 1
    OUTPUTS = 1
    PARAMS = [
        {"key": "field", "label": "Field", "type": "text", "default": "text",
         "desc": "Which field to shout.",
         "example": "{{ $json.message }}"},
    ]

    def run(self, items):
        field = self.p("field", "text")
        out = []
        for it in (items or [{"json": {}}]):
            j = dict(it.get("json", {}))
            j[field] = str(self.rexpr(j.get(field, ""), j)).upper()
            out.append({"json": j})
        return out
```

What the base class gives you:

- `self.p(key, default)` — a parameter, with `None` treated as missing
- `self.rexpr(value, item_json)` — resolve `{{ }}` including `$('Node')` refs
- `self.resolve(key, item_json, default)` — both at once
- `self.params` — the raw dict
- `self._context` — every node's output so far, set by the engine before `run()`

Optional class flags:

- `ALLOW_RERUN = True` — may execute more than once per run (loop nodes)
- `RUNS_ON_EMPTY = True` — run even with zero input items

Parameter types the desktop editor knows how to draw: `text`, `multiline`,
`number`, `bool`, `select` (with `options`, or `options_from: "credentials"`),
`json`, `json_object`, `kv_rows`, `kv_dict`, `switch_rules`, `filter_rules`,
`tabel`, `memory`, `semantic`. The runner ignores all of this — it only reads
values — so a node with an exotic parameter type still runs headless.

> **`show_if` is declared in several nodes and is not implemented.** Parameters
> that say they only appear for a given operation appear always. Harmless, but
> do not trust it.

A note on IF, Switch and Filter: `exists` and `not exists` used to compare
the *literal expression text* against the item's keys, so with a `{{ }}`
field `exists` was permanently false and `not exists` permanently true. They
now use the same missing-field detection as every other operator, and work
with expressions.

Raising an exception inside `run()` does not kill the workflow. The engine
catches it, emits `node_error`, and passes an item carrying `{"error": "..."}`
downstream.

---

## The node library

43 node files ship in `nodes/`. **41 load in the container** — see the warning
below. Categories are how the editor groups them; the runner does not care.

**trigger** — `webhook.trigger`, `trigger.schedule`, `trigger.manual`

**core** — `core.set`, `core.edit_fields`, `core.code`, `core.merge`,
`core.loop`, `core.batch`, `core.split_out`, `core.aggregate`, `core.sort`,
`core.limit`, `core.dedupe`, `core.text`, `core.datetime`, `core.hash`,
`core.wait`, `core.log`

**logic** — `logic.if`, `logic.switch`, `logic.filter`, `logic.keywords`

**data** — `data.tabel`, `data.semantic`, `data.notes`, `core.calculator`,
`memory.read`, `memory.write`, `memory.manage`

**action** — `file.read`, `file.create`, `file.edit`, `file.delete`,
`web.http`, `webhook.respond`, `action.telegram`, `action.discord`

**ai** — `ai.agent`, `ai.openrouter`, `audio.stt`, `audio.tts`

**robotics** — `device.pins`

### Two nodes do not load in the container

On startup in a container you will see:

```
[warn] could not load semantic_router.py: No module named 'semantic_search'
[warn] could not load pins.py: No module named 'device_base'
```

Neither is mysterious: the Dockerfile only copies a fixed list of top-level
modules, and `semantic_search.py` and `device_base.py` are not on it. The
nodes are present, their dependency is not, so the engine skips them and
reports 41 types instead of 43.

`device.pins` not loading is fine — hardware pins mean nothing in a container.
**`data.semantic` not loading is a real loss**, because Semantic Router is how
you route by meaning without paying for a model call. To fix it, add the file
to the Dockerfile:

```dockerfile
COPY dugs_runner.py engine.py node_base.py storage.py tabel_store.py \
     ai_helper.py semantic_search.py ./
```

Rebuild with `--no-cache` and the warning goes away. Running the runner
directly on a machine, rather than in a container, has neither problem.

A few worth knowing about:

- **`core.calculator`** — an AST-walking arithmetic evaluator. It does **not**
  use `eval`: the expression is parsed and walked one node at a time, and
  anything that is not arithmetic is refused, so no imports, no attribute
  access, no function calls beyond a small whitelist. Point an AI at it instead
  of letting the model do sums, and even a weak model becomes reliable at
  numbers.
- **`logic.keywords`** — true/false on whether any of a list of words or
  phrases appear in some text. Cheap routing without an AI call.
- **`data.semantic`** — routes by meaning rather than exact words, using a
  local semantic table. No model call.
- **`memory.manage`** — watches a bank's token count and, past a threshold,
  asks a model to compact it into a summary.
- **`audio.stt` / `audio.tts`** — talk to a self-hosted Whisper and Piper.
  Neither bundles a model; see [Running the voice
  stack](#running-the-voice-stack).
- **`web.http`** — besides JSON, it can upload a file (raw body or multipart)
  and save a binary reply to disk rather than mangling it into a string.
- **`data.tabel`** — an empty read no longer kills the chain: it sends one
  item saying `{"count": 0, "empty": true}` so the nodes after it still run.
  It can also map values to columns individually, or split ONE JSON field
  from an AI into the right columns.

---

## Exposing it to the internet

`tunnel.py` is a supervisor for `cloudflared` — it starts the tunnel and keeps
it up. Nothing more; it is not a server and it has no opinions about your
workflows.

Understand clearly what a tunnel does: while it is up, **the URL is reachable
by anyone who learns it**, and the runner has no authentication. If a workflow
can write files or spend money on an API, treat that URL as a secret, and bring
the tunnel down when you are not using it.

---

## Troubleshooting

**Everything returns `{"ok": true}` instead of my real answer.**
The run never reached Respond to Webhook. Read the newest file in `runs/` —
look for a node with an `error` on its output, or a Switch where every port got
zero items.

**A File node says a file does not exist, but it is right there.**
You are in a container and that path is not mounted. See
[Mounts](#mounts-the-thing-that-breaks-first).

**The AI node fails instantly — the whole run takes a couple of milliseconds.**
No credential. A real model call cannot complete in 2 ms. Check
`credentials/` exists *inside* the container:
```bash
podman exec dugs ls /data/credentials
```
and that `DUGS_SECRET` matches the machine that deployed it.

**I rebuilt but my change is not in there.**
Cached layer. Rebuild with `--no-cache` and verify with `podman exec dugs grep
-c ...`.

**The container is gone after a reboot.**
No `--restart unless-stopped`, or no `loginctl enable-linger` for rootless
podman.

**Connection refused through my tunnel.**
Check the container is actually up — `podman ps`. A container that exited still
leaves the tunnel pointing at nothing.

**A node after my loop never runs.**
It should. If it genuinely does not, check the run log for `SKIPPED (branch not
taken)` on that node and tell us — that path is specifically handled and a
failure there is a bug worth reporting.

**Unknown node type: '...' — is the node file in nodes/?**
The workflow references a node whose `.py` is not in `nodes/`. Copy it over and
rebuild if containerised.

**`[warn] could not load semantic_router.py` on startup.**
Expected in a container, and it means Semantic Router is unavailable there. See
[Two nodes do not load in the container](#two-nodes-do-not-load-in-the-container).

**The node count on startup is lower than the number of files in `nodes/`.**
Same cause — a node whose import failed is skipped with a warning rather than
taking the whole runner down. Read the warnings above the `engine ready` line.
