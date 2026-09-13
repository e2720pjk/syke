# Syke Platform Support

## Ingestion (data into Syke)

| Platform | Local Artifact Contract | Status |
|----------|-------------------------|--------|
| Claude Code | `~/.claude/projects/**/*.jsonl`, `~/.claude/transcripts/*.jsonl` | Active |
| Codex | rollout JSONL under `~/.codex/sessions` / `archived_sessions`, plus `session_index.jsonl` and SQLite metadata | Active |
| Pi | recursive session JSONL under `~/.pi/agent/sessions` (external Pi history only) | Active |
| OpenCode | LLM-first read-only WAL-aware SQLite DB under `~/.local/share/opencode/opencode*.db`; schema-detected legacy `session/message/part` plus v2 `session_v2/session_message`, with WAL/SHM excluded and bounded keyed message merge | Active (legacy + OpenCode 2.0 v2) |
| Cursor | official user-data roots under Cursor `workspaceStorage` / `globalStorage` | Active |
| GitHub Copilot | Copilot CLI `~/.copilot/session-state/**/events.jsonl` plus VS Code `chatSessions` files | Active |
| Antigravity | workflow artifacts under `~/.gemini/antigravity/brain` and browser recording metadata | Active |
| Hermes | `~/.hermes/state.db` plus session JSON under `~/.hermes/sessions` | Active |
| Gemini CLI | `~/.gemini/tmp/<project_hash>/chats/**/*.json` and checkpoint JSON | Active |
| ChatGPT Web | Explicitly configured ChatGPTExporter snapshot (`archive.json`, conversation index, normalized bodies) | Active, explicit opt-in |
| GitHub | historical/docs reference | Experimental |

## Distribution (Syke into agents)

Syke currently supports only three distribution surfaces:

| Surface | Path | Status |
|---------|------|--------|
| CLI | `syke ask`, `syke memex`, `syke record`, `syke doctor`, `syke setup` | Active |
| MEMEX artifact | exported memex at `~/.syke/MEMEX.md` | Active |
| Capability registration | canonical Syke capability package installed to detected skill/capability surfaces, plus native wrappers where needed | Active |

## Adding a Platform

Agents should update this table when they:
- Add or validate a real adapter/runtime path
- Fit a new agent into one of the three supported distribution surfaces
- Promote an experimental ingestion path to active

For current active harnesses, setup is seed-first — there is no runtime factory anymore:

- Syke ships seed adapters in-repo under `syke/observe/seeds/` for the active catalog
- `initialize_workspace()` installs shipped seeds locally on first run and upgrades only known untouched seed revisions
- new harnesses arrive by adding a seed adapter markdown plus a `SourceSpec` to the catalog — no generated Python adapters, no dynamic loader
- OpenCode's adapter seed revision is legacy+v2 aware (schema detection, v2-authoritative session metadata, stable message-ID merge, bounded legacy-part reconstruction); untouched older seeds upgrade automatically, while customized adapters are preserved and reported with manual repair guidance
- ChatGPT Web is projection-first: Syke validates the configured snapshot, filters denied project memberships before body reads, and exposes only a bounded current-branch projection; the exporter root is never a Pi sandbox read path

Updated by agents as they self-heal and add new platforms.
