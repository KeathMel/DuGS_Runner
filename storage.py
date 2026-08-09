"""
storage.py — local filesystem read/write for projects (workflows) and tabels
(spreadsheets). Pure file I/O, no Qt imports here on purpose.
"""
import os
import json

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECTS_DIR = os.path.join(HERE, "projects")
TABELS_DIR = os.path.join(HERE, "tabels")
CREDENTIALS_DIR = os.path.join(HERE, "credentials")
MEMORY_DIR = os.path.join(HERE, "memory_banks")
DOWNLOADS = os.path.expanduser("~/Downloads")


def _ensure(d):
    os.makedirs(d, exist_ok=True)


def _list(d):
    _ensure(d)
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json"))


def _path(d, name):
    return os.path.join(d, f"{name}.json")


def _load(d, name):
    with open(_path(d, name)) as f:
        return json.load(f)


def _save(d, name, data):
    _ensure(d)
    with open(_path(d, name), "w") as f:
        json.dump(data, f, indent=2)


def list_projects(): return _list(PROJECTS_DIR)
def load_project(n): return _load(PROJECTS_DIR, n)
def save_project(n, d): _save(PROJECTS_DIR, n, d)


# ---- project kind: "normal" (runs in the engine) or "servo" (generates
#      Arduino code instead of running). Stored in the project JSON.
def project_kind(n):
    """Return 'normal' or 'servo' for a saved project."""
    try:
        d = _load(PROJECTS_DIR, n)
        return d.get("kind", "normal")
    except Exception:
        return "normal"


# where generated .ino sketches get written
SKETCHES_DIR = os.path.join(HERE, "sketches")


def save_sketch(name, code):
    """Write a generated Arduino sketch. Arduino requires the .ino file to sit
    in a folder of the same name, so we make sketches/<name>/<name>.ino"""
    _ensure(SKETCHES_DIR)
    folder = os.path.join(SKETCHES_DIR, name)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{name}.ino")
    with open(path, "w") as f:
        f.write(code)
    return path


def list_tabels(): return _list(TABELS_DIR)
def load_tabel(n): return _load(TABELS_DIR, n)


def save_tabel(n, d):
    for i, row in enumerate(d.get("rows", []), start=1):
        row["id"] = i
    _save(TABELS_DIR, n, d)


# ---- credentials: named secrets (e.g. a DeepSeek token) reusable by nodes ----
def list_credentials(): return _list(CREDENTIALS_DIR)
def load_credential(n): return _load(CREDENTIALS_DIR, n)
def save_credential(n, d): _save(CREDENTIALS_DIR, n, d)


def delete_credential(n):
    p = _path(CREDENTIALS_DIR, n)
    if os.path.exists(p):
        os.remove(p)


# ---- UI layout state (panel sizes etc.), so the window remembers itself ----
_UI_STATE = os.path.join(HERE, "ui_state.json")


def load_ui_state():
    try:
        with open(_UI_STATE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_ui_state(d):
    try:
        with open(_UI_STATE, "w") as f:
            json.dump(d, f, indent=2)
    except Exception:
        pass


def new_tabel(n):
    save_tabel(n, {"name": n, "columns": ["column1"], "rows": []})


# ---- memory banks -------------------------------------------------------
# A memory bank is a small key/value store the AI/workflow can save things
# into, like a Tabel but simpler: each key holds a value plus optional
# expiry. Stored as plain JSON files so it works anywhere, no database.
import time as _time


def list_memory_banks():
    return _list(MEMORY_DIR)


def load_memory_bank(n):
    try:
        return _load(MEMORY_DIR, n)
    except Exception:
        return {"name": n, "entries": {}}


def save_memory_bank(n, d):
    _ensure(MEMORY_DIR)
    _save(MEMORY_DIR, n, d)


def new_memory_bank(n):
    save_memory_bank(n, {"name": n, "entries": {}})


def delete_memory_bank(n):
    try:
        os.remove(_path(MEMORY_DIR, n))
    except Exception:
        pass


def _bank_alive(entry):
    """An entry is alive if it has no expiry, or its expiry is still ahead."""
    exp = entry.get("expires_at")
    return exp is None or exp > _time.time()


def memory_get(bank, key):
    """Read one key, honouring expiry. Returns None if missing or expired."""
    d = load_memory_bank(bank)
    entry = (d.get("entries") or {}).get(key)
    if entry is None or not _bank_alive(entry):
        return None
    return entry.get("value")


def memory_all(bank):
    """Every live key/value in the bank, dropping expired ones as we go."""
    d = load_memory_bank(bank)
    entries = d.get("entries") or {}
    out, changed = {}, False
    for k, e in list(entries.items()):
        if _bank_alive(e):
            out[k] = e.get("value")
        else:
            del entries[k]; changed = True
    if changed:
        save_memory_bank(bank, d)
    return out


def memory_all_sorted(bank, order="oldest", limit=None):
    """Live entries as [(key, value, updated_at), ...], sorted by when they
    were written. order='oldest' or 'newest'. limit=None means everything —
    used by Memory Read to cap how much of a growing log gets fed out at
    once (handy for capping what reaches an AI node)."""
    d = load_memory_bank(bank)
    entries = d.get("entries") or {}
    live = [(k, e.get("value"), e.get("updated_at", 0))
           for k, e in entries.items() if _bank_alive(e)]
    live.sort(key=lambda t: t[2], reverse=(order == "newest"))
    if limit:
        live = live[:limit]
    return live


def memory_set(bank, key, value, ttl_seconds=None, append=False):
    """Write a key. ttl_seconds=None means it never expires.

    append=True writes a brand NEW, separate entry rather than growing
    whatever's already under `key` — each append is its own item you can
    read back individually (memory.read's 'all' mode gives you one item per
    key), not a blob that keeps getting longer every time something's added
    to it. The base key stays whatever it already was, untouched.

    Returns (value, actual_key) — actual_key is `key` itself normally, or
    the auto-generated key an append actually landed under.
    """
    d = load_memory_bank(bank)
    entries = d.setdefault("entries", {})
    expires = (_time.time() + ttl_seconds) if ttl_seconds else None

    actual_key = key
    if append:
        # a millisecond timestamp suffix guarantees a fresh key and keeps
        # appended entries naturally ordered by when they were written
        actual_key = f"{key}__{int(_time.time() * 1000)}"
        while actual_key in entries:   # astronomically rare, but be sure
            actual_key = f"{key}__{int(_time.time() * 1000)}_{os.urandom(2).hex()}"

    entries[actual_key] = {"value": value, "expires_at": expires,
                           "updated_at": _time.time()}
    save_memory_bank(bank, d)
    return value, actual_key


def memory_clear(bank):
    """Remove every entry from a bank, keeping the bank itself. Used by
    Memory Manage when compacting a spread-out log down to one summary."""
    d = load_memory_bank(bank)
    d["entries"] = {}
    save_memory_bank(bank, d)


# ---- deploy: copying a workflow into a runner's projects/ folder ----------
# The runner (DuGS_Runner container) watches a projects/ folder and runs
# whatever lands there. "Deploy" just copies a project's JSON into that folder;
# the runner picks it up within a few seconds on its own. We remember the
# folder path so the person only points at it once.
def deploy_path():
    """The runner's projects/ folder the app deploys into, or '' if unset."""
    return load_ui_state().get("deploy_path", "")


def set_deploy_path(path):
    st = load_ui_state()
    st["deploy_path"] = path or ""
    save_ui_state(st)


def list_deployed():
    """Names of projects currently sitting in the runner's folder."""
    p = deploy_path()
    if not p or not os.path.isdir(p):
        return []
    return sorted(f[:-5] for f in os.listdir(p) if f.endswith(".json"))


# ---- deployed-state registry ---------------------------------------------
# is_deployed() used to re-derive the answer by checking the filesystem every
# time (deploy_path() + does the file exist). That re-check could disagree
# with what Deploy/Undeploy just did — different working directory, a stale
# deploy_path, timing — so the button and the green name could drift out of
# sync with each other and with reality.
#
# Now the editor's Deploy/Undeploy actions are the single source of truth:
# they explicitly mark a project deployed or not, right here, at the moment
# it happens. is_deployed() just reads that record — no re-derivation, so it
# can't disagree with the button that changed it.
def mark_deployed(name, deployed=True):
    st = load_ui_state()
    deployed_set = set(st.get("deployed_projects", []))
    if deployed:
        deployed_set.add(name)
    else:
        deployed_set.discard(name)
    st["deployed_projects"] = sorted(deployed_set)
    save_ui_state(st)


def is_deployed(name):
    st = load_ui_state()
    return name in set(st.get("deployed_projects", []))


# ---- data dependencies: a workflow needing a Tabel or Memory Bank --------
# Tabels and memory banks are just local files, so a workflow that reads one
# works in the app (the file's right there) and comes up empty on the runner
# (it never had a copy). No manifest to track and keep in sync -- dependencies
# are derived by scanning the workflow itself, on demand, every time. If two
# workflows need the same tabel, "is it still needed" is answered the same
# way: scan whoever else is still deployed.
def _node_dependencies(nodes):
    """(tabel names, memory bank names) referenced by a list of node specs."""
    tabels, banks = set(), set()
    for n in nodes or []:
        t = n.get("type", "")
        params = n.get("params") or {}
        if t == "data.tabel":
            name = params.get("tabel")
            if name:
                tabels.add(name)
        elif t in ("memory.read", "memory.write"):
            name = params.get("bank")
            if name:
                banks.add(name)
    return tabels, banks


def project_dependencies(name):
    """The tabels/memory banks a saved project's nodes reference."""
    try:
        wf = load_project(name)
    except Exception:
        return set(), set()
    return _node_dependencies(wf.get("nodes", []))


def _deploy_base():
    """The runner's root folder, derived from deploy_path() the same way
    runs_path() derives its sibling -- deploy_path() points AT projects/
    itself."""
    p = deploy_path()
    if not p:
        return ""
    return os.path.dirname(p.rstrip("/\\"))


def _same_dir(a, b):
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except Exception:
        return False


def _guard_not_local_projects(p):
    """Refuse to deploy/undeploy if the runner folder points at the app's
    OWN local projects/ -- if the two are the same folder, 'undeploy' and
    'permanently delete my real saved project' become the exact same
    operation. Cheap check, catches a mistyped/mis-scanned path before it
    can destroy anything, regardless of how the path got set wrong."""
    if _same_dir(p, PROJECTS_DIR):
        raise RuntimeError(
            f"The runner folder is set to your own local projects/ folder "
            f"({p}) -- that's where YOUR saved projects live, not the "
            f"runner's. Point Deploy at the RUNNER's projects/ folder "
            f"instead (e.g. ~/Deploy_DuGS/projects), not this app's own.")


def deploy_project(name):
    """Copy a saved project into the runner's folder, along with whatever
    Tabels or Memory Banks its nodes actually use -- those land in tabels/
    and memory_banks/ next to projects/, so the runner's copy of storage.py
    (same file, same relative layout) finds them without any code change."""
    p = deploy_path()
    if not p:
        raise RuntimeError("no deploy folder set")
    if not os.path.isdir(p):
        raise RuntimeError(f"deploy folder not found: {p}")
    _guard_not_local_projects(p)
    data = load_project(name)          # the current saved version
    dest = os.path.join(p, f"{name}.json")
    with open(dest, "w") as f:
        json.dump(data, f, indent=2)

    base = _deploy_base()
    tabels, banks = _node_dependencies(data.get("nodes", []))
    if tabels:
        tdir = os.path.join(base, "tabels")
        os.makedirs(tdir, exist_ok=True)
        for t in tabels:
            try:
                with open(os.path.join(tdir, f"{t}.json"), "w") as f:
                    json.dump(load_tabel(t), f, indent=2)
            except Exception:
                pass   # tabel doesn't exist locally -- nothing to send
    if banks:
        bdir = os.path.join(base, "memory_banks")
        os.makedirs(bdir, exist_ok=True)
        for b in banks:
            try:
                with open(os.path.join(bdir, f"{b}.json"), "w") as f:
                    json.dump(load_memory_bank(b), f, indent=2)
            except Exception:
                pass

    mark_deployed(name, True)
    return dest


def undeploy_project(name):
    """Remove a project from the runner's folder. Also removes any Tabel or
    Memory Bank it needed there -- but only if nothing ELSE currently
    deployed still depends on it. Checked by scanning every other deployed
    project's own dependencies, not a separate tracked list, so there's
    nothing extra that can drift out of sync."""
    p = deploy_path()
    if not p:
        mark_deployed(name, False)
        return
    _guard_not_local_projects(p)

    my_tabels, my_banks = set(), set()
    dest = os.path.join(p, f"{name}.json")
    if os.path.isfile(dest):
        try:
            with open(dest) as f:
                my_tabels, my_banks = _node_dependencies(json.load(f).get("nodes", []))
        except Exception:
            pass
        os.remove(dest)
    mark_deployed(name, False)

    if not (my_tabels or my_banks):
        return

    # what does everyone ELSE still deployed still need?
    still_needed_tabels, still_needed_banks = set(), set()
    for other in list_deployed():
        if other == name:
            continue
        try:
            with open(os.path.join(p, f"{other}.json")) as f:
                t, b = _node_dependencies(json.load(f).get("nodes", []))
            still_needed_tabels |= t
            still_needed_banks |= b
        except Exception:
            continue

    base = _deploy_base()
    for t in my_tabels - still_needed_tabels:
        fp = os.path.join(base, "tabels", f"{t}.json")
        if os.path.isfile(fp):
            os.remove(fp)
    for b in my_banks - still_needed_banks:
        fp = os.path.join(base, "memory_banks", f"{b}.json")
        if os.path.isfile(fp):
            os.remove(fp)


def sync_deployed_from_disk():
    """One-time reconciliation: whatever is actually sitting in the runner's
    folder becomes the tracked deployed set. Covers upgrading from the old
    filesystem-check version, or the folder being edited by hand outside
    the app."""
    on_disk = set(list_deployed())
    st = load_ui_state()
    st["deployed_projects"] = sorted(on_disk)
    save_ui_state(st)
    return on_disk


def export_project(name, dest_path):
    """Write a project as one self-contained file the person can move to any
    machine by hand -- the workflow itself, plus whatever Tabels and Memory
    Banks its nodes use, bundled in. Not a plain drop-into-projects/ file
    (that's what Deploy is for) -- this is the portable, share-anywhere one."""
    wf = load_project(name)
    tabels, banks = _node_dependencies(wf.get("nodes", []))
    bundle = {
        "workflow": wf,
        "tabels": {},
        "memory_banks": {},
    }
    for t in tabels:
        try:
            bundle["tabels"][t] = load_tabel(t)
        except Exception:
            pass
    for b in banks:
        try:
            bundle["memory_banks"][b] = load_memory_bank(b)
        except Exception:
            pass
    with open(dest_path, "w") as f:
        json.dump(bundle, f, indent=2)
    return dest_path


# ---- run log: the runner's history of every run it has done ---------------
# The runner writes one file per run into its runs/ folder, sitting right next
# to its projects/ folder — same idea as deploy. We remember the runner's
# base folder once (same value as deploy_path, since runs/ and projects/ are
# siblings inside it) and read whatever's in runs/.
# One folder for the runner's runs/ directory, set the same way deploy_path
# is: point at it once (Scan / browse / type), remembered from then on. Falls
# back to deriving it next to deploy_path only if it was never set directly —
# covers upgrading from before this was its own setting.
def runs_path():
    """The runner's runs/ folder."""
    explicit = load_ui_state().get("runs_path", "")
    if explicit:
        return explicit
    dp = deploy_path()
    if not dp:
        return ""
    # deploy_path points AT the projects/ folder itself; runs/ is next to it
    base = os.path.dirname(dp.rstrip("/\\"))
    return os.path.join(base, "runs")


def set_runs_path(path):
    st = load_ui_state()
    st["runs_path"] = path or ""
    save_ui_state(st)


def set_runs_path_from_base(base_folder):
    """Point runs_path directly at <base_folder>/runs. Used by the run-log
    scan/browse dialog when the person points at the runner's root folder."""
    set_runs_path(os.path.join(base_folder, "runs"))


def list_runs(limit=200):
    """Every run record, most recent first. Each is the parsed JSON the
    runner wrote — workflow name, timestamp, duration, input, result, error."""
    p = runs_path()
    if not p or not os.path.isdir(p):
        return []
    files = sorted(
        (f for f in os.listdir(p) if f.endswith(".json")),
        reverse=True,   # filenames start with name__TIMESTAMP, so this isn't
    )                    # perfectly chronological across workflows; re-sort below
    out = []
    for f in files[:limit * 2]:   # a little slack before trimming to `limit`
        try:
            with open(os.path.join(p, f)) as fh:
                rec = json.load(fh)
            rec["_file"] = f
            out.append(rec)
        except Exception:
            continue
    out.sort(key=lambda r: r.get("ran_at", ""), reverse=True)
    return out[:limit]


# ---- run log auto-cleanup --------------------------------------------------
# Old run files can pile up forever otherwise. Three settings: never, every
# 24h, every 5h. Uses the same runs_path already set — nothing extra to
# configure. The sweep only actually happens when a refresh notices a run it
# hasn't seen before, so it's not running a background timer of its own.
def run_cleanup_setting():
    """'never' | '24h' | '5h'"""
    return load_ui_state().get("run_cleanup", "never")


def set_run_cleanup_setting(value):
    if value not in ("never", "24h", "5h"):
        value = "never"
    st = load_ui_state()
    st["run_cleanup"] = value
    save_ui_state(st)


def sweep_old_runs():
    """Delete run files older than the configured window. No-op on 'never'.
    Safe to call often — it's cheap and only does work when there's actually
    something past the cutoff."""
    setting = run_cleanup_setting()
    if setting == "never":
        return 0
    hours = {"24h": 24, "5h": 5}.get(setting)
    if hours is None:
        return 0

    p = runs_path()
    if not p or not os.path.isdir(p):
        return 0

    cutoff = _time.time() - hours * 3600
    removed = 0
    for f in os.listdir(p):
        if not f.endswith(".json"):
            continue
        fp = os.path.join(p, f)
        try:
            if os.path.getmtime(fp) < cutoff:
                os.remove(fp)
                removed += 1
        except Exception:
            continue
    return removed

