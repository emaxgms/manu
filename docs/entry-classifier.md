# The entry classifier, measured

`looks_like_work()` decides whether a chat message opens a kanban card. It is a word list plus two
structural rules — not a model call — so the only honest way to tune it is to measure it against real
traffic and publish the numbers.

## Method

1. Pull the most recent real user requests out of `~/.hermes/state.db`:
   `messages.role='user'`, sessions whose `source` is `telegram`/`tui`/`desktop`, excluding
   `Subagent:*` sessions and the gateway-generated classes (`[INTERNAL NOTIFICATION …]`,
   `[ASYNC DELEGATION BATCH COMPLETE …]`, `[OUT-OF-BAND USER MESSAGE …]`, image/document wrappers,
   goal-mode continuations, and the plugin's own acceptance probes).
2. Label each one **WORK** (the reply must produce or modify an artifact, or run an investigation with a
   reported conclusion) or **quick** (a question, a correction, a decision, feedback).
3. Run `looks_like_work()` over the labelled set and count four numbers: true/false positives and
   negatives. False positives are the expensive ones — each is a card nobody asked for; false negatives
   are the dangerous ones — real work that never gets a card.

The corpus itself stays outside this repository (it is the user's own chat log). The run recorded below
covered **45 requests, 2026-09-30 → 2026-10-12**, labelled by the agent and kept as
`~/.hermes/plans/manu-classifier-corpus.json` with the full table in
`~/.hermes/plans/manu-classifier-measurement.md`.

## Result

| classifier | TP | TN | FP | FN | accuracy |
|---|---|---|---|---|---|
| v0.2.0 (word list + 200-char rule) | 27 | 6 | **10** | 2 | 73.3% |
| v0.3.0 (this revision) | 29 | 14 | **2** | 0 | **95.6%** |

## What the numbers said

- **Length is not work.** The old rule "longer than 200 chars ⇒ work" produced 8 of the 10 false
  positives: long complaints, long questions and long requirement statements all opened cards. It
  contributed almost nothing on the true side (a real request that long carries an action verb anyway).
  The rule is gone; `WORK_MIN_CHARS` is now only the "too short to be a request" floor (20 chars).
- **A work verb inside a question is not an instruction.** `"come implementano questi flussi?"` and
  `"chi l'ha usato questo meccanismo?"` are questions. The classifier now splits on sentence boundaries
  and ignores work stems inside a question sentence — unless an explicit ask (`fai`, `fammi`,
  `puoi verificare`, `valuta`) appears anywhere, which is what keeps `"puoi verificare cosa abbiamo
  fatto?"` a real card.
- **Substring matching was the other half.** `preview` contains `review`, `generative-ai` contains
  `genera`, `implementano` contains `implement`. Stems are now anchored at a word start.
- **Some stems were pure noise.** Bare `build`, `setup`, `genera`/`generate` matched only questions and
  rants in the corpus, so they were dropped. (`"make a build script"` still matches on `script`.)
- **Missing change verbs were the two false negatives.** `"Voglio modificare una parte del processo"` and
  `"Io pulirei anche le skill inutili"` opened nothing. `modific`, `togli`, `rimuov`, `elimina`,
  `cancella`, `pulir`, `pulisc`, `miglior`, `fix`, `salva`, `check`, `verifica` were added.

## The remaining two false positives — the ceiling

- A long declarative rant that happens to contain `ricerca` / `creare` in a statement.
- A **negated** action: `"non c'è bisogno di riscriverlo"` reads as an instruction to a word list.

Both need semantics, not more words. `looks_like_work()` is a single function with no callers inside the
plugin, so replacing it with one auxiliary call (`auxiliary.<task>` route) is the upgrade path — the
measurement above is the regression set any replacement has to beat. The labelled cases ship as
`test_classifier_regressions_from_the_labelled_corpus` in `test_manu.py`, in anonymised form.

## Reproducing

```bash
# the labelled corpus (local, never committed) and the table
python - <<'PY'
import json, sqlite3
con = sqlite3.connect("file:%s?mode=ro" % (__import__("pathlib").Path.home()/".hermes"/"state.db"), uri=True)
...  # see the query in the method above
PY

# the regression cases that must keep passing
~/.hermes/hermes-agent/venv/bin/python -m pytest test_manu.py -q -k classifier
```
