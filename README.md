<h1 align="center">manu</h1>

<p align="center"><em>Plan → Do → Check → Close. On a card. With proof.</em></p>

<p align="center">
  <img src="https://img.shields.io/badge/for-Hermes%20Agent-111111?style=flat-square" alt="For Hermes Agent">
  <img src="https://img.shields.io/badge/license-MIT-111111?style=flat-square" alt="MIT license">
  <img src="https://img.shields.io/badge/dependencies-none-111111?style=flat-square" alt="No dependencies">
</p>

**manu is a process layer for [Hermes Agent](https://github.com/NousResearch/hermes-agent).** It opens a
kanban card for work-shaped requests, refuses file writes while that card is still in Plan, keeps the
four phases of that work in front of the agent every turn, runs Check in a context that never saw the
author's, and refuses to close the card before Check and without something to show for it.

The skill carries the process *content*; the plugin carries the *gates* — a skill can only ask, a hook
can refuse. One Python file, two hooks and two tools. No daemon, no database of its own, no UI.

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
| **An entry gate** | A work-shaped request with no card open gets one created **by the plugin** — in `triage`, bound to the session, your message as the body — plus one line naming the Plan step. A quick question gets no card and no text. |
| **A Plan lock** | `write_file` / `patch` are refused while this session's own card is still in `triage`/`todo`/`ready`: criteria first, then `claim`. Reads, searches and terminal are never gated. |
| **A Plan step** | `manu_specify` asks the auxiliary specifier for Goal / Approach / Acceptance criteria / Out of scope and promotes the card out of triage — the criteria Check will test against. |
| **Check in a fresh context** | `manu_review` reviews the card with only the card text and the artifacts you name — no conversation, no plan, no reasoning — and writes the per-criterion verdict onto the card as its review summary. |
| **A check gate** | `complete` is refused unless the card's latest review event is a `review_requested` — a `changes_requested` after it re-opens the loop. Check is a transition, not a promise. |
| **An evidence gate** | `hermes kanban complete` and the `kanban_complete` tool are refused unless the card carries a result or an attachment. The refusal says what to attach. |
| **Four phases on a card** | Plan → Do → Check → Close. The phase *is* the card status (`running` = Do, `review` = Check), so nothing is stored twice and nothing drifts. |
| **A per-turn recitation** | While a card is open, every turn carries exactly one line: `[manu] card #t_ab12 "Compare LoRa stacks" — phase Do. Close it only with evidence.` When no card is open, nothing is injected — quick questions pay nothing. |

## Check is the part most process layers get wrong

The gates prove that *a* review was recorded. They cannot make it adversarial, and inside one session the
planner, the coder and the reviewer are the same context: the author's own reasoning is the bias. So
`manu_review(task_id, artifacts=[...])` runs Check as a single auxiliary call whose **whole context is the
card title, the card body (the criteria) and the inlined artifact text**:

```json
{"criteria": [{"criterion": "AC1: the report cites 5 sources", "verdict": "met",
               "evidence": "report.md:12"},
              {"criterion": "AC2: every source states the mesh size", "verdict": "not_met",
               "evidence": "1 of 3 sources states a size"}],
 "unverifiable": ["whether the three names are real sources — no links given"],
 "verdict": "partial",
 "summary": "..."}
```

That verdict is written verbatim as the card's review summary — the exact event the Check gate reads —
so the recorded Check is a fact about the artifact, not a sentence the author wrote from memory:

```text
manu_review [deepseek-v4.1-flash] — verdict: partial | [met] AC1: the report cites 5 sources | [not_met] AC2: every source states the mesh size | unverifiable: 3
- [met] AC1: the report cites 5 sources — report.md lists three named stacks: Meshtastic, MeshCore, Reticulum
- [not_met] AC2: ... — 2 of 3 sources do not state a mesh size
unverifiable: whether the three names are genuine sources — no links or citations are provided
```

**The honest ceiling:** the reviewer is a completion with no tools and no filesystem, so it judges the
text it is handed and it cannot run a command. Ask for the artifact paths, and treat anything it could not
verify as unverified — the reviewer is instructed to say so rather than round up. What it does remove is
the failure mode that matters most in one long session: a reviewer that inherits the author's plan,
claims and rationalizations.

Want the review on a **different model**? Set the route the tool uses — `hermes config set
auxiliary.review.model <model>` — no profile and no dispatcher needed. (`hermes kanban set-model` is
task-level: it applies to the implementation run *and* the review run, so it is not the knob for this.)

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

Optional but recommended — the process skill (with its references and probes):

```bash
mkdir -p ~/.hermes/skills/software-development/manu
cp -r skill/. ~/.hermes/skills/software-development/manu/
```

Restart the gateway (or open a new CLI session) so the hooks load. Plugin modules are memoized per
process: a fix committed now runs only in a process started after it.

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

# work, then Check in a fresh context, then close with proof
hermes kanban claim t_ab12
#   -> call the manu_review tool with the card id and the artifact paths (records the verdict)
hermes kanban attach t_ab12 report.md
hermes kanban complete t_ab12 --result "report.md; 7 sources cited"
```

## How it works

Hermes already owns dispatch, review handoff, PR CI contracts, the retry budget and the escalation back
to triage. manu adds only the gates that were missing, through the documented plugin surface:

| Piece | Hook | Effect |
|---|---|---|
| Entry + recitation | `pre_llm_call` | opens the Plan card for a work-shaped request, or recites the phase of the open one; nothing when idle |
| Plan lock | `pre_tool_call` | vetoes `write_file` / `patch` while this session's card is still in Plan |
| Check | `pre_tool_call` | vetoes `complete` until a `review_requested` event exists on the card |
| Evidence gate | `pre_tool_call` | vetoes `kanban_complete` and agent-typed `hermes kanban complete` without evidence |
| Plan step | tool `manu_specify` | runs the auxiliary specifier **in-process** and promotes the card |
| Check step | tool `manu_review` | runs the review in a fresh context and records the verdict on the card |

```text
~/.hermes/plugins/manu/
├── plugin.yaml          # manifest: 2 tools, 2 hooks
├── __init__.py          # the whole plugin
├── test_manu.py         # 39 tests: entry classifier, Plan lock, Check, evidence, recitation, review
├── docs/
│   └── entry-classifier.md   # how the keyword classifier was measured (and what still fools it)
└── skill/
    ├── SKILL.md         # the process skill (phases, failure triage, recipes)
    ├── references/      # kanban command/flag map, process audit SQL
    └── scripts/         # fresh-process probes: entry gate, Check
```

## The entry classifier is measured, not guessed

`looks_like_work()` is a word list, so it is only as good as the evidence behind it: a labelled corpus of
45 real requests out of `state.db`, scored before and after each change.

| classifier | true positive | true negative | false positive | false negative | accuracy |
|---|---|---|---|---|---|
| v0.2.0 (word list + "longer than 200 chars") | 27 | 6 | 10 | 2 | 73.3% |
| v0.3.0 | 29 | 14 | 2 | 0 | **95.6%** |

Length is not work (eight of the ten false positives were long complaints), a work verb inside a question
sentence is not an instruction, and `preview` is not `review`. The method, the numbers and the two
remaining false-positive classes are in [`docs/entry-classifier.md`](docs/entry-classifier.md).

## What it deliberately does not do

Honest ceilings, so you can decide whether they matter for you:

- **The gate checks presence, not quality.** An attached file proves *something* was produced. Whether it
  is good is what `manu_review` is for — and even that only judges the material it is given.
- **The reviewer has no tools.** It cannot run your tests, open a URL or read a file you did not inline,
  so it must report those as unverifiable. Give it the artifact paths.
- **The entry classifier is still a word list.** Two classes of false positive survive: a long declarative
  rant that contains a work verb, and a *negated* action ("no need to rewrite it"). Both need semantics;
  `looks_like_work()` is one function, which is where an auxiliary call would go.
- **The Plan lock covers `write_file`/`patch`, not `terminal`.** A write routed through a heredoc is the
  documented bypass — deliberate, so arithmetic and quick shell work never pay for the process.
- **A human closing a card from their own shell is not intercepted.** `pre_tool_call` sees the agent's
  tool calls, not your terminal.
- **The recitation binds on the card's session.** Cards created from a session carry its id; cards
  created by the bare CLI are unbound and are recited by whichever session is active.
- **Check runs on the route you configure** (`auxiliary.review.*`, by default your main model — the same
  model as the author, minus the author's context). A second *profile* is only worth it for unattended
  dispatch, a different toolset, or a second messaging identity.

## Requirements

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) with the kanban feature (tested on
  v0.21.1) and Python 3.11 — no extra dependencies.
- Nothing else. `hermes plugins disable manu` turns the whole layer off; `hermes kanban archive <id>`
  stops the recitation for one session.

## License

MIT
