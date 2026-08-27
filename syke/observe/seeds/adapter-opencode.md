# opencode

Opencode is a terminal-based AI coding agent. It runs in a terminal, accepts natural-language prompts, and executes tool calls for code editing, file operations, and shell commands. It stores all session data in a single SQLite database. Sessions can have parent-child relationships for subagent tasks, and OpenCode 2.0 adds forked sessions.

OpenCode currently ships two coexisting session schemas in the same database:

- **legacy schema**: `session`, `message`, `part` tables. These stopped receiving updates but still hold historical sessions.
- **v2 schema**: `session_v2`, `session_message`. This is the active schema for current sessions.

Both must be read and merged, with `session_v2` winning on ID conflicts (see "Deduplication" below).

## Where

```
~/.local/share/opencode/opencode.db
```

The catalog discovery pattern is `opencode*.db`, covering filenames that
start with `opencode` and end with `.db` (including `opencode.db` and channel
variants). Its equivalent filename regex is `^opencode.*\.db$`.

> WAL/SHM sidecar files (`opencode.db-wal`, `opencode.db-shm`) are NOT artifacts.
> Never ingest them, never copy them, never include them in discovery results.
> The DB is WAL-mode and actively written by opencode — open it read-only and let
> SQLite resolve the WAL through the normal read path.

## Read safety (mandatory)

The database is live and ~1 GB+. All access MUST be:

- **Read-only.** Open with the SQLite URI read-only flag plus a busy timeout:
  `file:<abs path>?mode=ro` and set a busy timeout (e.g. 5000 ms) so a
  concurrent opencode writer doesn't instantly fail the read. Do NOT use
  `immutable=1` — it makes SQLite ignore the WAL and you will silently miss the
  most recent sessions.
- **Parameterized.** Never interpolate IDs or timestamps as string literals.
- **Bounded.** Every query has `LIMIT`. Page long histories instead of loading
  all rows. Truncate long text fields (e.g. `substr(data, 1, N)`) when you only
  need metadata.
- **Never blocking.** Use short transactions; never hold a lock on the DB while
  doing other work. If a read fails with `SQLITE_BUSY`, back off and retry, or
  skip and report.

Example connection (Python):

```python
import sqlite3
from urllib.parse import quote

db = "/home/user/.local/share/opencode/opencode.db"  # absolute path
conn = sqlite3.connect(f"file:{quote(db)}?mode=ro", uri=True, timeout=5.0)
conn.execute("PRAGMA busy_timeout = 5000")
```

## Never-query list

The same database contains sensitive tables. Do NOT query, join against, or
mention row contents from any of these. A query plan touching them is a
privacy violation:

- `credential`
- `account`
- `account_state`
- `control_account`
- `event`
- `event_sequence`
- `session_pending`
- `session_inbox`
- `session_share` (contains share secrets)
- any other table not in the allowlist below

The ONLY tables you may read: `session_v2`, `session_message`, `session`
(legacy), `message` (legacy), `part` (legacy), `project`, `workspace`.
The observed `workspace` columns are `provider`, `binding`, `created_at`, and
`last_used_at`; do not assume legacy/documentation-only `type`, `name`,
`directory`, or `extra` columns.

Additionally, even inside the allowlist: assistant `reasoning` content items
may be encrypted/opaque blobs from the provider — never attempt to decrypt or
dump raw reasoning blobs into output. Summarize that reasoning occurred; quote
at most a short snippet of plaintext reasoning.

## Schema: v2 (authoritative)

### Table: `session_v2`

| Column | Notes |
| --- | --- |
| `id`, `project_id`, `workspace_id` | Identifiers / FKs |
| `parent_id` | Null for root sessions; set for subagent sessions |
| `fork_session_id`, `fork_boundary` | V2-only fork lineage (which session this forked from, and the message boundary) |
| `slug`, `directory`, `path` | Naming / working directory; `path` is the session storage path (not a chat artifact) |
| `title`, `version`, `share_url` | Display + share metadata |
| `summary_additions`, `summary_deletions`, `summary_files`, `summary_diffs` | Code-change stats |
| `metadata` | JSON blob |
| `cost`, `tokens_input`, `tokens_output`, `tokens_reasoning`, `tokens_cache_read`, `tokens_cache_write` | Usage/cost |
| `revert`, `permission` | JSON blobs |
| `agent`, `model` | Which agent/model ran the session |
| `time_created`, `time_updated`, `time_compacting`, `time_archived`, `time_suspended` | ms-epoch timestamps; `time_updated` is the live recency signal |
| `resume_attempts`, `time_idle`, `time_viewed`, `idle_outcome` | Session lifecycle bookkeeping |

### Table: `session_message`

| Column | Notes |
| --- | --- |
| `id`, `session_id` | Message ID / FK to `session_v2` |
| `type` | **Role lives here, not in `data`.** Values: `user`, `assistant`, `synthetic`, `system`, `compaction` |
| `seq` | Integer ordering key; no NULLs; `(session_id, seq)` is indexed |
| `time_created`, `time_updated` | ms epoch |
| `data` | JSON payload, shape depends on `type` |

Ordering messages: `ORDER BY seq ASC, time_created ASC, id ASC` (seq is primary; time/id break ties).

`data` for `type='assistant'`: keys include `agent`, `model`, `content[]`,
`snapshot`, `finish`, `cost`, `tokens`, `time`, `error`, `providerState`.
`content[]` item types:

- `{type: "text", text}` — assistant answer text
- `{type: "reasoning", ...}` — internal reasoning; treat as opaque summary signal
- `{type: "tool", state: {...}}` — tool call. `state.status` is `success` (or
  in-progress/failed variants) and output lives in **`state.content`** (NOT
  `state.output` — that's legacy only). Errors surface in `state.error`.

`data` for `type='user'`: keys `text`, `files`, `agents`, `time`.

`data` for `type='synthetic'`: keys `text`, `time`, `metadata`, `description`.
`data` for `type='system'`: keys `time`, `text`, `description`.
`type='compaction'`: context-compaction placeholder.

**Conversation reconstruction uses only `user` and `assistant` messages.**
Skip `compaction`, `synthetic`, and `system` messages for conversation body,
but do NOT drop the session — a session consisting only of compaction/system
messages after filtering still exists and counts for recency. Malformed JSON
in `data` or unknown content item types must be tolerated: skip the item, keep
reading the session.

## Schema: legacy (`session` / `message` / `part`)

`session` (legacy) actually carries more than legacy docs imply: its columns
include `id`, `project_id`, `parent_id`, `slug`, `directory`, `title`,
`version`, `share_url`, `summary_additions/deletions/files`, `summary_diffs`,
`revert`, `permission`, `agent`, `model`, `cost`, `tokens_input`,
`tokens_output`, `tokens_reasoning`, `tokens_cache_read`,
`tokens_cache_write`, `path`, and `time_created` / `time_updated` /
`time_archived`. (No fork columns — forks are v2-only.) Note: `time_updated`
stopped advancing for all legacy rows after the migration point.

`message`: `id`, `session_id`, `time_created`, `time_updated`, `data` (JSON).
The `data` JSON carries a `role` field (`user` / `assistant`), `model`,
`tokens`, `cost`, `agent`, `finish`, `path`, etc.

`part`: `id`, `message_id`, `session_id`, `time_created`, `time_updated`,
`data` (JSON). `data.type` ∈ `text` / `reasoning` / `tool` / `patch` /
`file` / `compaction` / `step-finish`. Legacy tool parts store output in
**`state.output`** (this differs from v2). Skip `compaction` and
`step-finish` parts for conversation body.

## Deduplication (legacy + v2 coexistence)

Session IDs overlap between `session` and `session_v2`. Rules:

1. **v2 wins on conflict.** If an ID appears in both tables, use the
   `session_v2` row for title/metadata/cost/agent.
2. **Legacy fills pre-migration history.** Messages for a split session may
   exist in `message`/`part` up to the migration point and in
   `session_message` afterwards. Union both message streams; order legacy
   messages by `time_created ASC, id ASC` and v2 messages by
   `seq, time_created, id`; v2 messages sort after legacy when interleaved.
3. **Recency is the union.** For listing/sorting by recency use
   `max(time_updated across both tables per session_id)` — a session frozen in
   legacy can still be live in v2.

## Query recipes (read-only, bounded)

Recent sessions across both schemas, deduped with v2 winning, with legacy
history folded in:

```sql
WITH merged AS (
  SELECT id, title, time_created, time_updated, agent, model,
         2 AS src
  FROM session_v2
  UNION ALL
  SELECT id, title, time_created, time_updated, agent, model,
         1 AS src
  FROM session
),
chosen AS (
  SELECT *,
         ROW_NUMBER() OVER (PARTITION BY id ORDER BY src DESC, time_updated DESC) AS rn
  FROM merged
),
recency AS (
  SELECT id, MAX(time_updated) AS latest_time_updated
  FROM merged
  GROUP BY id
)
SELECT c.id, c.title, c.time_created, recency.latest_time_updated,
       c.agent, c.model
FROM chosen AS c
JOIN recency ON recency.id = c.id
WHERE c.rn = 1
ORDER BY recency.latest_time_updated DESC, c.id ASC
LIMIT ?;
```

Messages for one session (v2):

```sql
SELECT seq, type, time_created, data
FROM session_message
WHERE session_id = ?
ORDER BY seq ASC, time_created ASC, id ASC
LIMIT ?;
```

Legacy messages for the same session (history fill):

```sql
SELECT m.time_created, json_extract(m.data, '$.role') AS role, m.id
FROM message m
WHERE m.session_id = ?
ORDER BY m.time_created ASC, m.id ASC
LIMIT ?;
```

Tool-call output (v2): from the message `data` JSON, walk
`$.content[*]` items where `type = 'tool'` and read `state.content`; check
`state.error` for failures.

For metadata previews, select `substr(data, 1, ?)` and cap each rendered text
or tool content item to a fixed character budget. Page conversation reads with
`LIMIT ?`; never load an entire live session or emit unbounded tool output.

## Message / turn structure

- A **user turn** = one `type='user'` row (v2) or legacy `role='user'`.
- An **assistant turn** = one or more consecutive `assistant` messages
  containing text/reasoning/tool items. Tool results are embedded in the same
  message's `content[]`, not separate rows.
- Parent/child: `session_v2.parent_id` non-null → subagent session.
  `fork_session_id` + `fork_boundary` describe a fork (v2-only concept).
- Print nothing from `reasoning` items except a length/type note unless the
  user explicitly asks for reasoning text.

## What sessions contain

Each session records a multi-turn conversation: user prompts, assistant text,
reasoning markers, tool invocations (with input args and output content),
token/cost usage, model/agent selection, code-change summary stats (additions,
deletions, files), fork lineage, and compaction events.

## Harness memory

OpenCode reads context from these sources:

- `AGENTS.md` in the project root (project instructions, injected into context)
- `~/.config/opencode/AGENTS.md` (global instructions)
- Project-level config under `.opencode/`

Syke writes memory back through those surfaces only. It never opens
`opencode.db` for writes.

## Privacy boundaries (summary)

- Read-only, parameterized, `LIMIT`-bounded queries against the allowlisted
  tables only.
- Never query the never-query list (credentials, accounts, events, share
  secrets, pending/inbox).
- Never emit raw reasoning blobs, full tool outputs of arbitrary length, share
  secrets, or any row from a never-query table — truncate and summarize.
- Never write to the opencode DB from Syke.
