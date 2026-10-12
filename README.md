<h1 align="center">manu</h1>

<p align="center"><em>Plan → Do → Check → Close. On a card. With proof.</em></p>

<p align="center">
  <img src="https://img.shields.io/badge/for-Hermes%20Agent-111111?style=flat-square" alt="For Hermes Agent">
  <img src="https://img.shields.io/badge/license-MIT-111111?style=flat-square" alt="MIT license">
  <img src="https://img.shields.io/badge/dependencies-none-111111?style=flat-square" alt="No dependencies">
</p>

**manu is a process layer for [Hermes Agent](https://github.com/NousResearch/hermes-agent).** It puts
multi-step work on a kanban card, keeps the four phases of that work in front of the agent every turn,
and refuses to let the card close until there is something to show for it.

Small enough to read in one sitting: one Python file, ~290 lines, four hooks and one tool. No daemon,
no database of its own, no UI.

---

## The problem

Long agent sessions fail in three boring, predictable ways.

**1. "Done" with nothing behind it.** The agent says the tests pass, the report exists, the migration
ran. Sometimes it is right; often nothing was executed. Nothing in the loop asks for proof, so nothing
stops the claim.

**2. The plan evaporates.** Acceptance criteria agreed in the third turn are gone by the thirtieth —
compressed away, or never written down at all. The agent then verifies against whatever it remembers.

**3. Every failure gets the same treatment.** A red test and a green-but-wrong result are not the same
kind of failure. Retrying the first is cheap and correct. Retrying the second burns tokens reproducing
the same misreading, because the missing information is in the user's head, not in the code.

manu makes the process stateful instead of prompt-shaped: the phase lives on a card, the criteria live
on the card, and closure is gated on evidence.

## What you get

| | |
|---|---|
| **Four phases on a card** | Plan → Do → Check → Close. The phase *is* the card status (`running` = Do, `review` = Check), so nothing is stored twice and nothing drifts. |
| **A per-turn recitation** | While a card is open, every turn carries exactly one line: `[manu] card #t_ab12 "Compare LoRa stacks" — phase Do. Close it only with evidence.` When no card is open, nothing is injected — quick questions pay nothing. |
| **An evidence gate** | `hermes kanban complete` and the `kanban_complete` tool are refused unless the card carries a result or an attachment. The refusal says what to attach. |
| **A Plan step** | `manu_specify` asks the auxiliary specifier for Goal / Approach / Acceptance criteria / Out of scope and promotes the card out of triage — the criteria Check will test against. |

## The two loop exits

Failure kind picks the exit, so the retry budget is never spent on a question only a human can answer:

- **Retry** (inside Do) — machine-falsifiable failures (red test, crashing command, wrong output) are
  retried automatically, up to `kanban.failure_limit`, with **no human input**. At the threshold the
  card blocks itself and surfaces.
- **Rework** (back to Plan) — a green result that misses the requirement goes back with a reason
  (`hermes kanban reopen-review --reason "..."` in-session; `request-changes` when a review run is
  active). Questions are allowed here, because the gap is concrete by now.

## Before / after

An agent finishes a research task and closes the card:

```text
$ hermes kanban complete t_ab12
manu: card(s) t_ab12 have no evidence. Attach the command output or the artifact
(`hermes kanban attach <id> <file>`), or pass `--result "..."`, then close.
```

With the evidence attached, it closes:

```text
$ hermes kanban attach t_ab12 report.md
$ hermes kanban complete t_ab12 --result "report.md — 7 sources, one line per stack"
Completed t_ab12
```

That is the whole idea: the cheap, honest thing (one line of proof) becomes the path of least
resistance, and the card records it.

## Install

```bash
hermes plugins install emaxgms/manu --enable
```

Optional but recommended — the process skill that tells the agent when *not* to open a card at all:

```bash
mkdir -p ~/.hermes/skills/software-development/manu
cp skill/SKILL.md ~/.hermes/skills/software-development/manu/SKILL.md
```

Restart the gateway (or open a new CLI session) so the hooks load.

## Use it

The agent runs these commands for you — you talk, it drives the board. The CLI is shown so you can
follow (or take over) what happened.

```bash
# a concrete request: write the acceptance criteria yourself, start in todo
hermes kanban create "Compare EU LoRa mesh stacks" --assignee default \
  --body "Deliverable: markdown report. Acceptance: >=5 sources, one line per stack, every claim cited."

# a vague request: park it, then let the specifier write the Plan
hermes kanban create "improve the site" --triage --assignee default
#   -> call the manu_specify tool with that card id (triage -> ready, Plan artifact on the body)

# work, then close with proof
hermes kanban request-review t_ab12 --summary "what was built + what was verified"
hermes kanban attach t_ab12 report.md
hermes kanban complete t_ab12 --result "report.md; 7 sources cited"
```

## How it works

Hermes already owns dispatch, review handoff, PR CI contracts, the retry budget and the escalation back
to triage. manu adds only the three pieces that were missing, through the documented plugin surface:

| Piece | Hook | Effect |
|---|---|---|
| Recitation | `pre_llm_call` | one line per turn while a card is open; nothing when idle |
| Evidence gate | `pre_tool_call` | vetoes `kanban_complete` and agent-typed `hermes kanban complete` without evidence |
| Phase mirror | `kanban_task_claimed` / `_completed` | writes `current_step_key` so the dashboard shows the same phase (informational) |
| Plan step | tool `manu_specify` | runs the auxiliary specifier **in-process** and promotes the card |

```text
~/.hermes/plugins/manu/
├── plugin.yaml      # manifest: 1 tool, 4 hooks
├── __init__.py      # the whole plugin
├── test_manu.py     # 17 tests: recitation, gate on both paths, evidence lookup, tool mapping
└── skill/SKILL.md   # the process skill (traffic light, phases, failure triage, recipes)
```

## What it deliberately does not do

Honest ceilings, so you can decide whether they matter for you:

- **The gate checks presence, not quality.** An attached file proves *something* was produced; whether
  it is good is the reviewer's job.
- **A human closing a card from their own shell is not intercepted.** `pre_tool_call` sees the agent's
  tool calls, not your terminal.
- **The recitation binds on the card's session.** Cards created from a session carry its id; cards
  created by the bare CLI are unbound and are recited by whichever session is active.
- **One profile means the reviewer shares the author's model.** Use `hermes kanban set-model <id>` for
  a genuinely different verifier; a different *profile* is only worth it for a different toolset.
- **Phase bookkeeping needs a claim.** `current_step_key` is written when the card is claimed
  (`hermes kanban claim <id>`), so it stays empty on a card nobody has started.

## Requirements

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) with the kanban feature (tested on
  v0.21.1) and Python 3.11 — no extra dependencies.
- Nothing else. `hermes plugins disable manu` turns the whole layer off; `hermes kanban archive <id>`
  stops the recitation for one session.

## License

MIT
