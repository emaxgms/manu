# Hermes kanban capabilities — verified map

Everything here was verified against the installed tree at `~/.hermes/hermes-agent` and a live
`~/.hermes/kanban.db`. Re-check a line before relying on it; treat it as a map, not a contract.

## Task statuses and lifecycle

`VALID_STATUSES = {triage, todo, scheduled, ready, running, blocked, review, done, archived}`
(`hermes_cli/kanban_db.py:89`). `VALID_INITIAL_STATUSES = {running, blocked}`.

- `triage` — parking column for rough ideas; `specify` (or `decompose`) promotes it. Run `specify`
  through the `manu_specify` tool: the `hermes kanban specify` CLI has no turn context, so the
  OpenCode relay rejects its aux call with 400 `MissingSessionID`.
- `todo` — waiting on parents; the dispatcher promotes `todo → ready` when every parent is `done`
  (`hermes kanban promote` forces it).
- `ready` — claimable. `claim_task` requires `status='ready'`; a manual `hermes kanban claim` succeeds
  even with `assignee=NULL` (verified live) — it is the DISPATCHER's claim/spawn path that requires a
  non-NULL assignee (see Assignment and dispatch).
- `review` — Check phase; `reopen-review --reason` returns it to `ready`/`todo` (the in-session rework
  exit); `request-changes <id> <reason...>` is reviewer-side and requires an active review run.
- `blocked` — the human bucket. `scheduled` — recurring work.

## Create / link / close

```bash
hermes kanban create "<title>" [--triage] [--assignee <profile>] [--parent <id>]... \
  [--workspace worktree|dir] [--branch <name>] [--project <p>] [--skill <s>]... \
  [--model <override>] [--max-retries N] [--completion-contract OWNER/REPO|local-only] \
  [--goal] [--goal-max-turns N] [--priority N] [--initial-status blocked|running] \
  [--max-runtime 30m|2h|1d] [--idempotency-key <key>]
hermes kanban link <parent> <child>        # dependency edge (a running child is refused)
hermes kanban swarm "<goal>" --worker <profile>:<title>[:skill,skill]... --verifier <p> --synthesizer <p>
hermes kanban set-model <id> <model|none> [--provider <p>]   # per-task worker model override
hermes kanban schedule <id>                # park in `scheduled` (waiting on time, not a human)
hermes kanban claim <id>                   # atomic; prints the resolved workspace path
hermes kanban complete <id> --result "..." [--summary "..."] [--metadata '{...}']
hermes kanban request-review <id> --summary "what was implemented and how it was verified" [--reviewer <profile>]
hermes kanban request-changes <id> "<requirement not met>"   # reviewer-side; needs an ACTIVE review run
hermes kanban reopen-review <id> [--reason "<...>"]          # rework in-session: review -> ready/todo
hermes kanban block <id> --reason "..."   # kinds: dependency | needs_input | capability | transient
hermes kanban promote <id> ; hermes kanban archive <id> ; hermes kanban stats
```

- `--completion-contract OWNER/REPO` (or an exact PR URL): the card **cannot complete until the
  repository's required exact-head CI passes**. Missing, pending, failed, cancelled, timed-out, stale,
  skipped or neutral required evidence cannot complete it either; `local-only` is the default for
  non-PR work. Sources: `tools/kanban_tools_schemas.py` (`completion_contract`), kanban docs.
- `goal_mode` (+`goal_max_turns`): an auxiliary **judge** re-reads the worker's output against the
  card's title/body and keeps it going until done, or blocks it for human review when the turn budget
  runs out. That is the native evaluator loop for open-ended cards.
- `swarm`: `--worker` is repeatable (`profile:title[:skill,skill]`), `--verifier` and `--synthesizer`
  are required; the graph commits atomically (root blackboard → parallel workers → verifier →
  synthesizer).
- `hermes kanban daemon` is deprecated in this build — the dispatcher runs inside the gateway
  (`kanban.dispatch_in_gateway`); `hermes kanban dispatch [--dry-run]` is the single-pass form, and
  `--dry-run` names what a tick would spawn and what it held back (`respawn_guarded`, `rate_limited`).

## Failure handling and retry budget

- `_record_failure` (`hermes_cli/kanban_db.py:990-1008`): every non-success path bumps
  `consecutive_failures`; the trip threshold is **per-task `max_retries` > `kanban.failure_limit` >
  `DEFAULT_FAILURE_LIMIT`**; a trip flips the card to `blocked` with a `gave_up` event.
- `VALID_BLOCK_KINDS = {dependency, needs_input, capability, transient}` (`kanban_db.py:93`).
  `_route_block` (`:2950-2963`): `dependency` never enters the human `blocked` bucket (it waits in
  `todo` for `recompute_ready`); the other kinds count unblock-loop recurrences and at
  `BLOCK_RECURRENCE_LIMIT` the task routes back to **`triage`** — i.e. to the human, in Plan.

## Assignment and dispatch

- A card with `assignee=NULL` is never claimed **by the dispatcher** (`kanban_db_dispatch.py:1232`);
  manual `hermes kanban claim` works on an unassigned `ready` card.
- The dispatcher auto-assigns unassigned `ready` cards to `kanban.default_assignee` when it encounters
  them (`kanban_db_dispatch.py:1529`, `hermes_cli/kanban.py:630`).
- Workers run as separate subprocesses (`hermes -p <assignee> chat -q`): same profile, **new session,
  own context** — not the interactive chat.
- Knobs: `kanban.dispatch_in_gateway`, `kanban.max_in_progress`,
  `kanban.max_in_progress_per_profile`, `kanban.orchestrator_profile`, `kanban.auto_decompose`,
  `kanban.failure_limit`.
- `hermes project create|add-folder|bind-board` gives non-git work (research, dashboards, document
  sets) a named workspace and a deterministic worktree/branch convention for its board.
- `assignee` is written only at creation or by explicit `hermes kanban assign <id> <profile|none>`.
  `claim` → `running` → `review` → `done` never touch it: a card created without one stays `NULL`
  for its whole lifecycle (verified live: claimed, completed, still `(unassigned)`). `assign` is
  refused while the card is `running` AND claimed — backfill a legacy orphan after `complete`.

## Specifier and decomposer

- `specify` runs the auxiliary task **`triage_specifier`** (`hermes_cli/kanban_specify.py:194`) and
  rewrites title/body to Goal / Approach / **Acceptance criteria** / Out of scope, moving
  `triage → todo` in one transaction (`kanban_db.py:3426-3431`). Its system prompt is core-owned; only
  the model/provider/timeout are configurable (`auxiliary.triage_specifier.*`).
- `decompose` runs **`kanban_decomposer`** (`kanban_decompose.py:314`) and may fan out into a child
  graph. Routing depends on **profile descriptions** (`hermes profile describe <name> --text|--auto`);
  an unknown profile never yields `assignee=None` — it falls back to `kanban.default_assignee`, then the
  root's assignee, then the active profile. `fanout=false` collapses decompose to plain specify.
- With one profile, fan-out produces children that all land back on the same profile — noise. Leave
  `kanban.auto_decompose: false` until there is a second identity to route to.

## Reserved columns (write, do not expect routing)

`tasks.workflow_template_id` and `tasks.current_step_key` exist but the v1 kernel ignores them for
routing while allowing clients to write them (kanban docs, "reserved for v2"). Use them as a free
phase/sub-step label for dashboards and hooks; do not build routing on them.

## Hooks a process layer can use

From `website/docs/developer-guide/plugins/index.md`:

- `pre_llm_call` — fires **once per turn**, before the tool loop; returning `{"context": ...}` injects
  that text into the **current turn's** user message (tail-only, so it stays cache-safe). This is the
  recitation hook: inject one line of state, or return `None` when there is nothing to say.
- `pre_llm_call` — fires **once per turn**, before the tool loop; returning `{"context": ...}` injects
  that text into the **current turn's** user message (tail-only, so it stays cache-safe). This is the
  recitation hook: inject one line of state, or return `None` when there is nothing to say. Real
  callback kwargs: `session_id, user_message, conversation_history, is_first_turn, model, platform` —
  the per-turn tick, so reset per-turn counters here rather than keying them on the turn id.
- `pre_tool_call` — may return `{"action": "block", "message": ...}` to veto a tool call,
  `{"action": "approve"}` to escalate it to the human approval gate, or `{"action": "modify",
  "args": {...}}` to shallow-merge args before dispatch. This is the closure/evidence gate, and the
  only hook that can stop work **before** it happens.
  Real callback kwargs (`hermes_cli/plugins.py:1988`) are richer than the docs table: `tool_name,
  args, task_id, session_id, tool_call_id, turn_id, api_request_id, middleware_trace`. `session_id` is
  what makes a session-scoped gate possible at all ("no card open for THIS session"), and `turn_id`
  scopes a rule to the current turn instead of the whole session — the difference between blocking a
  research turn and blocking the fourth unrelated quick lookup of a long chat.
- `post_tool_call`, `pre_api_request`, `post_api_request`, `on_session_start`, `on_session_end`,
  `on_session_reset` — observers.
- `kanban_task_claimed` (dispatcher process), `kanban_task_completed` / `kanban_task_blocked` (worker
  process) — fire after the board write commits, so a callback always sees durable state. Phase
  bookkeeping belongs here.
- A repo-level `hooks/hooks.json` in the Claude Code shape is not read by Hermes; only
  `provides_hooks:` in the native `plugin.yaml` counts.
