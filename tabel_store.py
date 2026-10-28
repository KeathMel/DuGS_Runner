"""
tabel_store.py — read/write for Tabels (the spreadsheet-style tables).

WHY DUGS_DATA_DIR IS HERE
=========================
This file used to resolve TABELS_DIR relative to its own source folder:

    HERE = os.path.dirname(os.path.abspath(__file__))
    TABELS_DIR = os.path.join(HERE, "tabels")

That is fine when the code and the data sit together, which is how the
desktop app runs. It is wrong in a container. There the code lives in
/runner and the mounted data volume is /data, so every tabel write landed
in /runner/tabels -- inside the container, not mounted, invisible to the
host, and destroyed the moment the container was recreated.

`storage.py` was fixed for this (that is how memory banks survive), but
this file was missed. The result was a workflow whose memory banks came
home on Undeploy while its tabels silently did not, because the app was
reading a /data/tabels folder the runner had never written to.

Same rule as storage.py now: everything under DUGS_DATA_DIR, which
dugs_runner.py already uses for projects/ and runs/. Unset -- the desktop
app -- falls back to the source folder, so nothing changes there.
"""
import os
import json

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DUGS_DATA_DIR", HERE)
TABELS_DIR = os.path.join(DATA_DIR, "tabels")


def ensure_dir():
    os.makedirs(TABELS_DIR, exist_ok=True)

def tabel_path(name):
    return os.path.join(TABELS_DIR, f"{name}.json")

def list_tabels():
    ensure_dir()
    return sorted(f[:-5] for f in os.listdir(TABELS_DIR) if f.endswith(".json"))

def load_tabel(name):
    with open(tabel_path(name)) as f:
        return json.load(f)

def save_tabel(name, data):
    ensure_dir()
    for i, row in enumerate(data.get("rows", []), start=1):
        row["id"] = i
    with open(tabel_path(name), "w") as f:
        json.dump(data, f, indent=2)

def new_tabel(name):
    data = {"name": name, "columns": ["column1"], "rows": []}
    save_tabel(name, data)
    return data
