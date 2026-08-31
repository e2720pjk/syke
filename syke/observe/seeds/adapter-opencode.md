# opencode

OpenCode is a terminal-based coding agent. Its conversation history is in a
SQLite database. Syke keeps OpenCode at the source: this file is an
**LLM-first adapter guide**, not a Python ingest parser. During an observation
cycle the agent uses the guide and read-only `sqlite3`/shell queries, reasons
over bounded results, and writes only Syke-owned memory to `syke.db`.

The live database can contain either schema or both schemas. Do not assume that
either schema is present, and do not treat session-level deduplication as
message-level deduplication.

## Where and discovery

The catalog discovers regular files matching `opencode*.db` under:

```text
~/.local/share/opencode/
```

The equivalent filename regular expression is `^opencode.*\.db$`. This includes
`opencode.db` and channel variants such as `opencode-channel.db`; it excludes
`opencode.db-wal`, `opencode.db-shm`, other sidecars, and unrelated files.
WAL/SHM files are not artifacts: never ingest, copy, open directly, or report
their contents.

## Read boundary and safety

The database is a live WAL-mode database (the primary file may be about 1.2 GB).
Every read must be:

- **Read-only and WAL-aware.** Open an absolute path with SQLite URI
  `file:<quoted-absolute-path>?mode=ro`, `uri=True`, and a finite timeout; set
  `PRAGMA busy_timeout = 5000`. Never add `immutable=1`: it ignores the WAL and
  can hide recent sessions.
- **Parameterized.** Bind session IDs, message IDs, timestamps, page sizes,
  offsets, and truncation lengths. Never interpolate values into SQL.
- **Short and bounded.** Every data query has a parameterized `LIMIT`; page
  with a stable order and `OFFSET` (or a remembered key) rather than loading a
  whole history. Use `substr(..., 1, ?)` for raw JSON previews and cap every
  rendered text, reasoning note, tool input, tool output, error, path, and
  metadata value. A bounded output is required even when the source row is
  valid JSON.
- **Non-blocking.** Keep transactions short. On `SQLITE_BUSY`/locked reads,
  back off and retry briefly or skip/report the page; never hold a database
  lock while doing LLM work.

Example connection:

```python
import sqlite3
from urllib.parse import quote

db = "/home/user/.local/share/opencode/opencode.db"
conn = sqlite3.connect(f"file:{quote(db)}?mode=ro", uri=True, timeout=5.0)
conn.execute("PRAGMA busy_timeout = 5000")
```

Never write to this database. Never emit or copy credentials, secrets, private
chat beyond the bounded evidence needed for the current observation, raw
reasoning blobs, or unbounded JSON/tool output.

## Privacy allowlist

Only inspect these tables, and only for the fields needed below:

- v2: `session_v2`, `session_message`
- legacy: `session`, `message`, `part`
- optional session metadata: `project`, `workspace`

Do not query, join, or dump rows from credential/account/event/pending/inbox or
share-secret tables (including `credential`, `account`, `account_state`,
`control_account`, `event`, `event_sequence`, `session_pending`,
`session_inbox`, and `session_share`). Do not probe arbitrary tables. The
observed `workspace` columns are `provider`, `binding`, `created_at`, and
`last_used_at`; do not assume documentation-only columns such as `type`,
`name`, `directory`, or `extra`.

Reasoning content may be encrypted or opaque. Record only a bounded note that
reasoning occurred (and at most a short, explicitly safe plaintext snippet),
never the raw blob.

## Detect the schema before querying

First query SQLite's catalog, then branch in the agent based on the returned
set. This catalog query is metadata only and has a `LIMIT`:

```sql
SELECT name
FROM sqlite_master
WHERE type = 'table'
  AND name IN ('session_v2', 'session_message', 'session', 'message', 'part',
               'project', 'workspace')
ORDER BY name
LIMIT ?;
```

Never issue a query that names a table not returned by detection. In
particular, do not use a CTE or `UNION ALL` that mentions both schemas when one
is absent. A pure legacy database may have only `session`/`message`/`part`; a
pure v2 database may have only `session_v2`/`session_message`; a mixed database
may have all five conversation tables. Detect each table independently and
read only the available stream.

## Schemas

### v2 session metadata: `session_v2`

Useful columns include `id`, `project_id`, `workspace_id`, `parent_id`,
`fork_session_id`, `fork_boundary`, `slug`, `directory`, `path`, `title`,
`version`, `share_url`, summary fields, `metadata`, cost/token fields,
`revert`, `permission`, `agent`, `model`, and lifecycle timestamps including
`time_created`, `time_updated`, `time_archived`, `time_suspended`, and
`time_idle`. Fork fields are v2-only. Treat `time_updated` as the live recency
signal when it is present.

### v2 messages: `session_message`

Columns are `id`, `session_id`, `type`, `seq`, `time_created`, `time_updated`,
and JSON `data`. **The role is `type`, not a role field in `data`.** The
observed types include `user`, `assistant`, `synthetic`, `system`, and
`compaction`; tolerate future types.

- A user payload may contain `text`, `files`, `agents`, and `time`.
- An assistant payload may contain `content[]`, `agent`, `model`, `snapshot`,
  `finish`, `cost`, `tokens`, `time`, `error`, and `providerState`.
- A content item `{type: "text", text}` is assistant text.
- `{type: "reasoning", ...}` is a reasoning marker only; do not print its
  payload.
- `{type: "tool", state: {...}}` is a tool call. Its `state.content[]` is an
  array of result blocks, not a scalar string: retain bounded `{type: "text",
  text}` blocks and, when useful, bounded `{type: "file", uri, mime, name}`
  metadata. A failed tool has an error object; extract only
  `state.error.message`, never the whole error object.
- Tool `state.status` is normally **`completed`** or **`error`**. Preserve and
  label an unknown future status; never normalize it to a known terminal state,
  and never infer semantics for a value not observed in this schema.

The v2 stream's intrinsic order is `ORDER BY seq ASC, time_created ASC, id
ASC`; `seq` is primary. Do not substitute row insertion order.

### legacy metadata and messages: `session`, `message`, `part`

`session` has `id`, project/parent/path/title/version/share fields, summary and
cost/token fields, `agent`, `model`, and `time_created`/`time_updated`/
`time_archived`. Legacy `time_updated` stopped advancing at migration, so it
cannot be the only recency signal.

`message` has `id`, `session_id`, `time_created`, `time_updated`, and JSON
`data`. Its payload has a `role` (`user`/`assistant`) and may contain message
metadata or fallback text. `part` has `id`, `message_id`, `session_id`,
time fields, and JSON `data`. Part types include `text`, `reasoning`, `tool`,
`patch`, `file`, `compaction`, and `step-finish`.

Legacy tool output is in **`state.output`** (not v2's `state.content`). Legacy
tool statuses commonly use `completed`/`error`; tolerate unknown statuses
without inventing a meaning. Skip `compaction` and `step-finish` parts in the
conversation body. Reconstruct legacy-only messages by reading their parts
keyed by `message_id` after the message query; cap each part before rendering.
Do not SQL-join legacy messages/parts to v2 messages.

## Session metadata: the only cross-schema union

A SQL `JOIN` or `UNION ALL` is permitted for **session metadata only**, after
schema detection confirms the referenced tables. It is not a conversation
merge. When both `session_v2` and `session` exist, use separate metadata
branches or this mixed-schema CTE; execute it only in the both-present branch.
The form below also requires the separately detected `message` table for
legacy activity recency; if `message` is absent, omit `legacy_activity` and
use `time_created` for legacy rows in `recency`.

```sql
WITH merged AS (
  SELECT id, parent_id, fork_session_id, fork_boundary,
         substr(title, 1, ?) AS title, time_created, time_updated,
         substr(agent, 1, ?) AS agent, substr(model, 1, ?) AS model,
         2 AS source_rank
  FROM session_v2
  UNION ALL
  SELECT id, NULL AS parent_id, NULL AS fork_session_id,
         NULL AS fork_boundary,
         substr(title, 1, ?) AS title, time_created, time_updated,
         substr(agent, 1, ?) AS agent, substr(model, 1, ?) AS model,
         1 AS source_rank
  FROM session
), chosen AS (
  SELECT merged.*,
         ROW_NUMBER() OVER (
           PARTITION BY id
           ORDER BY source_rank DESC, time_updated DESC,
                    time_created DESC, id ASC
         ) AS row_number
  FROM merged
), legacy_activity AS (
  SELECT session_id AS id,
         MAX(COALESCE(time_updated, time_created)) AS latest_time
  FROM message
  GROUP BY session_id
  ORDER BY latest_time DESC, session_id ASC
  LIMIT ?
), recency AS (
  SELECT merged.id,
         MAX(
           CASE WHEN merged.source_rank = 1
                THEN COALESCE(legacy_activity.latest_time, merged.time_created)
                ELSE COALESCE(merged.time_updated, merged.time_created)
           END
         ) AS latest_time
  FROM merged
  LEFT JOIN legacy_activity ON legacy_activity.id = merged.id
  GROUP BY merged.id
)
SELECT chosen.id, chosen.parent_id, chosen.fork_session_id,
       chosen.fork_boundary, chosen.title, chosen.time_created,
       recency.latest_time, chosen.agent, chosen.model
FROM chosen
JOIN recency ON recency.id = chosen.id
WHERE chosen.row_number = 1
ORDER BY recency.latest_time DESC, chosen.id ASC
LIMIT ?;
```

Bind one fixed metadata budget to each `substr` placeholder, a bounded page
size to `legacy_activity`, and a separate bounded page size to the final
`LIMIT`; do not substitute unbounded defaults. For a v2-only database, keep
the same field bounds rather than selecting raw metadata:

```sql
SELECT id,
       parent_id,
       fork_session_id,
       fork_boundary,
       substr(title, 1, ?) AS title,
       time_created,
       time_updated,
       substr(agent, 1, ?) AS agent,
       substr(model, 1, ?) AS model
FROM session_v2
ORDER BY time_updated DESC, id ASC
LIMIT ?;
```

For a legacy-only database with `message` detected, use a bounded activity
projection instead of trusting the migrated session timestamp:

```sql
WITH activity AS (
  SELECT session_id AS id,
         MAX(COALESCE(time_updated, time_created)) AS latest_time
  FROM message
  GROUP BY session_id
  ORDER BY latest_time DESC, session_id ASC
  LIMIT ?
)
SELECT session.id,
       NULL AS parent_id,
       NULL AS fork_session_id,
       NULL AS fork_boundary,
       substr(session.title, 1, ?) AS title,
       session.time_created,
       session.time_updated,
       substr(session.agent, 1, ?) AS agent,
       substr(session.model, 1, ?) AS model
FROM session
LEFT JOIN activity ON activity.id = session.id
ORDER BY COALESCE(activity.latest_time, session.time_created) DESC,
         session.id ASC
LIMIT ?;
```

If `message` is absent, use the same bounded projection but order by
`time_created DESC, id ASC`; legacy `session.time_updated` stopped advancing
at migration and must not be the authoritative recency signal. The mixed CTE
makes v2 metadata authoritative for overlapping session IDs, while its
activity-aware `recency.latest_time` uses message activity for legacy rows and
live `time_updated` for v2 rows. A metadata ID choice does **not** choose,
deduplicate, or order message rows.

Optional `project`/`workspace` joins are likewise session-metadata-only and may
be used only after those tables are detected. Do not use their unverified
columns, and do not put a message table in those joins.

## Message reads and keyed merge (never raw UNION/JOIN)

For a selected session, read each available message stream separately. Never
`JOIN` or `UNION ALL` legacy chat rows with v2 chat rows. `LIMIT` and page
parameters are mandatory in every row query.

### v2 field-level JSON extraction

**Never select `substr(data, 1, ?)` as `data_preview` and then parse that
preview as v2 JSON.** A valid row can put large metadata, snapshots, or tool
results before `content[]`; truncating the complete document makes the entire
row look malformed and drops it. Instead, use SQLite JSON1 to validate the
row, enumerate arrays, and cap each extracted scalar field independently.

First read bounded row identity and the v2 user text field. The `json_valid`
branch keeps malformed rows non-fatal:

```sql
-- Run only when session_message was detected.
SELECT id, seq, type, time_created, time_updated,
       CASE WHEN json_valid(data) THEN
              CASE WHEN json_type(data, '$.text') = 'text'
                   THEN substr(json_extract(data, '$.text'), 1, ?)
              END
       END AS text_preview
FROM session_message
WHERE session_id = ?
ORDER BY seq ASC, time_created ASC, id ASC
LIMIT ? OFFSET ?;
```

For assistant `data.content[]`, enumerate one outer item at a time. The ID
markers below are illustrative; generate one bound `?` per ID returned by the
bounded row query. Do not select the full `data.content` array:

```sql
SELECT message.id,
       CAST(content.key AS INTEGER) AS content_index,
       CASE WHEN content.type = 'object'
            THEN json_extract(content.value, '$.type')
       END AS content_type,
       CASE WHEN content.type = 'object' THEN
              CASE WHEN json_extract(content.value, '$.type') = 'text'
                   AND json_type(content.value, '$.text') = 'text'
                   THEN substr(json_extract(content.value, '$.text'), 1, ?)
              END
       END AS text_preview,
       CASE WHEN content.type = 'object' THEN
              CASE WHEN json_extract(content.value, '$.type') = 'tool'
                   AND json_type(content.value, '$.state.status') = 'text'
                   THEN substr(json_extract(content.value, '$.state.status'), 1, ?)
              END
       END AS tool_status,
       CASE WHEN content.type = 'object' THEN
              CASE WHEN json_extract(content.value, '$.type') = 'tool'
                   AND json_type(content.value, '$.state.error.message') = 'text'
                   THEN substr(
                     json_extract(content.value, '$.state.error.message'), 1, ?
                   )
              END
       END AS error_message
FROM session_message AS message
JOIN json_each(
  CASE WHEN json_valid(message.data) THEN message.data ELSE '{}' END,
  '$.content'
) AS content
WHERE message.session_id = ?
  AND message.id IN (?, ?)
ORDER BY message.id ASC, CAST(content.key AS INTEGER) ASC
LIMIT ?;
```

For each outer tool item, enumerate `state.content[]` separately. Retain only
bounded text blocks and the bounded file metadata fields shown here; skip
unknown block types and missing/non-string fields:

```sql
SELECT message.id,
       CAST(content.key AS INTEGER) AS content_index,
       CAST(state_content.key AS INTEGER) AS state_content_index,
       CASE WHEN state_content.type = 'object'
            THEN json_extract(state_content.value, '$.type')
       END AS state_content_type,
       CASE WHEN state_content.type = 'object' THEN
              CASE WHEN json_extract(state_content.value, '$.type') = 'text'
                   AND json_type(state_content.value, '$.text') = 'text'
                   THEN substr(json_extract(state_content.value, '$.text'), 1, ?)
              END
       END AS state_text_preview,
       CASE WHEN state_content.type = 'object' THEN
              CASE WHEN json_extract(state_content.value, '$.type') = 'file'
                   AND json_type(state_content.value, '$.uri') = 'text'
                   THEN substr(json_extract(state_content.value, '$.uri'), 1, ?)
              END
       END AS uri_preview,
       CASE WHEN state_content.type = 'object' THEN
              CASE WHEN json_extract(state_content.value, '$.type') = 'file'
                   AND json_type(state_content.value, '$.mime') = 'text'
                   THEN substr(json_extract(state_content.value, '$.mime'), 1, ?)
              END
       END AS mime_preview,
       CASE WHEN state_content.type = 'object' THEN
              CASE WHEN json_extract(state_content.value, '$.type') = 'file'
                   AND json_type(state_content.value, '$.name') = 'text'
                   THEN substr(json_extract(state_content.value, '$.name'), 1, ?)
              END
       END AS name_preview
FROM session_message AS message
JOIN json_each(
  CASE WHEN json_valid(message.data) THEN message.data ELSE '{}' END,
  '$.content'
) AS content
JOIN json_each(
  CASE
    WHEN content.type = 'object' THEN
      CASE WHEN json_extract(content.value, '$.type') = 'tool'
           THEN content.value ELSE '{}' END
    ELSE '{}'
  END,
  '$.state.content'
) AS state_content
WHERE message.session_id = ?
  AND message.id IN (?, ?)
ORDER BY message.id ASC,
         CAST(content.key AS INTEGER) ASC,
         CAST(state_content.key AS INTEGER) ASC
LIMIT ?;
```

Bind one fixed text/file metadata budget to every `substr` placeholder and a
separate bounded item budget to each JSON-array query. Group
these extracted rows by `message.id` and the two array indexes, preserving
array order. `json_each` is an extractor, not permission to emit an
unbounded JSON value. The v2 row page still stops at its bounded `LIMIT` /
`OFFSET` budget, and malformed rows or unknown items are skipped.

### Legacy field reads

The legacy fallback keeps its separate bounded preview/part recipe:

```sql
-- Run only when message was detected.
SELECT id, time_created, time_updated,
       CASE WHEN json_valid(data) THEN json_extract(data, '$.role') END AS role,
       CASE WHEN json_valid(data)
            THEN substr(json_extract(data, '$.text'), 1, ?)
       END AS text_preview,
       substr(data, 1, ?) AS data_preview
FROM message
WHERE session_id = ?
ORDER BY time_created ASC, id ASC
LIMIT ? OFFSET ?;
```

The agent must maintain a bounded map keyed by stable `message.id`:

1. Insert v2 rows into `v2_by_id` in their `seq/time_created/id` order.
2. Insert legacy rows into a legacy list ordered by `time_created/id`, but keep
   only IDs absent from `v2_by_id`. If an ID occurs in both streams, the v2
   `session_message` row wins **even when the two JSON payloads differ**; do
   not inspect or emit the discarded legacy payload or its parts.
3. For each retained legacy-only ID, query its `part` rows separately and
   reconstruct that message in `part.time_created/id` order. Bind the IDs as
   `IN` parameters (never interpolate values), and keep the query bounded:

   ```sql
   -- Run only when part was detected, for a bounded page of legacy-only IDs.
   SELECT id, message_id, time_created, time_updated,
          substr(data, 1, ?) AS data_preview
   FROM part
   WHERE session_id = ?
     AND message_id IN (?, ?)
   ORDER BY message_id ASC, time_created ASC, id ASC
   LIMIT ? OFFSET ?;
   ```

   Generate one `?` per ID in the bounded legacy-only page (the two markers
   above are illustrative), and bind every ID. A generated placeholder list is
   okay only for IDs already obtained from
   bound query results; every ID remains a bound value. If the page has no IDs,
   skip the query. If `part` is absent, retain the legacy message's bounded
   metadata/text only and note that parts were unavailable.
4. Parse only bounded/validated JSON. Skip malformed rows and unknown content
   items instead of aborting the session. For user messages, use the v2
   `data.text` or a bounded legacy text fallback. For assistant messages,
   collect bounded text, reasoning markers, and tool summaries. v2 tools read
   `state.content`; legacy tools read `state.output`. Cap tool input, output,
   errors, and all text before adding them to evidence.
5. Merge the two retained lists with an explicit, reproducible cross-stream key;
   do not claim that v2 is always after (or before) legacy. Keep the intrinsic
   stream orders above, and use comparable event time for cross-stream
   placement, with source and stream keys only as deterministic tie-breakers:

   ```text
   v2 row key          = (event_time, 0, seq, time_created, id)
   legacy-only row key = (event_time, 1, 0, time_created, id)
   event_time          = time_created, or 0 when it is absent/non-numeric
   ```

   Sort the final bounded page by this key. Thus equal-time v2 rows use `seq`
   and equal-time legacy rows use `time_created/id`; rows from either source
   can precede the other when their timestamps differ. If an implementation
   instead emits stream pages independently, label that choice and do not
   present it as global chronology.

Because pages can overlap while the source is live, de-duplicate IDs across
page boundaries in the same maps. Stop after a bounded page budget; do not
promise a complete unbounded history from a live database.

## Conversation reconstruction and filtering

Only `user` and `assistant` rows/legacy messages become conversation body.
`system`, `synthetic`, `compaction`, unknown message roles/types, and legacy
`compaction`/`step-finish` parts are metadata/noise and must not contaminate
body text. Do not drop the session itself: a session containing only filtered
rows still exists and contributes title/fork/recency metadata.

A v2 assistant can have text, reasoning, and tool items in one `content[]`.
Unknown future item types, missing `state`, non-string text, malformed JSON,
and unknown tool statuses are tolerated and skipped or summarized. A legacy
assistant's body is assembled from its retained parts; text parts contribute
text, reasoning parts contribute only a marker, tool parts contribute a
bounded tool summary/output, and patch/file parts contribute only bounded
metadata when useful. Empty messages after filtering are omitted from body
content, not from session listing.

A non-null v2 `parent_id` marks a subagent session. `fork_session_id` and
`fork_boundary` are v2 fork lineage; report them as bounded metadata and never
mistake fork/system/compaction markers for user or assistant prose.

## What to retain

For a bounded evidence slice, retain session title/IDs, recency, agent/model,
parent/fork lineage, user text, assistant text, reasoning markers, and concise
bounded tool summaries. Summarize code-change counters and errors rather than
copying large JSON. The OpenCode database remains read-only; Syke writes
learned memory only through its normal LLM-first synthesis path.
