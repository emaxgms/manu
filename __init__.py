"""manu — a four-phase process layer over the Hermes kanban board.

Phases: Plan -> Do -> Check -> Close, with a rework loop back to Plan.
The phase is NOT stored anywhere new: it IS the card status. This plugin only
adds the three things Hermes does not already do:

  pre_llm_call                 one line of state per turn while a card is open
                               for this session; zero output when idle.
  pre_tool_call                refuse to close a card that has no evidence.
  kanban_task_claimed/_completed
                               mirror the phase into ``current_step_key`` so the
                               dashboard shows the same phase we recite.

Everything else — dispatch, review, rework handoff, PR CI contracts, retry
budget, escalation back to triage — is core Hermes and is reused untouched.
"""

from __future__ import annotations

import re
import shlex
import sqlite3
from pathlib import Path
from typing import Any, Optional, Tuple

# Statuses that mean "a card is live for someone" -> the recitation is on.
OPEN = ("triage", "todo", "scheduled", "ready", "running", "blocked", "review")

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

#: ``hermes [-p x] kanban complete <ids> ...`` as its own command segment.
#: ponytail: a heredoc that merely *mentions* the command can trip this; the
#: block message tells the model how to pass ``--result`` and move on.
_COMPLETE_CMD = re.compile(
    r"(?:^|[;&|]\s*)(?:\S*/)?hermes(?:\s+[-\w=.:/@]+)*\s+kanban\s+complete\b(?P<rest>[^;&|]*)"
)


def _db_path(board: Optional[str] = None) -> Path:
    """Board-aware kanban.db path, reusing the core resolver."""
    from hermes_cli.kanban_db import kanban_db_path

    return Path(kanban_db_path(board=board))


def _read_conn(db=None, board: Optional[str] = None) -> Optional[sqlite3.Connection]:
    path = Path(db) if db is not None else _db_path(board)
    if not path.exists():
        return None
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


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


def gate(tool_name: str, args: dict, _lookup=None, **kwargs) -> Optional[dict]:
    """Refuse to close a card with no evidence attached.

    Covers the two paths that can close a card in v1: the ``kanban_complete``
    tool (dispatcher workers) and ``hermes kanban complete`` typed by the agent
    (in-session work, where dispatch is off). ponytail: presence check only —
    evidence *quality* is the reviewer's job, and a human running the CLI from
    their own shell bypasses this on purpose.
    """
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
    if not ids or inline:
        return None
    lookup = _lookup or _evidence
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


def _set_step(task_id: Optional[str], step: Optional[str], board: Optional[str] = None) -> None:
    """Mirror the phase into the native reserved column.

    ponytail: informational only — the status stays the source of truth and v1
    routing ignores ``current_step_key``. Inert until the dispatcher runs
    (``kanban.dispatch_in_gateway: true``), since these hooks fire in the
    dispatcher/worker processes.
    """
    if not task_id:
        return
    try:
        # Import from the defining module: the ``kanban_db`` facade re-exports
        # these only through the Sep-2026 plugin-compat layer, which the plugin
        # loader refuses (``hermes plugins doctor``).
        from hermes_cli.kanban_db_connect import connect, write_txn

        conn = connect(board=board)
        try:
            with write_txn(conn):
                conn.execute(
                    "update tasks set current_step_key = ? where id = ?", (step, task_id)
                )
        finally:
            conn.close()
    except Exception:
        pass


def _recite_hook(session_id: Optional[str] = None, **kwargs) -> Optional[dict]:
    line = recite(session_id)
    return {"context": line} if line else None


def _on_claimed(task_id: Optional[str] = None, board: Optional[str] = None, **kwargs) -> None:
    _set_step(task_id, "Do", board=board)


def _on_completed(task_id: Optional[str] = None, board: Optional[str] = None, **kwargs) -> None:
    _set_step(task_id, None, board=board)


def register(ctx: Any) -> None:
    ctx.register_hook("pre_llm_call", _recite_hook)
    ctx.register_hook("pre_tool_call", gate)
    ctx.register_hook("kanban_task_claimed", _on_claimed)
    ctx.register_hook("kanban_task_completed", _on_completed)
