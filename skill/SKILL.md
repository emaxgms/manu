---
name: manu
description: "Use when work is more than a quick question — runs it through Plan/Do/Check/Close on a kanban card, gated on evidence, with retry vs rework routed by failure kind."
version: 1.0.0
author: emaxgms
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [hermes, kanban, process, workflow, phases, evidence, gates]
    related_skills: [plan, requesting-code-review, ad-hoc-verification, hermes-plugin-diagnostics]
---

# manu — Plan / Do / Check / Close

The process state is a **kanban card**, not a prompt. The phase **is** the card status, so nothing is
stored twice: `running` means Do, `review` means Check. The `manu` plugin adds the three things Hermes
does not already do — a one-line phase recitation per turn, a refusal to close a card with no evidence,
and phase bookkeeping — and everything else (dispatch, review, rework handoff, PR CI contracts, retry
budget, escalation) is core Hermes, reused untouched.

## When to Use

- 🟡 **One card**: work that spans turns, or produces an artifact the user will re-read (research,
  report, dashboard, document, script).
- 🔴 **Parent + children**: independent deliverables, or anything ending in a PR
  (`--completion-contract`).
- Not for 🟢 questions, short lookups, single-file edits. Those get **no card, no phases**: the process
  must be invisible on the fast path, and any rule that taxes it is wrong.

## Mechanics — what actually runs

| Piece | Where | Effect |
|---|---|---|
| Plugin | `~/.hermes/plugins/manu/{plugin.yaml,__init__.py}` | `pre_llm_call` recitation, `pre_tool_call` evidence gate, `kanban_task_claimed`/`_completed` step bookkeeping |
| Recitation | injected into the current turn's user message | one line, **only** while a card whose `session_id` is this session is open; nothing when idle |
| Gate | `pre_tool_call` | blocks `kanban_complete` (workers) **and** `hermes kanban complete` typed by the agent |
| `manu_specify` tool | registered by the plugin (`toolset: manu`) | runs the aux specifier **in-process** → Plan artifact on the card, then promotes it out of `triage` |
| Step mirror | `current_step_key` | informational; v1 routing ignores it. Inert while `kanban.dispatch_in_gateway: false` (those hooks fire in dispatcher/worker processes) |
| Tests | `~/.hermes/hermes-agent/venv/bin/python -m pytest ~/.hermes/plugins/manu/test_manu.py -q` | 17 tests: recitation, gate on both paths, evidence lookup, `manu_specify` mapping, hook/tool wiring |

- **Who types:** the agent runs every `hermes kanban ...` command in-session; the user talks and
  answers questions. The user types a command only if they choose to take over the board by hand.
- **Card ↔ session binding:** a card created from a session inherits `HERMES_SESSION_ID`, and only then
  is it recited. Cards created from the dashboard or a plain shell have `session_id = NULL` → no
  recitation; work them explicitly with `hermes kanban list` / `show <id>`.
- **Kill switches:** `hermes plugins disable manu` (whole layer) · `hermes kanban archive <id>` (stops
  the recitation for that session).
- **Plan step = the `manu_specify` tool, never the `hermes kanban specify` CLI.** The relay header
  `x-opencode-session` is derived from the **turn's** conversation context
  (`agent/opencode_affinity.py` ← `agent/auxiliary_client.py:6018`; the context is set at
  `agent/turn_context.py:383`). A standalone CLI process has no turn → no header → the OpenCode relay
  answers `400 MissingSessionID` (`LLM error: BadRequestError`) with the **default** aux model, so no
  provider pin fixes it. The tool calls the same specifier
  (`hermes_cli.kanban_specify.specify_task`) inside the live turn, so the default model works and
  `auxiliary.triage_specifier.*` stays at `auto`.
- **Ceiling, stated:** the gate is a presence check on evidence — quality is the reviewer's job. A human
  running `hermes kanban complete` in their own shell bypasses it on purpose.

## The four phases

| Phase | Status | Rule |
|---|---|---|
| **Plan** | `triage` → `specify` → `todo` → `ready` | **asking happens here.** Write the acceptance criteria before any build starts — Check tests against exactly those |
| **Do** | `running` | starts with `hermes kanban claim <id>` (that is what turns the recited phase into "Do" and fires the step mirror). **No questions here:** if something is genuinely missing, `block` the card and say what is missing instead of guessing |
| **Check** | `review` | verify against the criteria written in Plan; PR cards cannot close until required CI passes |
| **Close** | `done` → `archived` | only with evidence attached |

Two loop exits, and the failure kind picks which:

- ↻ **Retry** (inside Do, `running`): a machine-falsifiable failure (red test, crashing command, wrong
  output) is retried by the executor with **no human input**, up to `kanban.failure_limit` (2). At the
  threshold the card auto-blocks with a `gave_up` event.
- ↻ **Rework** (back to Plan): green but not the requirement. Then **questions are allowed**. In-session
  the exit is `hermes kanban reopen-review <id> --reason "<requirement not met>"` — verified: `review`
  → `ready` with the reason recorded as a `CHANGES REQUESTED` comment. The reviewer-side
  `hermes kanban request-changes <id> <reason...>` (reason is positional) only works on an **active
  review run**, i.e. when a dispatcher/worker claimed the card for review; with dispatch off it answers
  "task is not in an active review run". Three blind retries reproduce the same misreading three times.

## Failure triage — classify before retrying

- `transient` (red test, crash, flaky tool, empty output) → **automatic retry**, budget above, zero
  questions.
- `needs_input` (green but not what was meant; two readings possible) → **ask now**, rework path.
- `capability` (missing credential/system/permission) → **stop and report**; retrying is theatre.
- `dependency` (parent card open) → waits in `todo`; never enters the human bucket.

Repeated block → unblock → re-block escalates the card back to `triage` (= Plan, where the human
answers). Nothing to configure; classify honestly and spend the retry budget only on the first class.

## Recipes

```bash
# 🟡 vague request: park it, then let the specifier write the Plan artifact
hermes kanban create "improve the site" --triage --assignee default
#   -> call the manu_specify TOOL with that id (not the CLI: outside a turn it 400s, see Mechanics)
hermes kanban show <id>                 # read Acceptance criteria BEFORE building

# 🟡 concrete request: write the body yourself, start in todo
hermes kanban create "Compare the 3 EU LoRa mesh stacks" --assignee default \
  --body "Deliverable: markdown report. Acceptance: >=5 sources, per-stack mesh size, done when every claim cited."

# 🔴 project: parent + children, PR card carries the CI contract
hermes kanban create "Refactor auth" --assignee default
hermes kanban create "extract token store" --assignee default --parent <parent-id>
hermes kanban create "open PR for token store" --assignee default --parent <parent-id> \
  --completion-contract owner/repo

# phase moves
hermes kanban claim <id>                 # Do starts here: ready -> running, phase becomes "Do"
hermes kanban request-review <id> --summary "what was built + what was verified"
hermes kanban complete <id> --result "pytest -q -> 12 passed; report at /home/ema/report.md"
hermes kanban attach <id> <file>         # evidence alternative to --result
hermes kanban reopen-review <id> --reason "acceptance #2 not met: no latency numbers"   # rework, in-session
hermes kanban request-changes <id> "acceptance #2 not met"   # reviewer-side; needs an active review run
hermes kanban show <id> ; hermes kanban stats
```

Evidence is mandatory to close: the gate refuses a close with neither `--result`/`--summary` nor an
attachment, and says so in the block message.

## Common Pitfalls

1. **Opening a card for a 🟢.** If the answer fits one turn, no card. Archive cards you opened by mistake.
2. **Asking mid-Do.** Questions belong in Plan (they shape the spec) or in Rework (the gap is concrete).
   Mid-Do the correct move is `hermes kanban block <id> --reason "..." --kind needs_input`.
3. **Retrying an intent gap.** A green result that misses the requirement will stay green after N
   retries; the missing information is in the user's head. Rework, don't retry.
4. **Closing without evidence.** Not possible from an agent session — the gate blocks it. Keep the
   `--result` line short and factual (command + outcome), attach the artifact.
5. **Expecting the recitation for CLI-created cards.** Cards made from a plain shell have no
   `session_id`; create cards from the session that owns the work, or check the board by hand.

## Verification Checklist

- [ ] Level chosen before any work (🟢 no card · 🟡 one card · 🔴 parent + children)
- [ ] Acceptance criteria written on the card before the first build action
- [ ] Card is `review` with `--summary` before `complete`, and evidence attached or in `--result`
- [ ] Any failed Check classified (`transient` / `needs_input` / `capability` / `dependency`) before acting
- [ ] `hermes kanban stats` shows no card left in `running` when the work is done

## Reporting a design proposal to this user

- Verdict in one line, then the mechanism (which hook, which CLI command, which file) — never a promise.
- Name the honest ceiling and what is deferred, with the trigger that would revive it.
- End with **one** decision he has to make, not a menu. Push back in mechanism terms when an idea is wrong.
- Long design threads: **patch the plan file in place** (phase table, acceptance tests, affected skill
  bullets) instead of appending "UPDATE:" sections.

## Reference

- `references/kanban-capabilities.md` — verified command/flag/API map (`file:line` sources) for every
  kanban knob: statuses, `--completion-contract`, retry budget, block kinds, decompose/specify, hooks.

**Pilot status (2026-10-11, this machine):** verified end to end — a fresh session with an open card
gets exactly one `[manu]` line, a session with no open card gets none, both close paths are refused
without evidence, `specify` promotes `triage → todo → ready`, `reopen-review --reason` records the
reason and re-opens the card, and a PR card with `--completion-contract` refuses to close
(`pr_acceptance` receipt `ok: false, classification: missing`).
