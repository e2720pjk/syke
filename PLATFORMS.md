# Syke Platform Support

## Ingestion (data into Syke)

This table describes native history Syke can ingest, not which agents can install or use it.

| Platform | Local Artifact Contract | Status |
|----------|-------------------------|--------|
| Claude Code | `~/.claude/projects/**/*.jsonl`, `~/.claude/transcripts/*.jsonl` | Active |
| Codex | rollout JSONL under `~/.codex/sessions` / `archived_sessions`, plus `session_index.jsonl` and SQLite metadata | Active |
| Pi coding agent | default JSONL session trees under `~/.pi/agent/sessions/**/*.jsonl`; custom session directories are not auto-discovered | Active |
| OpenCode | SQLite DB under `~/.local/share/opencode/*.db`, including channel-named DBs | Active |
| Cursor | official user-data roots under Cursor `workspaceStorage` / `globalStorage` | Active |
| GitHub Copilot | Copilot CLI `~/.copilot/session-state/**/events.jsonl` plus VS Code `chatSessions` files | Active |
| Google Antigravity | shared-harness transcripts and workflow artifacts under `~/.gemini/antigravity/brain`, `~/.gemini/antigravity-cli/brain`, and `~/.gemini/antigravity-ide/brain`; browser recording metadata for Antigravity 2.0 | Active |
| Hermes | `~/.hermes/state.db` plus session JSON under `~/.hermes/sessions` | Active |
| ChatGPT Web / ChatGPTExporter | supplied archives under `~/.syke-chatgpt-web/`: conversation indexes, Markdown, normalized JSON and raw graphs | Active (local archive only) |
| GitHub | historical/docs reference | Experimental |

Requested but not active yet:

| Platform | Status |
|----------|--------|
| Amp | Needs verified local artifact contract |
| Windsurf | Needs verified local artifact contract |
| Cline / Roo-Code | Needs verified local artifact contract |
| Goose | Needs verified local artifact contract |

## Distribution (Syke into agents)

Syke currently supports only three distribution surfaces:

| Surface | Path | Status |
|---------|------|--------|
| CLI | `syke ask`, `syke memex`, `syke record`, `syke doctor`, `syke setup`, `syke source add/list/remove` | Active |
| MEMEX artifact | exported memex at `~/.syke/workspace/MEMEX.md` | Active |
| Capability registration | canonical Syke capability package installed to detected skill/capability surfaces, plus native wrappers where needed | Active |
