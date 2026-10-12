#!/usr/bin/env python3
"""Fresh-process E2E for the manu Check — proves `manu_review` end to end.

Run with the Hermes venv python:

    ~/.hermes/hermes-agent/venv/bin/python scripts/review-e2e.py

A fresh process runs exactly what a restarted gateway/dashboard runs, and this
script makes the REAL auxiliary call (the configured `auxiliary.review.*` route),
so it proves the whole path: read the card, build the reviewer's context, get a
verdict, record it as the card's `review_requested` summary.

Costs one small completion. Writes nothing to the live board: the card lives on a
throwaway DB in a temp dir.
"""

import importlib.util
import json
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

from hermes_cli import kanban_db as kb  # noqa: E402  (after the sys.path insert)
from hermes_cli import kanban_db_connect as kbc  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="manu-review-e2e-"))
board = tmp / "kanban.db"
report = tmp / "report.md"
report.write_text(
    "# LoRa mesh comparison\n\n"
    "1. Meshtastic — docs.meshtastic.org (mesh size: 100+ nodes)\n"
    "2. MeshCore — meshcore.io (mesh size: not stated)\n"
    "3. Reticulum — reticulum.network (mesh size: not stated)\n",
    encoding="utf-8",
)

criteria = (
    "**Acceptance criteria**\n"
    "- [ ] AC1: the report cites at least 3 sources\n"
    "- [ ] AC2: every source states the mesh size it supports\n"
)
with kbc.connect_closing(board) as conn:
    task_id = kb.create_task(conn, title="Compare the LoRa mesh stacks", body=criteria, assignee="default")
    kb.claim_task(conn, task_id, claimer="e2e:1")
    kb.add_attachment(conn, task_id, filename="report.md", stored_path=str(report),
                      content_type="text/markdown", uploaded_by="review-e2e")
print("plugin:   ", PLUGIN)
print("board:    ", board)
print("card:     ", task_id)

result = manu.review(task_id, [str(report)], db=board)
print("result:   ", json.dumps(result, indent=2, ensure_ascii=False)[:1200])

with kbc.connect_closing(board) as conn:
    status = kb.get_task(conn, task_id).status
    row = conn.execute(
        "select payload from task_events where task_id = ? and kind = 'review_requested'", (task_id,)
    ).fetchone()
    run = conn.execute(
        "select summary from task_runs where task_id = ? order by id desc limit 1", (task_id,)
    ).fetchone()
summary = json.loads(row["payload"])["summary"] if row else None
print("status:   ", status)
print("event:    ", summary)
print("run:      ", (run["summary"] or "").replace("\n", " ⏎ ")[:600])

assert result["success"] is True, result
assert status == "review", status
assert row is not None and "verdict:" in (summary or ""), summary
assert result["verdict"] in ("pass", "partial", "fail"), result
assert result["criteria"], "the reviewer returned no per-criterion verdict"
print("\nOK — Check ran in a fresh process and is recorded on the card as", result["recorded"])
