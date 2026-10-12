"""manu — Plan / Do / Check / Close over the Hermes kanban board.

Hybrid by design, and the split is the point:

  * the `manu` SKILL.md holds the process CONTENT — the four phases, the
    acceptance-criteria shape, the rework rule, the failure triage. It is read
    by the model, like BMAD's workflow docs or a Superpowers skill.
  * THIS PLUGIN holds the four things a skill cannot do: gates on live state.
    A skill can only ask; a hook can refuse.

Gates, in the order a turn meets them:

  pre_llm_call   PLAN ENTRY. A work-shaped request with no card open gets a
                 `triage` card created for the session, and one `[manu]` line
                 injected telling the model to write the criteria, claim, and
                 build. Quick questions: zero cards, zero text, zero friction.
                 While a card is open this hook just recites its phase.
  pre_tool_call  PLAN LOCK: file writes are refused while this session's card
                 is still in Plan (no criteria written, not claimed).
                 CHECK: a card cannot be closed before it passed review.
                 CLOSE: a card cannot be closed without evidence.

The card status IS the phase — nothing new is stored, and everything else
(dispatch, review handoff, PR CI contracts, retry budget, escalation back to
triage) is core Hermes, reused untouched.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sqlite3
from pathlib import Path
from typing import Any, Optional, Tuple

# Statuses that mean "a card is live for someone" -> the recitation is on.
OPEN = ("triage", "todo", "scheduled", "ready", "running", "blocked", "review")
#: Plan phase: no work may touch files while this session's card sits here.
PLAN = ("triage", "todo", "scheduled", "ready")

#: status -> phase name printed to the model.
PHASE = {
    "triage": "Plan",
    "todo": "Plan",
    "ready": "Plan",
    "scheduled": "Plan",
    "blocked": "Plan (waiting for a human)",
    "running": "Do",
    "review": "Check",
}

#: Tools that write to disk. Reads/searches are deliberately never gated.
ARTIFACTS = ("write_file", "patch")

#: kanban lifecycle events that record a Check pass or a rework handoff.
_REVIEW_EVENTS = ("review_requested", "changes_requested", "review_reopened")

#: Word stems meaning "this produces an artifact or spans steps" (IT + EN).
#: ponytail: a word list, not a classifier — swap `looks_like_work` for one
#: auxiliary call if it keeps misfiring; the seam is that single function.
WORK_SIGNALS = (
    "implement", "refactor", "riscr", "rewrit", "build", "crea", "costruisci",
    "ricerca", "research", "analis", "analy", "report", "document", "script",
    "migra", "integrat", "automat", "configur", "setup", "set up", "deploy",
    "progetta", "design", "ottimizz", "optimi", "debug", "sistem", "confronta",
    "compare", "valuta", "review", "audit", "scriv", "write", "genera",
    "generate", "make me", "help me", "add ", "aggiungi", "aggiorna", "update",
    "installa", "pianifica",
)
#: A message longer than this is treated as work even without a signal word.
WORK_MIN_CHARS = 200
#: A short question is a lookup; it never opens a card.
QUESTION_MAX_CHARS = 140

#: Gateway-generated text that arrives in the user role but is never a user
#: request: delegation results and system notices. Without this the gate opens a
#: card for a message nobody sent (observed 3x on 2026-10-12).
#: ponytail: marker list; extend it if a new internal sender appears.
_INTERNAL_MARKERS = ("[internal notification", "[async delegation batch complete")

#: ``hermes [-p x] kanban complete <ids> ...`` as its own command segment.
#: ponytail: a heredoc that merely *mentions* the command can trip this; the
#: block message tells the model how to pass ``--result`` and move on.
_COMPLETE_CMD = re.compile(
    r"(?:^|[;&|]\s*)(?:\S*/)?hermes(?:\s+[-\w=.:/@]+)*\s+kanban\s+complete\b(?P<rest>[^;&|]*)"
)

#: Idempotency-key prefix for cards this plugin opens itself.
_KEY_PREFIX = "manu:"


def _db_path(board: Optional[str] = None) -> Path:
    """Board-aware kanban.db path, reusing the core resolver."""
    from hermes_cli.kanban_db import kanban_db_path

    return Path(kanban_db_path(board=board))


def _read_conn(db=None, board: Optional[str] = None) -> Optional[sqlite3.Connection]:
    path = Path(db) if db is not None else _db_path(board)
    if not path.exists():
        return None
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _session_card(
    session_id: Optional[str], statuses: Tuple[str, ...], db=None, board: Optional[str] = None
) -> Optional[Tuple[str, str, str]]:
    """Newest card bound to this session in one of *statuses*, else None.

    Only cards the session itself owns: an unbound card (``hermes kanban
    create`` writes no session_id) must not unlock or lock another session.
    """
    if not session_id:
        return None
    conn = None
    try:
        conn = _read_conn(db, board)
        if conn is None:
            return None
        row = conn.execute(
            "select id, title, status from tasks where session_id = ? and status in "
            f"({','.join('?' * len(statuses))}) order by created_at desc limit 1",
            (session_id, *statuses),
        ).fetchone()
        return (row[0], row[1], row[2]) if row else None
    except Exception:
        return None
    finally:
        if conn is not None:
            conn.close()


def recite(session_id: Optional[str], db=None, board: Optional[str] = None) -> Optional[str]:
    """One line of board state for this session, or None when idle.

    A card bound to this session wins; an unbound card (``session_id IS NULL``,
    which is what ``hermes kanban create`` produces — it has no --session flag)
    is recited by the session that is active. Never raises: a hook that throws
    would break the turn it is attached to.
    """
    if not session_id:
        return None
    conn = None
    try:
        conn = _read_conn(db, board)
        if conn is None:
            return None
        placeholders = ",".join("?" * len(OPEN))
        row = conn.execute(
            "select id, title, status from tasks "
            f"where (session_id = ? or session_id is null) and status in ({placeholders}) "
            "order by created_at desc limit 1",
            (session_id, *OPEN),
        ).fetchone()
    except Exception:
        return None
    finally:
        if conn is not None:
            conn.close()
    if not row:
        return None
    task_id, title, status = row
    return (
        f'[manu] card #{task_id} "{title}" — phase {PHASE.get(status, status)}. '
        "Close it only with evidence."
    )


def _evidence(task_id: str, db=None, board: Optional[str] = None) -> bool:
    """True when the card carries evidence: an attachment, or a stored result."""
    conn = None
    try:
        conn = _read_conn(db, board)
        if conn is None:
            return False
        attachments = conn.execute(
            "select count(*) from task_attachments where task_id = ?", (task_id,)
        ).fetchone()[0]
        if attachments:
            return True
        row = conn.execute("select result from tasks where id = ?", (task_id,)).fetchone()
        return bool(row and row[0])
    except Exception:
        return False
    finally:
        if conn is not None:
            conn.close()


def _reviewed(task_id: str, db=None, board: Optional[str] = None) -> bool:
    """True when the card's last Check event was a pass.

    A ``changes_requested`` or ``review_reopened`` after the last
    ``review_requested`` means the rework loop is open: not reviewed.
    """
    conn = None
    try:
        conn = _read_conn(db, board)
        if conn is None:
            return False
        row = conn.execute(
            "select kind from task_events where task_id = ? and kind in "
            f"({','.join('?' * len(_REVIEW_EVENTS))}) order by id desc limit 1",
            (task_id, *_REVIEW_EVENTS),
        ).fetchone()
        return bool(row and row[0] == "review_requested")
    except Exception:
        return False
    finally:
        if conn is not None:
            conn.close()


def looks_like_work(message: Optional[str]) -> bool:
    """Cheap a-priori read of the request: deliverable, or chat reply?

    The question the plugin can answer before any action exists — and the one
    the model cannot answer reliably at that same moment.
    """
    text = " ".join((message or "").lower().split())
    if len(text) < 20:
        return False
    if any(marker in text for marker in _INTERNAL_MARKERS):
        return False
    if text.endswith("?") and len(text) < QUESTION_MAX_CHARS and not any(
        s in text for s in WORK_SIGNALS
    ):
        return False
    if any(s in text for s in WORK_SIGNALS):
        return True
    return len(text) > WORK_MIN_CHARS


def _in_worker() -> bool:
    """True inside a kanban worker / cron run / delegated child: never re-enter."""
    return any(
        os.environ.get(key)
        for key in ("HERMES_KANBAN_TASK", "HERMES_CRON_SESSION", "HERMES_DELEGATED_CHILD_CONTEXT")
    )


def _default_assignee() -> Optional[str]:
    """``kanban.default_assignee`` — the value the dispatcher would apply anyway.

    A card created without an assignee is never claimed by dispatch, so the
    plugin writes the same default the board would show for a hand-made card.
    """
    try:
        from hermes_cli.config import load_config

        return ((load_config().get("kanban") or {}).get("default_assignee") or "").strip() or None
    except Exception:
        return None


def _create_card(session_id: str, message: str, db=None, board: Optional[str] = None) -> Optional[str]:
    """Open the Plan card for this session; returns its id.

    ``triage`` because the criteria are not written yet — writing them is the
    Plan step the injected line asks for. ``idempotency_key`` is per session and
    per request ordinal, so a re-entry in the same session reuses the card
    instead of stacking duplicates.
    """
    from hermes_cli.kanban_db import create_task
    from hermes_cli.kanban_db_connect import connect

    title = " ".join((message or "").strip().split())[:80] or "untitled request"
    conn = connect(Path(db) if db is not None else None, board=board)
    try:
        key = f"{_KEY_PREFIX}{session_id}:"
        ordinal = conn.execute(
            "select count(*) from tasks where idempotency_key like ?", (key + "%",)
        ).fetchone()[0]
        return create_task(
            conn,
            title=title,
            body=message,
            triage=True,
            assignee=_default_assignee(),
            session_id=session_id,
            idempotency_key=f"{key}{ordinal + 1}",
        )
    finally:
        conn.close()


_PLAN_HINT = (
    "Plan first: the acceptance criteria are what Check runs against. Call the "
    "`manu_specify` tool (or write them into the card body), then "
    "`hermes kanban claim {task_id}` and retry this write."
)


def _entry_hook(
    user_message: Optional[str] = None,
    session_id: Optional[str] = None,
    _db=None,
    _create=None,
    **kwargs,
) -> Optional[dict]:
    """pre_llm_call: open the card before the first action, or recite the phase.

    Silent on the fast path: a quick question produces no card and no text.
    """
    if not session_id or _in_worker():
        return None
    try:
        if _session_card(session_id, OPEN, db=_db):
            line = recite(session_id, db=_db)
            return {"context": line} if line else None
        if not looks_like_work(user_message):
            return None
        task_id = _create(session_id, user_message or "") if _create else _create_card(
            session_id, user_message or "", db=_db)
    except Exception:
        return None
    if not task_id:
        return None
    return {
        "context": (
            f"[manu] Plan: card {task_id} opened for this request. Acceptance criteria "
            f"first — call `manu_specify(task_id=\"{task_id}\")` or write them in the card "
            f"body — then `hermes kanban claim {task_id}` and build. Close it only with "
            "evidence, and only after it passed Check."
        )
    }


def _ids_from_command(command: str) -> Tuple[list, bool]:
    """Task ids named by every ``hermes kanban complete`` in the command, plus
    whether an inline ``--result``/``--summary``/``--metadata`` was supplied.

    shlex handles the quoting; flag *values* are skipped so a summary string
    containing digits is never mistaken for a task id.
    """
    value_flags = ("--result", "--summary", "--metadata")
    ids: list = []
    inline = False
    for match in _COMPLETE_CMD.finditer(command):
        try:
            tokens = shlex.split(match.group("rest"))
        except ValueError:  # unbalanced quotes: fall back to a naive split
            tokens = match.group("rest").split()
        i = 0
        while i < len(tokens):
            token = tokens[i]
            if token in value_flags:
                inline = True
                i += 2
                continue
            if token.split("=")[0] in value_flags:
                inline = True
                i += 1
                continue
            if not token.startswith("-"):
                for part in token.split(","):
                    part = part.strip(".;:!?'\"()[]")
                    if part:
                        ids.append(part)
            i += 1
    return ids, inline


def gate(
    tool_name: str,
    args: dict,
    _lookup=None,
    _plan_lookup=None,
    _reviewed_lookup=None,
    session_id: Optional[str] = None,
    _db=None,
    **kwargs,
) -> Optional[dict]:
    """The three pre_tool_call refusals: Plan lock, Check, Close.

    ``_db`` is the single injection seam: the default lookups read the same
    board the entry hook wrote to. ponytail: presence checks only — evidence
    *quality* and criteria quality are the reviewer's job, and a human running
    the CLI from their own shell bypasses this on purpose.
    """
    if tool_name in ARTIFACTS:
        if not session_id:
            return None
        plan = _plan_lookup(session_id) if _plan_lookup else _session_card(session_id, PLAN, db=_db)
        if not plan:
            return None
        task_id, title = plan[0], plan[1]
        return {
            "action": "block",
            "message": (
                f'manu: card {task_id} "{title}" is in Plan. '
                + _PLAN_HINT.format(task_id=task_id)
            ),
        }

    ids: list = []
    inline = False
    if tool_name == "kanban_complete":
        task_ids = args.get("task_ids") or ([args["task_id"]] if args.get("task_id") else [])
        ids = [str(t) for t in task_ids]
        inline = bool(args.get("result") or args.get("summary") or args.get("metadata"))
    elif tool_name == "terminal":
        command = args.get("command") or ""
        if isinstance(command, str) and "kanban complete" in command:
            ids, inline = _ids_from_command(command)
    if not ids:
        return None

    reviewed = _reviewed_lookup or (lambda tid: _reviewed(tid, db=_db))
    unreviewed = [t for t in ids if not reviewed(t)]
    if unreviewed:
        return {
            "action": "block",
            "message": (
                f"manu: card(s) {', '.join(unreviewed)} have not passed Check. Record the "
                "review first — `hermes kanban request-review <id> --summary \"what was "
                "built + what was verified\"` — then close."
            ),
        }

    if inline:
        return None
    lookup = _lookup or (lambda tid: _evidence(tid, db=_db))
    missing = [t for t in ids if not lookup(t)]
    if not missing:
        return None
    return {
        "action": "block",
        "message": (
            f"manu: card(s) {', '.join(missing)} have no evidence. Attach the command "
            "output or the artifact (`hermes kanban attach <id> <file>`), or pass "
            '`--result "..."`, then close.'
        ),
    }


_SPECIFY_SCHEMA = {
    "name": "manu_specify",
    "description": (
        "Plan step: turn the open card into Goal / Approach / Acceptance criteria / Out of "
        "scope, retitle it and promote it out of `triage`. Run it before claiming the card — "
        "the criteria it writes are what Check verifies against. Returns JSON. Use this "
        "instead of the `hermes kanban specify` CLI: that command runs in its own process "
        "with no conversation context, so a relay provider that needs a session header "
        "(OpenCode) answers HTTP 400 MissingSessionID. This runs the same specifier in-process."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "Card id of a card in `triage` (from `hermes kanban list`).",
            }
        },
        "required": ["task_id"],
    },
}


def specify(task_id: str) -> dict:
    """Run the auxiliary specifier in this process and promote the card.

    Returns a dict, never raises: the caller is a tool handler.
    """
    task_id = str(task_id or "").strip()
    if not task_id:
        return {"success": False, "error": "task_id is required"}
    try:
        from hermes_cli.kanban_specify import specify_task

        outcome = specify_task(task_id)
    except Exception as exc:
        return {"success": False, "task_id": task_id, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "success": bool(outcome.ok),
        "task_id": outcome.task_id,
        "reason": outcome.reason,
        "new_title": outcome.new_title,
    }


def _specify_handler(args: dict, **kwargs) -> str:
    return json.dumps(specify((args or {}).get("task_id")))


def register(ctx: Any) -> None:
    ctx.register_hook("pre_llm_call", _entry_hook)
    ctx.register_hook("pre_tool_call", gate)
    ctx.register_tool(
        name="manu_specify",
        toolset="manu",
        schema=_SPECIFY_SCHEMA,
        handler=_specify_handler,
        description="Write the acceptance criteria of a triage card in-process (Plan phase)",
    )
