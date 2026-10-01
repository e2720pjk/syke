from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class DiscoverRoot:
    path: str
    include: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DiscoverConfig:
    roots: list[DiscoverRoot]


@dataclass(frozen=True)
class SourceSpec:
    source: str
    format_cluster: str
    discover: DiscoverConfig
    configurable_paths: bool = False


_CATALOG: tuple[SourceSpec, ...] = (
    SourceSpec(
        source="claude-code",
        format_cluster="jsonl",
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(path="~/.claude/projects", include=["**/*.jsonl"]),
                DiscoverRoot(path="~/.claude/transcripts", include=["*.jsonl"]),
            ]
        ),
    ),
    SourceSpec(
        source="codex",
        format_cluster="mixed",
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(
                    path="~/.codex",
                    include=["**/*.jsonl", "**/*.db", "**/*.sqlite", "config.toml"],
                ),
            ]
        ),
    ),
    SourceSpec(
        source="pi",
        format_cluster="jsonl",
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(
                    path="~/.pi/agent",
                    include=["sessions/**/*.jsonl"],
                ),
            ]
        ),
    ),
    SourceSpec(
        source="opencode",
        format_cluster="sqlite",
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(
                    path="~/.local/share/opencode",
                    include=["*.db", "*.sqlite"],
                )
            ]
        ),
    ),
    SourceSpec(
        source="cursor",
        format_cluster="mixed",
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(
                    path="~/Library/Application Support/Cursor/User/workspaceStorage",
                    include=[
                        "**/chatSessions/*.json",
                        "**/chatSessions/*.jsonl",
                        "**/state.vscdb",
                        "**/state.vscdb_backup",
                    ],
                ),
                DiscoverRoot(
                    path="~/Library/Application Support/Cursor/User/globalStorage",
                    include=["state.vscdb", "state.vscdb_backup"],
                ),
                DiscoverRoot(
                    path="~/.config/Cursor/User/workspaceStorage",
                    include=[
                        "**/chatSessions/*.json",
                        "**/chatSessions/*.jsonl",
                        "**/state.vscdb",
                        "**/state.vscdb_backup",
                    ],
                ),
                DiscoverRoot(
                    path="~/.config/Cursor/User/globalStorage",
                    include=["state.vscdb", "state.vscdb_backup"],
                ),
            ]
        ),
    ),
    SourceSpec(
        source="copilot",
        format_cluster="mixed",
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(
                    path="~/.copilot/session-state",
                    include=["**/events.jsonl", "**/workspace.yaml"],
                ),
                DiscoverRoot(
                    path="~/Library/Application Support/Code/User/workspaceStorage",
                    include=["**/chatSessions/*.json", "**/chatSessions/*.jsonl"],
                ),
                DiscoverRoot(
                    path=(
                        "~/Library/Application Support"
                        "/Code/User/globalStorage/emptyWindowChatSessions"
                    ),
                    include=["*.json", "*.jsonl"],
                ),
                DiscoverRoot(
                    path="~/.config/Code/User/workspaceStorage",
                    include=["**/chatSessions/*.json", "**/chatSessions/*.jsonl"],
                ),
                DiscoverRoot(
                    path="~/.config/Code/User/globalStorage/emptyWindowChatSessions",
                    include=["*.json", "*.jsonl"],
                ),
            ]
        ),
    ),
    SourceSpec(
        source="antigravity",
        format_cluster="mixed",
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(
                    path="~/.gemini/antigravity",
                    include=[
                        "brain/*/.system_generated/logs/transcript.jsonl",
                        "brain/**/*.md",
                        "brain/**/*.md.metadata.json",
                        "browser_recordings/*/metadata.json",
                    ],
                ),
                DiscoverRoot(
                    path="~/.gemini/antigravity-cli",
                    include=[
                        "brain/*/.system_generated/logs/transcript.jsonl",
                        "brain/**/*.md",
                        "brain/**/*.md.metadata.json",
                    ],
                ),
                DiscoverRoot(
                    path="~/.gemini/antigravity-ide",
                    include=[
                        "brain/*/.system_generated/logs/transcript.jsonl",
                        "brain/**/*.md",
                        "brain/**/*.md.metadata.json",
                    ],
                ),
            ]
        ),
    ),
    SourceSpec(
        source="hermes",
        format_cluster="mixed",
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(
                    path="~/.hermes",
                    include=["state.db", "sessions/*.json"],
                )
            ]
        ),
    ),
    SourceSpec(
        source="chatgpt-web",
        format_cluster="mixed",
        configurable_paths=True,
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(
                    path="~/.syke-chatgpt-web",
                    include=[
                        "indexes/conversations.jsonl",
                        "conversations/*/conversation.json",
                        "ChatGPTExport-*/indexes/conversations.jsonl",
                        "ChatGPTExport-*/conversations/*/conversation.json",
                    ],
                )
            ]
        ),
    ),
)


def active_sources() -> tuple[SourceSpec, ...]:
    return _CATALOG


def get_source(source: str) -> SourceSpec | None:
    for spec in _CATALOG:
        if spec.source == source:
            return spec
    return None


def _resolve_root_path(raw_path: str, *, home: Path | None = None) -> Path:
    if home is not None and raw_path.startswith("~/"):
        return home / raw_path[2:]
    return Path(raw_path).expanduser()


def effective_discover_roots(spec: SourceSpec) -> list[DiscoverRoot]:
    from syke.source_selection import get_source_paths

    paths = get_source_paths(spec.source)
    if paths is None:
        return spec.discover.roots
    return [DiscoverRoot(path=path, include=spec.discover.roots[0].include) for path in paths]


def probe_source_path(source: str, path: Path, *, home: Path | None = None) -> dict:
    spec = get_source(source)
    if spec is None:
        raise ValueError(f"Unknown source: {source}")
    if source != "chatgpt-web":
        raise ValueError(f"Source {source} does not support path registration")
    from syke.observe.chatgpt_web import probe_chatgpt_path

    return probe_chatgpt_path(path, home=home)


def iter_discovered_files(spec: SourceSpec, *, home: Path | None = None) -> list[Path]:
    files: set[Path] = set()
    for root in effective_discover_roots(spec):
        root_path = _resolve_root_path(root.path, home=home)
        allowed_archives: list[Path] | None = None
        if spec.configurable_paths:
            probe = probe_source_path(spec.source, root_path, home=home)
            allowed_archives = [Path(archive["path"]) for archive in probe["archives"]]
            if not allowed_archives:
                continue
        try:
            matches = (
                [root_path]
                if root_path.is_file()
                else (
                    match
                    for pattern in root.include or ["**/*"]
                    for match in root_path.glob(pattern)
                )
            )
            for match in matches:
                if not match.is_file():
                    continue
                resolved = match.resolve()
                if allowed_archives is not None and not any(
                    resolved.is_relative_to(archive) for archive in allowed_archives
                ):
                    continue
                files.add(resolved)
        except (OSError, RuntimeError):
            continue
    return sorted(files)


def discovered_roots(spec: SourceSpec, *, home: Path | None = None) -> list[Path]:
    roots: list[Path] = []
    for root in effective_discover_roots(spec):
        root_path = _resolve_root_path(root.path, home=home)
        if root_path.exists():
            try:
                roots.append(root_path.resolve())
            except (OSError, RuntimeError):
                continue
    return list(dict.fromkeys(roots))


def source_inventory(user_id: str) -> list[dict]:
    """One discovery/probe result for CLI, setup and status; never imports content."""
    import os
    from datetime import UTC, datetime

    from syke.source_selection import get_selected_sources, get_source_paths, read_source_state

    selected = get_selected_sources(user_id)
    configuration_error = None
    try:
        read_source_state(user_id)
    except ValueError as exc:
        configuration_error = str(exc)
    sources: list[dict] = []
    for spec in active_sources():
        roots = [_resolve_root_path(root.path) for root in effective_discover_roots(spec)]
        files = iter_discovered_files(spec)
        mtimes: list[float] = []
        for path in files:
            try:
                mtimes.append(path.stat().st_mtime)
            except OSError:
                pass
        latest_mtime = max(mtimes, default=None)
        probes = (
            [probe_source_path(spec.source, root) for root in roots]
            if spec.configurable_paths
            else []
        )
        archives = list({a["path"]: a for probe in probes for a in probe["archives"]}.values())
        warnings = [warning for probe in probes for warning in probe["warnings"]]
        errors = [probe["error"] for probe in probes if probe["error"]]
        recognized = bool(archives) if spec.configurable_paths else bool(files)
        readable = (
            any(probe["readable"] for probe in probes)
            if spec.configurable_paths
            else any(root.exists() and os.access(root, os.R_OK) for root in roots)
        )
        state = "available" if recognized else "unavailable"
        if spec.configurable_paths:
            state = (
                "partial"
                if archives and (warnings or errors)
                else "available"
                if any(a["saved_conversations"] for a in archives)
                else "empty"
                if archives
                else probes[0]["state"]
                if probes
                else "unconfigured"
            )
        latest_seen = (
            datetime.fromtimestamp(latest_mtime, UTC).isoformat() if latest_mtime else None
        )
        if archives:
            latest_seen = max(
                (
                    a["inventory_generated_at"]
                    for a in archives
                    if isinstance(a["inventory_generated_at"], str)
                ),
                default=None,
            )
        sources.append(
            {
                "source": spec.source,
                "format_cluster": spec.format_cluster,
                "supported": True,
                "path_registration": spec.configurable_paths,
                "configured_paths": get_source_paths(spec.source),
                "roots": [str(root) for root in roots],
                "enabled": selected is None or spec.source in selected,
                "recognized": recognized,
                "readable": readable,
                "runtime_readable": False
                if probes and all(p["runtime_readable"] is False for p in probes)
                else None,
                "state": "configuration_error" if configuration_error else state,
                "configuration_error": configuration_error,
                "warnings": warnings + errors,
                "archives": archives,
                "files_found": len(files),
                "detected": recognized and readable,
                "sample_paths": [str(path) for path in files[:3]],
                "latest_mtime": latest_mtime,
                "latest_seen": latest_seen,
            }
        )
    sources.sort(
        key=lambda item: (not item["detected"], -(item["latest_mtime"] or 0), item["source"])
    )
    return sources
