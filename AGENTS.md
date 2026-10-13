# Working on manu — agent notes

Rules for an agent (human or model) working in this repository. They describe this project only.

## Ship flow: commit to `main`, no pull request

- **Direct commits to `main`.** Push to `origin/main` (`git@github.com:emaxgms/manu.git`). This repository
  does **not** take pull requests; do not open one unless Emanuele explicitly asks for it in that session.
- **One shipped behaviour change = one commit**, with the version in `plugin.yaml` bumped and a matching
  entry in the changelog at the end of `skill/SKILL.md`. The version in `plugin.yaml` is what
  `hermes plugins list` reports — keep it honest.
- **Prompt wording is a file edit**: everything the plugin injects lives in `prompts/*.md` and is read at
  call time. Edit the `.md`, not the Python.

## What runs where

- **The checkout is the deployed plugin.** `~/.hermes/plugins/manu` is this git working tree; Hermes loads
  `plugin.yaml` + `__init__.py` from here. There is no separate install step and no build.
- **`__init__.py` and `plugin.yaml` are memoized per process** (`hermes_cli/plugins.py`
  `discover_and_load`): after editing them, every already-running host keeps the **old** code until it is
  restarted. `hermes-gateway` and `hermes-dashboard` are systemd system units, and `sudo` needs a password
  on this host — the restart is the one step an agent cannot perform; hand it to Emanuele (`/restart`, or
  `sudo systemctl restart hermes-gateway hermes-dashboard`). Then verify on a **new** session: a committed
  fix is not a live fix.
- `prompts/*.md`, `skill/SKILL.md` and `docs/*` need no restart.

## CI

- **There is no CI service on this repository — the gate is the local suite.** Before any push:

  ```bash
  ~/.hermes/hermes-agent/venv/bin/python -m pytest test_manu.py -q   # currently 49 tests
  ```

  It must be green in a plain shell *and* inside a delegated child (the suite clears
  `HERMES_KANBAN_TASK` / `HERMES_CRON_SESSION` / `HERMES_DELEGATED_CHILD_CONTEXT` itself).
- **Classifier changes are measured, not argued.** `looks_like_work()` is scored against the labelled
  45-request corpus that lives outside the repo
  (`~/.hermes/plans/manu-classifier-corpus.json`, method in `docs/entry-classifier.md`) **before and
  after** the change, and the numbers go into that doc. The corpus is Italian; the English class is
  covered by `test_classifier_reads_english_requests` /
  `test_classifier_ignores_a_wh_question_about_work` in `test_manu.py`. Never trade a false negative
  (real work gets no card) for a false positive (a card nobody asked for) without saying so in the doc.
- `hermes kanban diagnostics` for the board after touching gate or hook code (it is `diagnostics`/`diag`,
  not `doctor`), and `hermes doctor` for the Hermes install itself.

## Evidence

- Every change is verified against a **throwaway board** (`HERMES_KANBAN_DB` pointing at a temp file), not
  the live one, and the command output is what gets attached to the card. Claims without output are not
  evidence — that is also what `manu_review` will tell you.
