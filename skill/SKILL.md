---
name: manu
description: "Use when work is more than a quick question — runs it through Plan/Do/Check/Close on a kanban card, gated on evidence, with retry vs rework routed by failure kind."
version: 1.3.0
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
| `manu_review` tool | registered by the plugin (`toolset: manu`) | runs Check in a **fresh context** (card text + named artifacts only), writes the per-criterion verdict as the card's review summary — the event the Check gate reads |

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
- **`manu_specify` and `manu_review` are not always in the visible tool schema** — they are plugin tools
  in the `manu` toolset, so on a session whose schema predates the plugin they appear only through
  discovery: `tool_search("manu_review review card")` then invoke via `tool_call`. Do not conclude a tool
  is missing and reach for the CLI on the Plan step, which has no turn context and 400s (bullet above).
- **Ceilings, stated:** the classifier is a word list, not a model call — a work-shaped request it misses
  gets no card, and a quick question it over-reads leaves one orphan `triage` card to archive. It was
  measured on 45 real requests in 2026-10: 10 false positives → 2, 2 false negatives → 0 (`docs/entry-classifier.md`
  in the repo). Add a
  marker to `_INTERNAL_MARKERS` whenever a new gateway-generated message class appears: delegation batch
  completions and `[INTERNAL NOTIFICATION …]` notices arrive in the **user** role but are not user
  requests, and without a marker the entry gate opens a card nobody asked for. All three
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
- **A card that is already `done` cannot be reopened** — `reopen-review` accepts only cards in `review`
  (verified: it answers "cannot reopen <id> (not in review?)"). When an independent Check lands *after* the
  close, run the rework as a **child card** (`hermes kanban create ... --parent <done-id>`) whose body
  carries the reviewer's findings, claim it, fix the artifact, and record the reviewer's verdict verbatim
  as a comment on that child. Do not re-open the parent, and do not silently patch the artifact with no
  card: the close already happened, and an untracked fix is exactly the drift the gates exist to prevent.

## Check in a fresh context — the reviewer must not share the author's context

The gates prove that *a* review was recorded. They cannot make it adversarial, and in one session the
planner, the coder and the reviewer are the same context: the author's own reasoning is the bias.

**Default: `manu_review(task_id, artifacts=[...])`.** The plugin tool reads the card title, body and
attachments, adds the artifact paths you name, and runs the review as one auxiliary call on the
`auxiliary.review.*` route whose **entire context is that material** — no conversation, no plan, no
reasoning, no "check that it's fine". It then writes the verdict verbatim onto the card as the
`review_requested` summary, so the Check gate rests on the reviewer's own words:

```text
manu_review [<model>] — verdict: partial | [met] AC1: … | [not_met] AC2: … | unverifiable: 2
- [met] AC1: the report cites 5 sources — report.md:12
- [not_met] AC2: every source states the mesh size — 1 of 3 sources states one
unverifiable: whether the three names are real sources — no links given
```

- Run it while the card is `running` (Do). A card in `ready` is refused: Check runs on claimed work.
- **Give it the artifact paths** (`artifacts=[...]`): the reviewer has no tools and no filesystem, only the
  text you hand it. Small text files are inlined; binaries and oversize files are named but not inlined,
  and everything it cannot see must come back as `unverifiable` — the prompt forbids rounding up (a `pass`
  requires every criterion `met` *and* an empty unverifiable list).
- The first line carries the per-criterion verdicts on purpose: core keeps only the first line (400 chars)
  of a `review_requested` summary in the event payload; the full evidence lines live on the run row
  (`hermes kanban runs <id>`).
- Read the returned JSON and **act on a `partial`/`fail`**: fix the artifact, then run `manu_review` again
  (or `reopen-review` after the close if the card already moved). A recorded Check is not a passing Check.
- **An unreadable verdict is not recorded.** If the reply comes back truncated or not as a JSON object,
  the tool returns `success=false`, hands you the raw reply, and leaves the card in Do — the gate must not
  accept a Check nobody can read. (Live case: ten long criteria made the reviewer echo them past the token
  ceiling; the budget was raised and the prompt now bounds each field.)
- **A different reviewer *model* is a config line, not a profile**: `hermes config set auxiliary.review.model
  <model>` (and `.provider` if needed). By default that route falls through to your main model — the same
  model as the author, minus the author's context, which is the trade this design takes.

**When the reviewer must run things** — execute the test suite, diff two revisions, open a URL — the aux
reviewer cannot, so run Check as a **delegated fresh session** instead and record its verdict with
`hermes kanban request-review <id> --summary "..."`:

```python
delegate_task(
    goal="Verify <artifact> against these acceptance criteria",
    context="<card title> + <card body criteria> + <artifact paths>  # and nothing else",
    output_schema={... per-criterion verdict + an 'unverifiable' list ...},
)
```

- **Pass only the card title, the criteria and the artifact paths.** Never your reasoning, never "check
  that it's fine", never the conversation: the bias re-enters through the context you hand over. This is
  the whole rule.
- Verified properties: the child reports `can_see_prior_conversation: false` (its context is the system
  prompt plus the delegated text) and the **same model** as the parent — `delegation.model` /
  `delegation.provider` empty means inherit. Same model is the point (removes context bias without adding
  a second provider's blind spots); a different model is a choice, not a requirement.
- Record the child's verdict **verbatim** in `hermes kanban request-review <id> --summary "..."`, with the
  per-criterion verdicts in the summary, so the Check gate rests on a fact rather than on your word.
- Ask for an explicit *unverifiable* list. A reviewer that says what it could not check from the artifact
  alone is the one worth having; on its first run here it found a criterion that only 3 of 7 options
  actually satisfied.
- Limits, so you do not oversell it: not durable (parent dies ⇒ child dies), not attributed on the board
  (the review stays a run of your session), read-only only by discipline (same profile ⇒ same write
  permissions), and it cannot ask the user anything — it reports the gap instead.
- **Dispatcher vs delegation, in one line: dispatch buys durability and board attribution, delegation
  buys a fresh context.** They are not substitutes, and the requirement here is the fresh context.
- **A different reviewer *model*: pin the review route, not a flag.** `hermes kanban set-model` is
  task-level and applies to the implementation run too, so it does not give you a different reviewer.
  `hermes config set auxiliary.review.model <model>` changes the model `manu_review` uses, with no profile
  and no dispatcher; a *profile* is only needed for `hermes kanban request-review <id> --reviewer <profile>`
  ("reassigns the task before review dispatch"), which means nothing with the dispatcher off — there is no
  review run to hand to anyone.

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
#   -> call the manu_review TOOL (card id + artifact paths): Do -> Check, verdict recorded verbatim
hermes kanban request-review <id> --summary "what was built + what was verified"   # Do -> Check (manual)
hermes kanban complete <id> --result "pytest -q -> 12 passed; report at /home/ema/report.md"
hermes kanban attach <id> <file>         # evidence alternative to --result
hermes kanban reopen-review <id> --reason "acceptance #2 not met: no latency numbers"   # rework, in-session
hermes kanban request-changes <id> "acceptance #2 not met"   # reviewer-side; needs an active review run
hermes kanban runs <id>                  # the full Check, evidence lines included
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
8. **Treating a committed plugin fix as live.** Plugin hook code loads once per process
   (`hermes_cli/plugins.py` `discover_and_load`: `if self._discovered and not force: return`), so
   a fix written after a host started keeps running the OLD module in that host until it restarts.
   After any change to this plugin's hook code: restart every plugin host (on this machine BOTH
   `hermes-gateway.service` and `hermes-dashboard.service` — `gwrs` + `dbrs`, sudo), then verify
   on a **new session** (a work-shaped message must open an *assigned* card) — a green suite runs
   the module, not the process. `scripts/entry-gate-e2e.py` proves the on-disk module in a fresh
   process against a throwaway board (no live writes) while you arrange the restart.
9. **Trusting the aux specifier's Approach when the evidence is an attachment.** `manu_specify` sees only
   the card **body text** — an attached log, dump or trace is not parsed, so it happily writes
   plausible-but-false criteria (e.g. "the log is truncated after the display modes" for a log whose
   crash sits in the tail) and Check then verifies against them. Read the artefact yourself first, correct
   the card before claiming (`hermes kanban edit <id> --body ...`, or a comment recording the corrected
   criteria), and only then `claim`.
10. **Trying to move a `triage` card with `promote` or `claim`.** Both refuse it: `promote` applies only
   to `todo`/`blocked`, `claim` requires `ready` — the **only** exit from `triage` is `specify`
   (or `decompose`), which is why the entry gate's card is moved by `manu_specify`. When you want your
   own criteria on that card instead of the specifier's, the order matters:
   `manu_specify` (moves the state machine) → `hermes kanban edit <id> --title … --body …` (replaces the
   text with yours; `edit` works at **any** status, unlike `promote`) → `hermes kanban claim <id>`.
   Writing the body first and specifying after loses your text.
11. **Reading a recorded Check as a passing Check.** `manu_review` records a verdict — `partial` and `fail`
   are recorded too, and the gate only asks that a `review_requested` came last. Read the JSON it returns
   (or `hermes kanban runs <id>`): a `not_met`/`partial` criterion is unfinished work, not a formality to
   close over. The same is true of a summary you typed yourself — the gate cannot tell it from a review.
12. **Asking the reviewer for something it cannot do.** It is a completion with no tools: it cannot run
   `pytest`, open a URL, or read a file you did not inline (it will list those as `unverifiable`, which is
   the honest answer, not a failure). If a criterion requires executing something, run that yourself, attach
   the output as an artifact, and pass it to `manu_review` — or use the delegated child above.

## Verification Checklist

- [ ] The gates fired: entry opened the card, the Plan lock held the writes until `claim`, Check preceded Close
- [ ] Acceptance criteria written on the card before the first build action
- [ ] `hermes kanban stats` shows no card left in `running` when the work is done
- [ ] After a plugin-code change: every plugin host restarted, and the new behaviour seen on a
      NEW session (not just in tests)
- [ ] Any failed Check classified (`transient` / `needs_input` / `capability` / `dependency`) before acting
- [ ] The recorded Check is a verdict you read (per-criterion `met`/`not_met`/`partial` + `unverifiable`),
      not just a `review_requested` event the gate happens to accept
- [ ] Card closed with evidence attached or in `--result`, after `review`

## Reporting a design proposal to this user

- Verdict in one line, then the mechanism (which hook, which CLI command, which file) — never a promise.
- Name the honest ceiling and what is deferred, with the trigger that would revive it.
- End with **one** decision he has to make, not a menu — unless he asked for a menu (bullet below).
  Push back in mechanism terms when an idea is wrong.
- Review/decision cards ("fai un review su X / se e perché fare Y"): deliver a markdown review in
  `~/.hermes/plans/` (English) grounded in live-checked facts (the commands run and their outputs)
  plus the authoritative docs — current state, pros/cons, an explicit recommendation with decision
  triggers; attach it to the card, and make **no** system changes unless the card asks.
- Long design threads: **patch the plan file in place** (phase table, acceptance tests, affected skill
  bullets) instead of appending "UPDATE:" sections.
- Research/landscape reports ("how do others do X", "what should we build"): every third-party claim
  carries its URL plus an evidence label — `documented` / `anecdotal` / `snippet` (X, Reddit, LinkedIn
  pages behind login walls are snippet-level unless actually fetched; say so rather than dropping or
  inflating them); prefer aggregators that bundle quote + link (e.g. the docs' user-stories page);
  close with a coverage section naming what was thin.
- Deliver on request: when he asks for the report "su telegram" (or delivery is part of the ask), send
  the file itself — `hermes send --to telegram "MEDIA:<abs path>"`; targets come from
  `hermes send --list telegram`, it needs no LLM or running gateway for bot-token platforms, and
  `--file` carries text only (documents go via `MEDIA:` in the message text). The file in
  `~/.hermes/plans/` stays the deliverable; the chat copy is a duplicate.
- **When he asks to choose ("elenco di opzioni / cosa possiamo implementare"), the deliverable IS a
  numbered menu** and the one decision is "which numbers". Shape that works: state the **filter
  criterion** up front (an option is kept only if it serves a named goal), then one numbered item per
  option carrying *what changes · cost · prerequisites · how we verify it · which goal it serves*; list
  the **rejected** options too, one line each with the reason — omitting them silently reads as if they
  were never considered; close with the recommended bundle and the order to apply it. An option with no
  verification step is not decidable, so it does not belong on the list. Write it to a `~/.hermes/plans/`
  file (English) **and** repeat the numbered list in chat, so he can answer with numbers alone.

## Reference

- `references/kanban-capabilities.md` — verified command/flag/API map (`file:line` sources) for every
  kanban knob: statuses, `--completion-contract`, retry budget, block kinds, decompose/specify, real
  `pre_llm_call` / `pre_tool_call` callback kwargs.
- `references/process-audit.md` — SQL over `kanban.db` + `state.db` that answers "did the process
  actually run on this job?" (cards, `[manu]` recitations, per-session tool mix) with numbers before
  proposing a fix or blaming the plugin.
- `scripts/entry-gate-e2e.py` — fresh-process probe (throwaway board, no live writes) that proves the
  on-disk plugin opens an *assigned* card; run after any hook/plugin change, before or while you
  arrange the host restarts.
- `scripts/review-e2e.py` — fresh-process probe for Check: creates a card on a throwaway board, makes the
  **real** `auxiliary.review` call through `manu_review`, and asserts the verdict landed as the card's
  `review_requested` summary. Costs one small completion; no live writes. Run it from a real shell (a
  tool wrapper that re-executes the file can kill the run mid-call).
- `docs/entry-classifier.md` (repo root) — the labelled-corpus measurement behind the entry classifier:
  method, before/after numbers (FP 10 → 2, FN 2 → 0 on 45 real requests), and the two false-positive
  classes that remain.

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
memoized per process (`hermes_cli/plugins.py` `discover_and_load`). The plugin registers exactly three
things: `pre_llm_call` (entry + recitation), `pre_tool_call` (the three gates) and the `manu_specify`
tool. The earlier `kanban_task_claimed`/`kanban_task_completed` step mirror (`current_step_key`) is
**gone** — do not document it, expect it, or read a phase out of that column.

**v0.3.0 (2026-10-12) — a measured classifier and Check in a fresh context.** Option 1/2/3/5 of
`~/.hermes/plans/2026-10-12_1915-manu-options.md`.
- The entry classifier was measured against 45 real requests from `state.db` and retuned: false positives
  10 → 2, false negatives 2 → 0, accuracy 73% → 96%. What changed: the 200-char "long means work" rule is
  gone, stems are word-anchored (`preview` ≠ `review`), a work stem inside a question sentence no longer
  counts unless an explicit ask (`fai`, `puoi verificare`, `valuta`) appears, `build`/`setup`/`genera` were
  dropped as pure false-positive sources, and the change verbs (`modific`, `togli`, `rimuovi`, `pulir`,
  `miglior`, `fix`, `check`, `verifica`…) were added. Regression cases ship in `test_manu.py`.
- `manu_review(task_id, artifacts=[...])` runs Check in a fresh context and records the verdict as the
  card's `review_requested` summary. Verified end to end on a throwaway board: the aux route
  (`auxiliary.review.*`) returned per-criterion verdicts (`met` / `not_met`) plus three `unverifiable`
  items, and `hermes kanban runs` shows the full Check. 39 tests pass; `doctor` clean.
- Gates unchanged: same two hooks, same three refusals, plus the second tool.
- **A gateway restart is required** to load the new hook/tool code in the running hosts.

**Honest ceiling of v0.3.0:** the gates still enforce *state transitions*, not quality. What is still
prose: the criteria the model writes, and the evidence it attaches. Check is no longer self-review — the
reviewer never sees the author's context — but it is one text-only completion: it judges what it is handed,
cannot execute anything, shares the author's *model* (by default), and a summary you type yourself passes
the gate just as well as a recorded verdict does. The four-phase board is likewise reduced in practice:
with no second reader, Evaluate and Rework happen inside the same turn; the gates guarantee that a Check
*happened*, not that it was rigorous.
