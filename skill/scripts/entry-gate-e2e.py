#!/usr/bin/env python3
"""Fresh-process E2E for the manu entry gate — proves the ON-DISK plugin opens an ASSIGNED card.

Run with the Hermes venv python:

    ~/.hermes/hermes-agent/venv/bin/python scripts/entry-gate-e2e.py

A fresh process runs exactly what a restarted gateway/dashboard runs, so run this
after any change to the plugin's hook code (a green pytest suite runs the module,
not the process — see SKILL.md pitfall: committed != loaded). The final word is
still the live check on a new session.

Writes nothing to the live board: everything happens on a throwaway DB in a temp dir.
"""

import importlib.util
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

HERMES = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
sys.path.insert(0, str(HERMES / "hermes-agent"))
PLUGIN = HERMES / "plugins" / "manu" / "__init__.py"

spec = importlib.util.spec_from_file_location("manu_plugin", PLUGIN)
manu = importlib.util.module_from_spec(spec)
sys.modules["manu_plugin"] = manu
spec.loader.exec_module(manu)

print("module:            ", PLUGIN)
print("_default_assignee():", repr(manu._default_assignee()))

tmp = Path(tempfile.mkdtemp(prefix="manu-e2e-"))
db = tmp / "kanban.db"
out = manu._entry_hook(
    user_message="scrivi un report di prova end to end per il controllo assegnatario",
    session_id="e2e-session-1",
    _db=db,
)
assert out and "opened for this request" in out["context"], out
print("entry line:        ", out["context"])

con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
row = con.execute(
    "select id, status, assignee, session_id, idempotency_key from tasks"
).fetchone()
print("card row:          ", dict(row))

expected = manu._default_assignee()
assert expected, "kanban.default_assignee is not set — a created card would land unassigned"
assert row["status"] == "triage", dict(row)
assert row["assignee"] == expected, f"assignee={row['assignee']!r}, expected {expected!r}"
assert row["session_id"] == "e2e-session-1", dict(row)
assert (row["idempotency_key"] or "").startswith("manu:e2e-session-1:"), dict(row)

out2 = manu._entry_hook(
    user_message="scrivi un altro report sul confronto", session_id="e2e-session-1", _db=db
)
assert out2 and "phase Plan" in out2["context"], out2
n = con.execute("select count(*) from tasks").fetchone()[0]
con.close()
assert n == 1, f"expected 1 card, found {n}"
print("E2E OK: entry card assigned; re-entry recited; no duplicate")
