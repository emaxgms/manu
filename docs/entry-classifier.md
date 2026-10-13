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
covered **45 requests, 2026-10-10 → 2026-10-12** (31 from a telegram session, 14 from the desktop UI),
labelled by the agent and kept as
`~/.hermes/plans/manu-classifier-corpus.json` with the full table in
`~/.hermes/plans/manu-classifier-measurement.md`.

4. The corpus holds **no English requests**, so the second set is synthetic and small: 11 English
   requests and 5 English wh-questions, written from the failure that started the v0.4.1 work, and
   committed as `test_classifier_reads_english_requests` / `test_classifier_ignores_a_wh_question_about_work`
   in `test_manu.py` — they are the regression set for that class, and they need no corpus to re-run.

## Result

| classifier | TP | TN | FP | FN | accuracy | English set |
|---|---|---|---|---|---|---|
| v0.2.0 (word list + 200-char rule) | 27 | 6 | **10** | 2 | 73.3% | — |
| v0.3.0 (measured, Italian corpus) | 29 | 14 | **2** | 0 | **95.6%** | 2/11 asks, 2/5 wh-questions |
| v0.4.1 (Italian corpus unchanged + English stems) | 29 | 14 | **2** | 0 | **95.6%** | **11/11 asks, 5/5 wh-questions** |

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

## The English gap (v0.4.1)

The 45-case corpus is Italian, so **every English request was invisible to the measurement** — and a real
one paid for it: *"Make an analysis trying to order the models provided in Opencode go subscription …"*
(2026-10-13, 12:18) opened no card, so no Plan, no gate, no Check, and the work shipped unreviewed.

- **Nine of eleven English requests got no card.** Stems are prefixes, and the Italian prefix is not the
  English one: `analis`≠`analysis`, `installa`≠`install`, `verifica`≠`verify`, `rimuov`≠`remove`,
  `elimina`/`cancella`≠`delete`, `traduci`≠`translate`. Added: `analys`, `analyz`, `install`, `verif`,
  `remov`, `delet`, `translat` (the longer form replaces the shorter one: `verif` matches *verifica* and
  *verify*).
- **Fixing that alone created the opposite failure.** Two English wh-questions — *"how does the gate decide
  to refuse a write?"*, *"why did no card get created for that request?"* — opened cards, because the ask
  list held the bare English verbs `create`, `write`, `review`, `report`, `compare`, `audit`, `update`,
  `add `, `analyze`, `debug`, `fix`, `summarize`, `explain`. In Italian those entries were imperatives
  (`crea`, `scrivi`, `migliora`); in English the imperative, the infinitive and the participle are the same
  word. The ask list now holds **imperative forms only** (and `crea ` with its trailing space, so *created*
  is not *crea*).
- **A question that is an ask still counts.** `REQUEST_OPENERS` (`puoi`, `potresti`, `possiamo`, `mi fai`,
  `per favore`, `please`, `can you`, `could you`, `would you`, `will you`) marks the sentence as an
  instruction, so its work stems count — *"Can you check the logs?"* opens a card, *"How does the gate
  check a write?"* does not.
- **The Italian corpus did not move:** 29/14/2/0, 95.6%, same two false-positive classes. The fix is
  verified on both sets before it ships, which is the whole point of keeping the corpus around.

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
