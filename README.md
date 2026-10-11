# manu — four-phase process layer for Hermes

Plan → Do → Check → Close, on a Hermes kanban card, with a rework loop back to Plan.

The phase **is** the card status (`running` = Do, `review` = Check), so nothing is duplicated. This
plugin adds only what Hermes does not already do:

- **Recitation** — one line once per turn while a card whose `session_id` is the current session is
  open, injected into the current turn's user message. Nothing when idle, so quick questions pay
  nothing.
- **Evidence gate** — refuses to close a card with no evidence: no `--result`/`--summary` and no
  attachment. Covers both `kanban_complete` (dispatcher workers) and `hermes kanban complete` typed by
  the agent (in-session work).
- **Phase bookkeeping** — mirrors the phase into the native `current_step_key` column on kanban
  lifecycle events (inert until the dispatcher runs; informational only).

Everything else — dispatch, review, rework handoff (`request-changes --reason`), PR CI contracts
(`--completion-contract`), the retry budget and auto-block, escalation back to `triage` — is core
Hermes and is reused untouched.

## Install

```bash
hermes plugins install emaxgms/manu --enable
# optional: the process skill that drives the plugin
mkdir -p ~/.hermes/skills/software-development/manu
cp skill/SKILL.md ~/.hermes/skills/software-development/manu/SKILL.md
```

Restart the gateway (or start a new CLI session) so the hooks load.

## Verify

```bash
hermes plugins list | grep manu            # enabled
hermes plugins doctor manu                 # runtime contracts
hermes kanban create "smoke" --triage --assignee default
hermes kanban list
```

In a session that created a card, each turn carries exactly one line:

```
[manu] card #3 "smoke" — phase Plan. Close it only with evidence.
```

Closing without evidence is refused:

```
$ hermes kanban complete 3
manu: card(s) 3 have no evidence. Attach the command output or the artifact
(`hermes kanban attach <id> <file>`), or pass `--result "..."`, then close.
```

Unit tests (no Hermes process needed):

```bash
python -m pytest test_manu.py -q
```

## Ceilings (deliberate)

- The gate is a **presence** check on evidence — evidence *quality* is the reviewer's job.
- A human running `hermes kanban complete` in their own shell bypasses the gate on purpose; the
  `pre_tool_call` hook only sees the agent's own tool calls.
- The recitation binds on `tasks.session_id`, which the CLI/dashboard leave NULL: cards created outside
  a session are not recited.
- With one profile the Check phase is reviewed in a fresh session of the same model — same blind spots.
  A different verifier model comes from `hermes kanban set-model`, not from a profile.

## Kill switches

```bash
hermes plugins disable manu      # whole layer off
hermes kanban archive <id>       # stop the recitation for that session
```

## License

MIT
