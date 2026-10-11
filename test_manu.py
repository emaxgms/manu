"""Tests for the manu plugin. Run: venv/bin/python -m pytest test_manu.py -q

Loaded by file path so the flat plugin layout (plugin.yaml + __init__.py) is
tested exactly as Hermes loads it, with no package/name assumptions.
"""

import importlib.util
import sqlite3
import sys
from pathlib import Path

_PLUGIN = Path(__file__).parent / "__init__.py"
_spec = importlib.util.spec_from_file_location("manu_plugin", _PLUGIN)
manu = importlib.util.module_from_spec(_spec)
sys.modules["manu_plugin"] = manu
_spec.loader.exec_module(manu)


def _fake_db(tmp, session="s1", status="running", attachments=0, result=None):
    tmp = Path(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    path = tmp / "kanban.db"
    con = sqlite3.connect(path)
    con.execute(
        "create table tasks (id text, title text, status text, session_id text, "
        "created_at integer, result text)"
    )
    con.execute("create table task_attachments (id integer primary key, task_id text)")
    con.execute(
        "insert into tasks values ('1','Report X',?,?,1,?)", (status, session, result)
    )
    for _ in range(attachments):
        con.execute("insert into task_attachments (task_id) values ('1')")
    con.commit()
    con.close()
    return path


# --- recitation -----------------------------------------------------------


def test_recite_open_card(tmp_path):
    db = _fake_db(tmp_path)
    line = manu.recite("s1", db=db)
    assert line and "Report X" in line and "Do" in line, line


def test_recite_review_is_check(tmp_path):
    db = _fake_db(tmp_path, status="review")
    assert "Check" in manu.recite("s1", db=db)


def test_recite_nothing_when_idle(tmp_path):
    db = _fake_db(tmp_path, session="other")
    assert manu.recite("s1", db=db) is None
    assert manu.recite(None, db=db) is None
    assert manu.recite("s1", db=tmp_path / "missing.db") is None


def test_recite_unbound_card(tmp_path):
    # `hermes kanban create` records no session_id -> the active session recites it.
    db = _fake_db(tmp_path, session=None)
    assert "Report X" in manu.recite("s1", db=db)


def test_recite_ignores_closed_card(tmp_path):
    db = _fake_db(tmp_path, status="done")
    assert manu.recite("s1", db=db) is None


# --- evidence gate --------------------------------------------------------


def test_gate_blocks_empty_close():
    reason = manu.gate("kanban_complete", {"task_ids": ["1"]}, _lookup=lambda tid: False)
    assert reason and reason["action"] == "block", reason
    assert "1" in reason["message"]


def test_gate_allows_evidenced_close():
    assert (
        manu.gate(
            "kanban_complete", {"task_ids": ["1"], "result": "gate: green"}, _lookup=lambda t: False
        )
        is None
    )
    assert manu.gate("kanban_complete", {"task_ids": ["1"]}, _lookup=lambda t: True) is None


def test_gate_ignores_other_tools():
    assert manu.gate("write_file", {"path": "x"}, _lookup=lambda t: False) is None
    assert manu.gate("terminal", {"command": "hermes kanban list"}, _lookup=lambda t: False) is None


def test_gate_blocks_cli_close_without_evidence():
    reason = manu.gate(
        "terminal",
        {"command": "cd ~ && hermes -p default kanban complete 7 && echo ok"},
        _lookup=lambda t: False,
    )
    assert reason and reason["action"] == "block", reason
    assert "7" in reason["message"]


def test_gate_allows_cli_close_with_result():
    assert (
        manu.gate(
            "terminal",
            {"command": 'hermes kanban complete 7 --result "tests: 12 passed"'},
            _lookup=lambda t: False,
        )
        is None
    )


def test_gate_ignores_punctuation_around_ids():
    ids, inline = manu._ids_from_command("hermes kanban complete t_ab80c5a4 . && echo done")
    assert ids == ["t_ab80c5a4"], ids
    assert inline is False
    ids, inline = manu._ids_from_command("hermes kanban complete 3,4 --summary 'ok'")
    assert ids == ["3", "4"] and inline is True, (ids, inline)


def test_evidence_reads_real_db(tmp_path):
    assert manu._evidence("1", db=_fake_db(tmp_path / "a")) is False
    assert manu._evidence("1", db=_fake_db(tmp_path / "b", attachments=1)) is True
    assert manu._evidence("1", db=_fake_db(tmp_path / "c", result="done")) is True


# --- specify tool (Plan step without the CLI) -----------------------------


def test_specify_requires_task_id():
    r = manu.specify("")
    assert r["success"] is False and "task_id" in r["error"]


def test_specify_maps_the_outcome(monkeypatch):
    import types
    import sys

    fake = types.ModuleType("hermes_cli.kanban_specify")

    class _Outcome:
        ok = True
        task_id = "t_1"
        reason = "specified"
        new_title = "Retitled"

    fake.specify_task = lambda tid: _Outcome()
    monkeypatch.setitem(sys.modules, "hermes_cli.kanban_specify", fake)
    assert manu.specify("t_1") == {
        "success": True, "task_id": "t_1", "reason": "specified", "new_title": "Retitled",
    }


def test_specify_survives_an_import_error(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "hermes_cli.kanban_specify", None)
    r = manu.specify("t_1")
    assert r["success"] is False and "error" in r


def test_register_wires_hooks_and_tool():
    seen = {"hooks": [], "tools": []}

    class _Ctx:
        def register_hook(self, name, cb):
            seen["hooks"].append(name)

        def register_tool(self, **kw):
            seen["tools"].append(kw["name"])

    manu.register(_Ctx())
    assert seen["hooks"] == ["pre_llm_call", "pre_tool_call",
                             "kanban_task_claimed", "kanban_task_completed"], seen
    assert seen["tools"] == ["manu_specify"], seen


# --- phase map ------------------------------------------------------------


def test_phase_map_covers_every_open_status():
    assert set(manu.PHASE) == set(manu.OPEN)
    assert manu.PHASE["running"] == "Do" and manu.PHASE["review"] == "Check"
