# ChatGPT Web (ChatGPTExporter)

Source identity: `chatgpt-web`.

Syke reads only the generated projection at:

```text
sources/chatgpt-web/projection.jsonl
```

The projection is created after the user explicitly selects `chatgpt-web`.
Each JSONL row is one conversation and contains its identity, membership
scopes, timestamps, normalized version/hash, and the current graph branch.
Rows are bounded and ordered with newer conversations first; messages remain
in normal oldest-to-newest order within each conversation. Text and useful
code are retained with tail truncation when a limit is reached.

Assets and citations are represented by bounded omission markers. System/tool
messages, raw node fields, extension/provider payloads, account data, project
instructions, binary assets, raw paths, and the configured exporter root are
not part of the projection. Do not read the exporter archive directly and do
not infer a project exclusion from a project name.

If `sources/chatgpt-web/run.json` exists, it contains only bounded operational
counts and failure reasons. It is not conversation evidence.
