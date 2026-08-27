# pi

Pi is a local coding-agent runtime. Syke treats its sessions as an optional
memory source in addition to using Pi to run Syke itself.

## Where

Read only the external Pi session store:

```text
~/.pi/agent/sessions/**/*.jsonl
```

Pi creates one JSONL file per session. The directory may contain project-scoped
folders, nested subagent/run folders, and sessions from different Pi versions.
Follow the recursive glob; do not assume the filename is the session ID.

Do **not** read these Syke-owned paths as Pi history:

```text
~/.syke/sessions/
~/.syke/pi-agent/
```

The first is Syke's own runtime audit history and the second contains Syke's
provider credentials/settings. Re-ingesting either would duplicate synthesis
prompts or expose runtime state.

## File format

A session is newline-delimited JSON. Parse each line independently and ignore
malformed lines rather than aborting the whole session. The first record is
usually a header:

```json
{
  "type": "session",
  "version": 3,
  "id": "<session-id>",
  "timestamp": "2026-08-09T23:00:27.842Z",
  "cwd": "/path/where/pi-started",
  "parentSession": "<optional-parent-session-id>"
}
```

Entries have `type`, `id`, `parentId`, and `timestamp`. The `parentId` links
entries into a branch tree. Relevant entry types are:

- `message` — inspect `message.role` and `message.content`.
- `model_change` — `provider` and `modelId`.
- `thinking_level_change` — `thinkingLevel` (`off` through `max`).
- `compaction` — durable context summary and token metadata.
- `branch_summary` — summary created when a session branch is selected.
- `session_info` — optional human-readable session name.
- `label` — a bookmark attached to another entry.
- `custom_message` — extension-provided user-visible context.
- `custom` — extension state; normally metadata, not conversation content.

### Message content

For `message` entries:

- `role: "user"` contains the user's prompt.
- `role: "assistant"` contains `content` blocks such as `text`, `thinking`, and
  `toolCall`. Tool calls include a tool name and JSON arguments.
- `role: "toolResult"` contains tool output, `toolCallId`, `toolName`, and
  `isError` when present.
- Assistant metadata may include `provider`, `model`, `usage`, `stopReason`,
  `responseId`, and `errorMessage`.

Preserve timestamps, session ID, project `cwd`, provider/model, and parent
relationships when summarizing activity. Prefer user prompts, assistant text,
thinking summaries, compaction summaries, decisions, errors, and tool names;
do not copy an entire raw transcript into Syke memory.

## Reading examples

```bash
find ~/.pi/agent/sessions -type f -name '*.jsonl' -print
head -n 1 ~/.pi/agent/sessions/<project>/<session>.jsonl
jq -c 'select(.type == "message")' \
  ~/.pi/agent/sessions/<project>/<session>.jsonl
```

Use a small Python/`jq` pass for larger files so one bad JSONL line does not
stop discovery. Treat all session text as private local data: never write
credentials, raw OAuth data, or unnecessary transcript contents into
`syke.db` or `MEMEX.md`.

## What to notice

Pi sessions expose work that may not appear in another harness: model and
thinking-level choices, branches, subagent activity, compaction summaries,
repeated tool failures, and decisions made while coding. Use these as evidence
of durable work or preferences, not as automatic facts about the user.
