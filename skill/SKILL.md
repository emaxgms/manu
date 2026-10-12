---
name: manu
description: "Use when work is more than a quick question — runs it through Plan/Do/Check/Close on a kanban card, gated on evidence, with retry vs rework routed by failure kind."
version: 1.1.0
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
stored twice: `running` means Do, `review` means Check.

The split is the design: **this skill carries the process content** — the phases, the acceptance-criteria
shape, when to rework, how to classify a failed Check — and **the plugin carries the gates**, the part a
skill cannot do, because a skill can only ask and a hook can refuse. Everything else (dispatch, review
handoff, PR CI contracts, retry budget, escalation) is core Hermes, reused untouched.

## Mechanics — the four gates, and what runs them

| Gate | Where | Effect |
|---|---|---|
| **Entry** | `pre_llm_call` | a work-shaped message with no card open for this session gets a `triage` card **created by the plugin** (bound to the session, body = your message) + one `[manu]` line telling the model to write the criteria, claim, and build. A quick question gets **nothing** — no card, no text |
| **Recitation** | `pre_llm_call` | one line per turn while a card is open for this session (or unbound on the board); silent otherwise |
| **Plan lock** | `pre_tool_call` on `write_file` / `patch` | refused while this session's card is still in `triage`/`todo`/`ready`: criteria first, then `claim` |
| **Check** | `pre_tool_call` on `kanban complete` (tool **and** CLI) | refused unless the card's latest review event is a `review_requested` — a `changes_requested`/`review_reopened` after it re-opens the loop |
| **Close** | `pre_tool_call` on `kanban complete` (tool **and** CLI) | refused with no attachment and no `--result` |
| `manu_specify` tool | registered by the plugin (`toolset: manu`) | runs the aux specifier **in-process** → Goal / Approach / Acceptance criteria / Out of scope on the card, then promotes it out of `triage` |

- **Who types:** the agent runs every `hermes kanban ...` command in-session; the user talks and answers.
  Entry needs no command at all — the plugin opens the card.
- **Card ↔ session binding:** the cards the plugin opens carry `session_id`, which is what arms the entry
  check and the Plan lock. A card created by hand from a shell has `session_id = NULL`: it is recited but
  does not arm the Plan lock, and it does not stop the entry gate from opening its own card. Bind cards by
  hand only when you want them to gate.
- **Kill switches:** `hermes plugins disable manu` (whole layer) · `hermes kanban archive <id>` (stops the
  recitation and the Plan lock for that session).
- **Plan step = the `manu_specify` tool, never the `hermes kanban specify` CLI.** The relay header
  `x-opencode-session` is derived from the **turn's** conversation context
  (`agent/opencode_affinity.py` ← `agent/auxiliary_client.py:6018`; the context is set at
  `agent/turn_context.py:383`). A standalone CLI process has no turn → no header → the OpenCode relay
  answers `400 MissingSessionID` (`LLM error: BadRequestError`) with the **default** aux model, so no
  provider pin fixes it. The tool calls the same specifier
  (`hermes_cli.kanban_specify.specify_task`) inside the live turn.
- **Ceilings, stated:** the classifier is a word list, not a model call — a work-shaped request it misses
  gets no card, and a quick question it over-reads leaves one orphan `triage` card to archive. All three
  gates are *presence* checks: criteria quality and evidence quality stay the reviewer's job. A human
  running `hermes kanban complete` from their own shell bypasses them on purpose.

## The four phases

| Phase | Status | Rule |
|---|---|---|
| **Plan** | `triage` → `specify` → `todo` → `ready` | **asking happens here.** Write the acceptance criteria before any build starts — Check tests against exactly those |
| **Do** | `running` | starts with `hermes kanban claim <id>` — that is what lifts the Plan lock. **No questions here:** if something is genuinely missing, `block` the card and say what is missing instead of guessing |
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
# 🟡 by hand (the plugin already does this for a work-shaped chat message)
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

# phase moves — the order the gates enforce
hermes kanban claim <id>                 # Plan -> Do: lifts the lock on file writes
hermes kanban request-review <id> --summary "what was built + what was verified"   # Do -> Check
hermes kanban complete <id> --result "pytest -q -> 12 passed; report at /home/ema/report.md"
hermes kanban attach <id> <file>         # evidence alternative to --result
hermes kanban reopen-review <id> --reason "acceptance #2 not met: no latency numbers"   # rework, in-session
hermes kanban request-changes <id> "acceptance #2 not met"   # reviewer-side; needs an active review run
hermes kanban show <id> ; hermes kanban stats
```

Closing is a three-step order, not a suggestion: Check first (`request-review`), then a close with
`--result`/`--summary` or an attachment. Both are refused by the gate with the exact command to run.

## Common Pitfalls

1. **Correcting a blocked write by writing somewhere else.** The Plan lock covers `write_file`/`patch`;
   `terminal`/`execute_code` are deliberately never gated. Routing a write through a heredoc is the
   documented bypass — it is visible in the transcript, and it defeats the criteria.
2. **Asking mid-Do.** Questions belong in Plan (they shape the spec) or in Rework (the gap is concrete).
   Mid-Do the correct move is `hermes kanban block <id> --reason "..." --kind needs_input`.
3. **Retrying an intent gap.** A green result that misses the requirement will stay green after N
   retries; the missing information is in the user's head. Rework, don't retry.
4. **Closing without evidence, or closing before Check.** Both are refused from an agent session; a
   human in their own shell bypasses by design. Keep the `--result` line short and factual.
5. **Reading silence as "the binding is wrong".** Silence has exactly two causes: no card in an open
   status *and* the message was not work-shaped. Query the board before blaming the session id.
6. **Trusting the entry gate for a job that starts as a question.** A lookup that turns into a rewrite
   mid-turn is only caught by the Plan lock if a card exists by then; the first write of a work-shaped
   *follow-up* message opens the card. Audit the odd case with `references/process-audit.md`.
7. **Bundling the close into a compound shell line.** The Check gate parses the raw command string, not
   argv: every non-flag token it sees is treated as a card id. So
   `hermes kanban complete <id> --result "text with spaces" 2>&1` is refused with the bogus
   `card(s) <words> have not passed Check`, and the refusal kills the whole `&&`/`;` chain — a
   `request-review` earlier in the same line never runs. Invoke the close as a **bare, single command**
   with a space-free `--result` (an artifact path is ideal); put the narrative in the earlier
   `request-review --summary` or in the attachment.

## Verification Checklist

- [ ] The gates fired: entry opened the card, the Plan lock held the writes until `claim`, Check preceded Close
- [ ] Acceptance criteria written on the card before the first build action
- [ ] `hermes kanban stats` shows no card left in `running` when the work is done
- [ ] Any failed Check classified (`transient` / `needs_input` / `capability` / `dependency`) before acting
- [ ] Card closed with evidence attached or in `--result`, after `review`

## Reporting a design proposal to this user

- Verdict in one line, then the mechanism (which hook, which CLI command, which file) — never a promise.
- Name the honest ceiling and what is deferred, with the trigger that would revive it.
- End with **one** decision he has to make, not a menu. Push back in mechanism terms when an idea is wrong.
- Long design threads: **patch the plan file in place** (phase table, acceptance tests, affected skill
  bullets) instead of appending "UPDATE:" sections.

## Reference

- `references/kanban-capabilities.md` — verified command/flag/API map (`file:line` sources) for every
  kanban knob: statuses, `--completion-contract`, retry budget, block kinds, decompose/specify, real
  `pre_llm_call` / `pre_tool_call` callback kwargs.
- `references/process-audit.md` — SQL over `kanban.db` + `state.db` that answers "did the process
  actually run on this job?" (cards, `[manu]` recitations, per-session tool mix) with numbers before
  proposing a fix or blaming the plugin.

**Verified behaviour (this machine):** a session with an open card gets exactly one `[manu]` line; a
session whose cards are all `done`/`archived` gets none. Both close paths are refused without evidence.
`manu_specify` promotes `triage → todo → ready` in-process without the relay 400. `reopen-review --reason`
records the reason and re-opens the card. A PR card with `--completion-contract` refuses to close
(`pr_acceptance` receipt `ok: false, classification: missing`).

**v0.2.0 (2026-10-12) — the entry gates.** The 0/2 failure (real work, zero cards) came from the entry
decision living only in this skill's prose; it now lives in the plugin. Verified against a throwaway
board: a work-shaped message creates the card in `triage` bound to the session, the Plan lock refuses
`write_file` on it, a second turn recites instead of duplicating, quick questions stay silent. `doctor`
clean, 30 tests pass. **A gateway restart is required** to load the new hook code — plugin modules are
memoized per process (`hermes_cli/plugins.py` `discover_and_load`).

**Honest ceiling of v0.2.0:** the gates enforce *state transitions*, not quality. What is still prose:
the criteria the model writes, the evidence it attaches, and the reviewer (self-review in a single-user
setup). The four-phase board is also deliberately reduced in practice — with no second reader, Evaluate
and Rework happen inside the same turn; the gates only guarantee that they *happened*, not that they
were adversarial.
