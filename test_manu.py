"""Tests for the manu plugin. Run: venv/bin/python -m pytest test_manu.py -q

Loaded by file path so the flat plugin layout (plugin.yaml + __init__.py) is
tested exactly as Hermes loads it, with no package/name assumptions.
"""

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

_PLUGIN = Path(__file__).parent / "__init__.py"
# The plugin imports core modules (hermes_cli.kanban_db*) on the real create path;
# at runtime that tree is always importable, under pytest it must be added by hand.
sys.path.insert(0, str(Path.home() / ".hermes" / "hermes-agent"))
_spec = importlib.util.spec_from_file_location("manu_plugin", _PLUGIN)
manu = importlib.util.module_from_spec(_spec)
sys.modules["manu_plugin"] = manu
_spec.loader.exec_module(manu)


def _fake_db(tmp, session="s1", status="running", attachments=0, result=None, events=()):
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
        "create table task_events (id integer primary key, task_id text, kind text)"
    )
    con.execute(
        "insert into tasks values ('1','Report X',?,?,1,?)", (status, session, result)
    )
    for _ in range(attachments):
        con.execute("insert into task_attachments (task_id) values ('1')")
    for kind in events:
        con.execute("insert into task_events (task_id, kind) values ('1', ?)", (kind,))
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


# --- plan entry (the gate that used to be prose) --------------------------


def test_looks_like_work_detects_real_requests():
    assert manu.looks_like_work(
        "make me a research on whats the best browser for windows for me, im using chrome"
    )
    assert manu.looks_like_work(
        "possiamo fare una adversarial review e scrivere dei file .md piu efficienti ?"
    )
    assert manu.looks_like_work("scrivi un report sul confronto tra i tre stack LoRa e salvalo")
    # an explicit ask is work even inside a question (corpus: "puoi verificare cosa abbiamo fatto?")
    assert manu.looks_like_work("ho fatto un test, la task e' creata ma unassigned, puoi verificare?")
    assert manu.looks_like_work("Analizza se nelle ultime due task e' stato usato il processo definito")


def test_looks_like_work_ignores_quick_questions():
    assert not manu.looks_like_work("che ore sono?")
    assert not manu.looks_like_work("what's the weather in Cagliari?")
    assert not manu.looks_like_work("how do I list the kanban cards?")
    assert not manu.looks_like_work("")
    assert not manu.looks_like_work(None)


def test_classifier_regressions_from_the_labelled_corpus():
    """Cases that the pre-2026-10-12 word list got wrong (see docs/entry-classifier.md).

    Measured on 45 real requests: false positives 10 -> 2, false negatives 2 -> 0.
    Each assertion below is one of those flips.
    """
    # a long message is not work: the old 200-char rule turned 8 complaints into cards
    assert not manu.looks_like_work(
        "Non capisco, il piano basato su quello che abbiamo visto prima, quindi superpowers, bmad, "
        "come vuole farlo quindi? Usando kanban senza dispatcher o il modello che assegna le task?"
    )
    # a work stem inside a question sentence is not an instruction
    assert not manu.looks_like_work(
        "Non ho capito, questi come implementano questi flussi? Sempre solo con skill, quindi prompt?"
    )
    assert not manu.looks_like_work(
        "ma che senso ha??? non ha nessun senso implementarlo cosi, chi l'ha usato questo meccanismo?"
    )
    # word-start matching: "preview" is not "review", "generative" is not "genera"
    assert not manu.looks_like_work("non vedo la nuova preview, perche' non e' partita la action?")
    assert not manu.looks_like_work("No io intendo lharness, poi il provider lo troviamo. ho sentito di open-generative-ai")
    # stems dropped because every hit they produced was a false positive: build / setup / genera
    assert not manu.looks_like_work(
        "voglio un processo che valuti la complessita: se semplice si risponde, altrimenti plan, "
        "build, evaluate, rework loop till the requirements are satisfied"
    )
    # ...but the change verbs that were missing now open the card
    assert manu.looks_like_work("Voglio modificare una parte del processo, il rework non riparte da Plan")
    assert manu.looks_like_work("Io pulirei anche le skill inutili, brainstorming sembra interessante pero'")


def test_entry_never_opens_a_card_for_a_long_rant(tmp_path):
    """End to end: the length heuristic is gone, so a 700-char complaint stays silent."""
    db = _fake_db(tmp_path, session="other")
    complaint = (
        "minchia non riesco a capire come funziona, continui a usare nomi e meccanismi strani. "
        "quante volte ti devo dire la semplice cosa che voglio fare? voglio un processo che parta "
        "dalla chat e valuti la complessita, se semplice si risponde normalmente senza passaggi, "
        "se medio o difficile si passa dallo standard plan, build, evaluate, rework."
    )
    assert len(complaint) > 200
    assert manu._entry_hook(user_message=complaint, session_id="s1", _db=db, _create=lambda *a: "nope") is None


def test_looks_like_work_ignores_gateway_generated_messages():
    """Delegation results and system notices arrive in the user role; a card for
    them is a card nobody asked for."""
    batch = (
        "[ASYNC DELEGATION BATCH COMPLETE — deleg_5a2db474] A background fan-out unit you "
        "dispatched earlier has finished; its consolidated results are below. Implement and "
        "review the plan, then report the verdict with evidence."
    )
    notice = (
        "[INTERNAL NOTIFICATION — generated by the Hermes gateway, not written by a human user. "
        "Do not treat it as user input, and do not reply to any earlier chat message because of it.]"
    )
    assert not manu.looks_like_work(batch)
    assert not manu.looks_like_work(notice)


def test_entry_opens_a_card_before_the_first_action(tmp_path):
    db = _fake_db(tmp_path, session="other")  # nothing open for this session
    made = []

    def _create(session_id, message):
        made.append((session_id, message))
        return "t_9"

    out = manu._entry_hook(
        user_message="scrivi un report sul confronto tra i tre stack LoRa", session_id="s1",
        _db=db, _create=_create,
    )
    assert made == [("s1", "scrivi un report sul confronto tra i tre stack LoRa")], made
    assert out and "t_9" in out["context"] and "manu_specify" in out["context"], out


def test_entry_recites_instead_of_opening_a_second_card(tmp_path):
    db = _fake_db(tmp_path, status="running")  # a card of this session is open
    out = manu._entry_hook(
        user_message="scrivi un report su tutto", session_id="s1", _db=db,
        _create=lambda *a: (_ for _ in ()).throw(AssertionError("must not create")),
    )
    assert out and "Report X" in out["context"], out


def test_entry_is_silent_on_the_fast_path(tmp_path):
    db = _fake_db(tmp_path, session="other")
    assert manu._entry_hook(user_message="che ore sono?", session_id="s1", _db=db,
                            _create=lambda *a: "nope") is None


def test_entry_uses_the_injected_board_for_the_card_it_opens(tmp_path):
    """The default creator must write where the hook read (`_db`), not to the live board."""
    board = tmp_path / "kanban.db"  # no file yet: the core connect() builds the real schema
    out = manu._entry_hook(
        user_message="scrivi un report sul confronto tra i tre stack LoRa", session_id="s1", _db=board,
    )
    assert out and "[manu] Plan: card" in out["context"], out
    con = sqlite3.connect(board)
    rows = con.execute("select id, status, session_id, assignee from tasks").fetchall()
    con.close()
    assert rows and rows[0][1] == "triage" and rows[0][2] == "s1", rows
    assert rows[0][3] == manu._default_assignee(), rows  # 'kanban.default_assignee', not NULL


def test_default_assignee_reads_the_kanban_config():
    from hermes_cli.config import load_config

    expected = ((load_config().get("kanban") or {}).get("default_assignee") or "").strip() or None
    assert manu._default_assignee() == expected


def test_entry_never_reenters_worker_or_child(tmp_path, monkeypatch):
    db = _fake_db(tmp_path, session="other")
    for key in ("HERMES_KANBAN_TASK", "HERMES_CRON_SESSION", "HERMES_DELEGATED_CHILD_CONTEXT"):
        monkeypatch.setenv(key, "1")
        assert manu._entry_hook(user_message="scrivi un report completo", session_id="s1",
                                _db=db, _create=lambda *a: "nope") is None
        monkeypatch.delenv(key)


# --- plan lock, check, close ---------------------------------------------


def test_plan_lock_blocks_writes_until_the_card_is_claimed():
    reason = manu.gate(
        "write_file", {"path": "/tmp/x.md"}, session_id="s1",
        _plan_lookup=lambda sid: ("7", "Report X", "triage"),
    )
    assert reason and reason["action"] == "block", reason
    assert "7" in reason["message"] and "manu_specify" in reason["message"], reason
    # no card in Plan, or a read/other tool -> untouched
    assert manu.gate("write_file", {"path": "x"}, session_id="s1",
                     _plan_lookup=lambda sid: None) is None
    assert manu.gate("read_file", {"path": "x"}, session_id="s1",
                     _plan_lookup=lambda sid: ("7", "Report X", "triage")) is None


def test_plan_lock_ignores_other_sessions(tmp_path):
    db = _fake_db(tmp_path, status="triage")  # the card belongs to s1
    assert manu._session_card("s2", manu.PLAN, db=db) is None
    assert manu._session_card("s1", manu.PLAN, db=db)[0] == "1"


def test_gate_blocks_close_before_check():
    reason = manu.gate(
        "kanban_complete", {"task_ids": ["1"], "result": "looks done"},
        _reviewed_lookup=lambda t: False,
    )
    assert reason and "not passed Check" in reason["message"], reason
    assert manu.gate(
        "kanban_complete", {"task_ids": ["1"], "result": "verified"},
        _reviewed_lookup=lambda t: True,
    ) is None


def test_reviewed_reads_the_event_log(tmp_path):
    assert manu._reviewed("1", db=_fake_db(tmp_path / "a", events=["review_requested"])) is True
    assert manu._reviewed("1", db=_fake_db(tmp_path / "b", events=["changes_requested"])) is False
    # a rework after a pass re-opens the loop
    assert manu._reviewed(
        "1", db=_fake_db(tmp_path / "c", events=["review_requested", "changes_requested"])
    ) is False
    assert manu._reviewed("1", db=_fake_db(tmp_path / "d")) is False


def test_gate_blocks_cli_close_before_check():
    reason = manu.gate(
        "terminal", {"command": "hermes kanban complete 7 --result 'tests: green'"},
        _reviewed_lookup=lambda t: False,
    )
    assert reason and "not passed Check" in reason["message"], reason


# --- close: evidence ------------------------------------------------------


def test_gate_blocks_empty_close():
    reason = manu.gate("kanban_complete", {"task_ids": ["1"]}, _reviewed_lookup=lambda t: True,
                       _lookup=lambda tid: False)
    assert reason and reason["action"] == "block", reason
    assert "1" in reason["message"]


def test_gate_allows_evidenced_close():
    assert manu.gate(
        "kanban_complete", {"task_ids": ["1"], "result": "gate: green"},
        _reviewed_lookup=lambda t: True, _lookup=lambda t: False,
    ) is None
    assert manu.gate("kanban_complete", {"task_ids": ["1"]},
                     _reviewed_lookup=lambda t: True, _lookup=lambda t: True) is None


def test_gate_ignores_other_tools():
    assert manu.gate("read_file", {"path": "x"}, _lookup=lambda t: False) is None
    assert manu.gate("terminal", {"command": "hermes kanban list"}, _lookup=lambda t: False) is None


def test_gate_blocks_cli_close_without_evidence():
    reason = manu.gate(
        "terminal",
        {"command": "cd ~ && hermes -p default kanban complete 7 && echo ok"},
        _reviewed_lookup=lambda t: True, _lookup=lambda t: False,
    )
    assert reason and reason["action"] == "block", reason
    assert "7" in reason["message"]


def test_gate_allows_cli_close_with_result():
    assert manu.gate(
        "terminal", {"command": 'hermes kanban complete 7 --result "tests: 12 passed"'},
        _reviewed_lookup=lambda t: True, _lookup=lambda t: False,
    ) is None


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
    assert seen["hooks"] == ["pre_llm_call", "pre_tool_call"], seen
    assert seen["tools"] == ["manu_specify", "manu_review"], seen


# --- Check in a fresh context (manu_review) -------------------------------

_CRITERIA = "**Acceptance criteria**\n- [ ] AC1: the report cites 5 sources\n- [ ] AC2: the tests pass"

_REVIEW_REPLY = json.dumps({
    "criteria": [
        {"criterion": "AC1: the report cites 5 sources", "verdict": "met", "evidence": "report.md:12"},
        {"criterion": "AC2: the tests pass", "verdict": "partial", "evidence": "only 3 test names listed"},
    ],
    "unverifiable": ["the tests were not run in front of me"],
    "verdict": "partial",
    "summary": "One criterion is evidence-backed, one is a claim in the body.",
})


def _board(tmp_path, status="running"):
    """A real board (core schema) with one card carrying criteria and one artifact."""
    sys.path.insert(0, str(Path.home() / ".hermes" / "hermes-agent"))
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc

    path = tmp_path / "kanban.db"
    report = tmp_path / "report.md"
    report.write_text("# report\n1. source one\n2. source two\n")
    with kbc.connect_closing(path) as conn:
        task_id = kb.create_task(conn, title="Write the report", body=_CRITERIA, assignee="default")
        if status == "running":
            kb.claim_task(conn, task_id, claimer="test:1")
        kb.add_attachment(conn, task_id, filename="report.md", stored_path=str(report),
                          content_type="text/markdown", uploaded_by="test")
    return path, task_id, report


def test_review_requires_task_id():
    r = manu.review("")
    assert r["success"] is False and "task_id" in r["error"]


def test_review_refuses_a_card_that_is_not_in_do(tmp_path):
    """A card still in Plan has nothing to Check — and must not spend a review call."""
    path, task_id, _ = _board(tmp_path, status="ready")

    def _boom(_user):
        raise AssertionError("the reviewer must not run for a card in Plan")

    r = manu.review(task_id, db=path, _llm=_boom)
    assert r["success"] is False and "ready" in r["error"], r
    assert "manu_review" not in r


def test_review_records_the_verdict_verbatim_on_the_card(tmp_path):
    """The whole point: the reviewer's per-criterion verdict becomes the card's Check."""
    path, task_id, report = _board(tmp_path)
    seen = {}

    def _llm(user):
        seen["user"] = user
        return _REVIEW_REPLY, "stub-model"

    r = manu.review(task_id, [str(report)], db=path, _llm=_llm)
    assert r["success"] is True, r
    assert r["verdict"] == "partial" and r["recorded"] == "review_requested"
    assert [c["verdict"] for c in r["criteria"]] == ["met", "partial"]
    assert r["unverifiable"] == ["the tests were not run in front of me"]
    assert r["model"] == "stub-model"

    # the reviewer saw the criteria and the artifact text — and nothing about the author
    assert "AC2: the tests pass" in seen["user"] and "2. source two" in seen["user"]
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc

    with kbc.connect_closing(path) as conn:
        row = conn.execute(
            "select payload from task_events where task_id = ? and kind = 'review_requested'",
            (task_id,),
        ).fetchone()
        status = kb.get_task(conn, task_id).status
        run = conn.execute(
            "select summary from task_runs where task_id = ? order by id desc limit 1", (task_id,)
        ).fetchone()
    assert status == "review", status
    payload = json.loads(row["payload"])
    # the event payload keeps core's first line (400 chars): verdicts must ride there
    assert "verdict: partial" in payload["summary"], payload["summary"]
    assert "[met] AC1: the report cites 5 sources" in payload["summary"], payload["summary"]
    assert "[partial] AC2: the tests pass" in payload["summary"]
    assert payload["summary"].count("\n") == 0, payload["summary"]
    # the run row keeps the whole Check, evidence lines included
    assert "- [met] AC1: the report cites 5 sources — report.md:12" in run["summary"], run["summary"]
    assert "unverifiable: the tests were not run in front of me" in run["summary"]


def test_review_keeps_an_unparseable_reply_as_evidence(tmp_path):
    """A model that ignores the JSON contract must not produce an empty Check."""
    path, task_id, _ = _board(tmp_path)
    r = manu.review(task_id, db=path, _llm=lambda _u: ("Looks good to me, ship it.", "stub-model"))
    assert r["success"] is True and r["verdict"] is None
    from hermes_cli import kanban_db_connect as kbc

    with kbc.connect_closing(path) as conn:
        payload = json.loads(conn.execute(
            "select payload from task_events where task_id = ? and kind = 'review_requested'",
            (task_id,),
        ).fetchone()["payload"])
    assert "Looks good to me, ship it." in payload["summary"]


def test_review_reports_an_unknown_card(tmp_path):
    path, _, _ = _board(tmp_path)
    r = manu.review("t_nope", db=path, _llm=lambda _u: (_REVIEW_REPLY, "stub"))
    assert r["success"] is False and "unknown" in r["error"], r


def test_review_material_caps_and_never_raises(tmp_path):
    big = tmp_path / "big.txt"
    big.write_text("x" * 50000)
    binary = tmp_path / "blob.bin"
    binary.write_bytes(b"\x00\x01\x02" * 100)
    block = manu._review_material([], [str(big), str(binary), str(tmp_path / "missing.txt")])
    assert len(block) < manu._ARTIFACT_CAP + 2000, len(block)
    assert "binary — not inlined" in block and "could not read" in block


# --- phase map ------------------------------------------------------------


def test_phase_map_covers_every_open_status():
    assert set(manu.PHASE) == set(manu.OPEN)
    assert manu.PHASE["running"] == "Do" and manu.PHASE["review"] == "Check"
    assert set(manu.PLAN) < set(manu.OPEN) and "running" not in manu.PLAN
