"""Tests for the manu plugin. Run: venv/bin/python -m pytest test_manu.py -q

Loaded by file path so the flat plugin layout (plugin.yaml + __init__.py) is
tested exactly as Hermes loads it, with no package/name assumptions.
"""

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_PLUGIN = Path(__file__).parent / "__init__.py"
# The plugin imports core modules (hermes_cli.kanban_db*) on the real create path;
# at runtime that tree is always importable, under pytest it must be added by hand.
sys.path.insert(0, str(Path.home() / ".hermes" / "hermes-agent"))
_spec = importlib.util.spec_from_file_location("manu_plugin", _PLUGIN)
manu = importlib.util.module_from_spec(_spec)
sys.modules["manu_plugin"] = manu
_spec.loader.exec_module(manu)


@pytest.fixture(autouse=True)
def _clean_worker_env(monkeypatch):
    """The suite must be green in a clean shell *and* inside a delegated child.

    `_in_worker()` short-circuits the entry hook when HERMES_DELEGATED_CHILD_CONTEXT /
    HERMES_KANBAN_TASK / HERMES_CRON_SESSION are set, which is exactly the case when a
    subagent (or a worker) runs this file — 7 tests then fail for no code reason. The one
    test that exercises those vars sets them itself, after this fixture clears them.
    """
    for key in ("HERMES_KANBAN_TASK", "HERMES_CRON_SESSION", "HERMES_DELEGATED_CHILD_CONTEXT"):
        monkeypatch.delenv(key, raising=False)


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


def test_classifier_reads_english_requests():
    """The English class the corpus could not see (2026-10-13).

    The word lists were tuned on Italian traffic: bare English stems were missing
    ("analysis" is not "analis", "install" is not "installa") and the English verbs
    that *were* in the ask list are the same word in a wh-question. A real request
    — rank the OpenCode Go models — opened no card for exactly this reason.
    """
    for request in (
        # reported case, verbatim: no card was opened for it
        "Make an analysis trying to order the models provided in Opencode go subscription "
        "https://opencode.ai/v2/docs/console/go/\n\nYou have to find benchmarks, other data, "
        "community data, to reorder this table in order of most capable to least capable.",
        "Make an analysis of the pricing table and reorder it",
        "Install the grill-me skill and wire it into manu",
        "Install this plugin on the Pi",
        "Verify the evidence attached to the card",
        "Please verify the sha256 of the artifact",
        "Remove the stale entry from memory",
        "Delete the archived cards",
        "Translate the README into Italian",
        # a question is still an ask when it carries a request opener
        "Can you check the logs from last night?",
        "Could you verify whether the card passed review?",
    ):
        assert manu.looks_like_work(request), request


def test_classifier_ignores_a_wh_question_about_work():
    """A question *about* the work is not the work: the same verbs must not fire.

    These flipped to true as soon as `create`/`write`/`review` sat in the ask list,
    which is why the ask list holds imperative forms only.
    """
    for question in (
        "Why did no card get created for that request?",
        "How does the gate decide to refuse a write?",
        "What is the best coding model right now?",
        "Which model has the best price per token?",
        "Is the manu plugin enabled?",
        "ho ricontrollato a mano i nostri file User.md e memory.md, non hanno le informazioni "
        "che ti semplificherebbero la vita... come li riscriveresti?",
    ):
        assert not manu.looks_like_work(question), question


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

    # Live false positive (2026-10-16): a terminal background=true completion
    # notice arrived in the user role, classified as work, and opened a card
    # nobody asked for. This is the exact text that did it.
    background_done = (
        "[IMPORTANT: Background process proc_8bd401711a2c completed normally (exit code 0).\n"
        "Command: for i in $(seq 1 12); do st=$(hermes kanban show t_acdef994 2>&1 | awk '/^  status:/{print $2}'); "
        'echo "poll $i: status=$st"; case "$st" in done|blocked|review|ready) break;; esac; sleep 45; done; '
        'ps -eo pid,etime,args | grep "work kanban task t_acdef994" | grep -v grep || echo "reviewer process finished"\n'
        "Output:\npoll 1: status=running\n...\npoll 12: status=running\n"
        " 492633       10:49 /home/ema/.hermes/tools/python-3.14.7/bin/python3 -m hermes_cli.main -p default --cli "
        "--accept-hooks --skills sdlc-review --toolsets browser,... chat -q work kanban task t_acdef994\n]"
    )
    background_failed = (
        "[IMPORTANT: Background process proc_1a2b3c4d failed with exit code 1. "
        "Command: node test/pricing.test.mjs\nOutput:\n  FAIL inverted stay — expected 0, got 1\n"
    )
    assert not manu.looks_like_work(background_done)
    assert not manu.looks_like_work(background_failed)


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


def test_gate_does_not_turn_a_result_string_into_phantom_ids():
    """Observed on 2026-10-13: the close was refused with the *words* of the
    result string reported as cards whose Check was missing.

    The value of `--result` was unquoted by the time the gate saw the command, so
    one token became `--result manu v0.4.1 shipped …` and the old `i += 2` skipped
    only its first word; every other word was read as a task id. Same for the
    `2>` left by `2>&1 | tail`, which is a redirect, not a card.
    """
    ids, inline = manu._ids_from_command(
        "cd ~ && hermes kanban attach t_286bb29e f.md 2>&1 | tail -1; "
        "hermes kanban complete t_286bb29e --result manu v0.4.1 shipped and pushed 2>&1 | tail -2"
    )
    assert ids == ["t_286bb29e"], ids
    assert inline is True
    ids, inline = manu._ids_from_command(
        'hermes kanban complete t_286bb29e --result "manu v0.4.1, shipped" 2>&1 | tail -2'
    )
    assert ids == ["t_286bb29e"] and inline is True, (ids, inline)
    # ...and the pre-existing behaviour still holds
    assert manu._ids_from_command("hermes kanban complete t_a, t_b --result x")[0] == ["t_a", "t_b"]


# --- dispatched workers (v0.5.0): the gates bind via HERMES_KANBAN_TASK -----


def test_gate_binds_worker_completes_via_env(monkeypatch):
    """Observed 2026-10-13 (probe B): a dispatched worker's `kanban_complete`
    carries no task id — it defaults from HERMES_KANBAN_TASK — so the gate read
    no ids and the Check/Close refusals never fired: the worker closed an
    unreviewed card. The env fallback is what makes the gates hold in workers."""
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_w0rk")
    reason = manu.gate("kanban_complete", {"summary": "done"},
                       _reviewed_lookup=lambda t: False)
    assert reason and "not passed Check" in reason["message"], reason
    reason = manu.gate("kanban_complete", {}, _reviewed_lookup=lambda t: True,
                       _lookup=lambda tid: False)
    assert reason and "no evidence" in reason["message"], reason
    # the reviewer's own approval passes (reviewed), explicit ids still win over
    # the env, and outside a worker the hook stays silent exactly as before
    assert manu.gate("kanban_complete", {"task_ids": ["1"]},
                     _reviewed_lookup=lambda t: True, _lookup=lambda t: True) is None
    monkeypatch.delenv("HERMES_KANBAN_TASK")
    assert manu.gate("kanban_complete", {"summary": "x"},
                     _reviewed_lookup=lambda t: False) is None


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


def test_review_refuses_an_unparseable_reply_and_records_nothing(tmp_path):
    """A reply cut off mid-JSON is not a Check: the gate must not accept it.

    Observed live: ten long criteria made the reviewer echo them and hit the token
    ceiling, and the truncated JSON was recorded as a pass. Now it is refused.
    """
    path, task_id, _ = _board(tmp_path)
    truncated = _REVIEW_REPLY[: len(_REVIEW_REPLY) // 2]
    r = manu.review(task_id, db=path, _llm=lambda _u: (truncated, "stub-model"))
    assert r["success"] is False and "not a JSON verdict" in r["error"], r
    assert r["raw"].startswith('{"criteria"')
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc

    with kbc.connect_closing(path) as conn:
        status = kb.get_task(conn, task_id).status
        events = conn.execute(
            "select count(*) from task_events where task_id = ? and kind = 'review_requested'",
            (task_id,),
        ).fetchone()[0]
    assert status == "running" and events == 0, (status, events)


def test_review_tolerates_prose_around_a_valid_json_verdict(tmp_path):
    """A fence or a sentence before the object is fine — the JSON is what is read."""
    path, task_id, _ = _board(tmp_path)
    chatty = f"Here is my verdict:\n```json\n{_REVIEW_REPLY}\n```\n"
    r = manu.review(task_id, db=path, _llm=lambda _u: (chatty, "stub-model"))
    assert r["success"] is True and r["verdict"] == "partial", r


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


# --- prompt texts live in prompts/*.md ------------------------------------

_PROMPT_NAMES = (
    "entry_plan", "recite", "plan_lock", "close_check", "close_evidence",
    "review_system", "review_user",
)


def test_every_prompt_manu_asks_for_exists_on_disk():
    for name in _PROMPT_NAMES:
        f = manu._PROMPT_DIR / f"{name}.md"
        assert f.is_file(), f
        assert manu.prompt_text(name).strip(), name


def test_prompt_renders_known_placeholders():
    line = manu.prompt_text("recite", task_id="t_1", title="T", phase="Do", skill_hint="S.")
    assert "#t_1" in line and '"T"' in line and "Do" in line and "S." in line
    assert "{" not in line, line


def test_prompt_keeps_unknown_placeholders_literal():
    partial = manu.prompt_text("recite", task_id="t_1")
    assert "#t_1" in partial and "{title}" in partial, partial


def test_prompt_with_code_braces_is_not_formatted():
    # review_system.md carries a literal JSON example: its braces must survive.
    body = manu.prompt_text("review_system")
    assert '{"criteria"' in body, body


def test_missing_prompt_degrades_instead_of_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(manu, "_PROMPT_DIR", tmp_path / "gone")
    assert manu.prompt_text("close_evidence").startswith("manu: refused")
    assert manu.prompt_text("plan_lock").startswith("manu: refused")
    assert manu.prompt_text("recite") == ""      # a hook goes quiet, no crash
    assert manu.prompt_text("entry_plan") == ""


def test_recite_names_the_phase_skills(tmp_path):
    db = _fake_db(tmp_path)
    assert "test-driven-development" in manu.recite("s1", db=db)
    quiet = _fake_db(tmp_path / "quiet", status="blocked")
    assert "Skills:" not in manu.recite("s1", db=quiet)


def test_plugin_source_holds_no_copy_of_the_prompt_text():
    src = _PLUGIN.read_text(encoding="utf-8")
    for literal in ("Acceptance criteria first", "have not passed Check",
                    "You are the independent Check"):
        assert literal not in src, literal
