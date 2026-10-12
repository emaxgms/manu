# Auditing whether the manu process actually ran

Use when asked "did we use the process on <job>?" or when work looks uncarded. Answer with numbers read
from the two DBs, never with a summary of what the process is supposed to do. `sqlite3` is enough; run
one query per call rather than one giant heredoc.

## The two databases

| DB | Tables that matter | Holds |
|---|---|---|
| `~/.hermes/kanban.db` | `tasks`, `task_events`, `task_runs`, `task_comments`, `task_attachments` | the board: every card, its status, and every transition |
| `~/.hermes/state.db` | `messages` (+ FTS), `sessions` | the transcript: which tools each session called, and any injected `[manu]` line |

Schema gotchas that waste a call: `tasks` has **no `updated_at`** (it has `created_at`, `started_at`,
`completed_at`, `last_heartbeat_at`); the event log is **`task_events`** (`kind` + `payload`), not
`events`; `sessions.tool_names` is a **hash**, so the session's tool mix comes from `messages.tool_name`.

## Step 1 — did a card exist for the window?

```bash
sqlite3 -header ~/.hermes/kanban.db \
 "SELECT id,title,status,assignee,coalesce(session_id,'NULL'),\
  datetime(created_at,'unixepoch','localtime') created, current_step_key\
  FROM tasks ORDER BY created_at;"
```

Compare `created` against the job's own timestamps. Cards whose `session_id` is `NULL` prove they came
from a shell or the dashboard; a card bound to the session proves it was created from inside it.

## Step 2 — what transitions happened on those cards

```bash
sqlite3 -header ~/.hermes/kanban.db \
 "SELECT id,task_id,run_id,kind,substr(payload,1,120),datetime(created_at,'unixepoch','localtime')\
  FROM task_events ORDER BY created_at DESC LIMIT 40;"
```

`created` → `specified` → `promoted` → `review_requested` → `completed` is a full Plan/Do/Check/Close
pass; `pr_acceptance` payloads carry `ok`/`classification`. A card whose only event is `created` never
left Plan. `task_runs` adds the summarised outcome per attempt (and `metadata` on PR cards).

## Step 3 — did the recitation fire, and what tools did the session actually call?

```bash
sqlite3 ~/.hermes/state.db \
 "SELECT session_id, count(*) FROM messages WHERE content LIKE '%[manu]%'\
  GROUP BY session_id ORDER BY max(rowid) DESC LIMIT 20;"

sqlite3 ~/.hermes/state.db \
 "SELECT tool_name, count(*) FROM messages WHERE session_id='<sid>' AND tool_name IS NOT NULL\
  GROUP BY tool_name ORDER BY 2 DESC;"

sqlite3 ~/.hermes/state.db \
 "SELECT id,role,substr(replace(coalesce(content,''),char(10),' | '),1,300) FROM messages\
  WHERE session_id='<sid>' AND role='user' ORDER BY id;"
```

A `[manu]` hit also appears when a transcript merely *mentions* the tag (e.g. a `skill_view` result), so
check `role`/`tool_name` on the hit before counting it as a recitation. The tool mix is the giveaway: a
research job that only ever called `web_search`, or a document job of `terminal` + `write_file`, ran
with no card and no phase at all.

## Verdict table

| Cards for the window | `[manu]` in the session | Reading |
|---|---|---|
| none | none | **entry failure** — the job ran uncarded; the plugin was never in play, so nothing is broken in it |
| one, `running`/`review` | one per turn | process ran; check evidence and the Check phase from `task_events` |
| one, open | none | binding/status problem — confirm an open status exists at all (`done`/`archived` cards recite nothing) |
| one, blocked early | partial | a real blocker: read `block_kind` and the block recurrences on the card |

Report the fill-in numbers, not adjectives: "0 cards created in the window; 12 `web_search`, no other
tool" is a finding; "the process was not used properly" is not.
