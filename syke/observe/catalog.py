from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class DiscoverRoot:
    path: str
    include: list[str] = field(default_factory=list)
    priority: int = 0


@dataclass(frozen=True)
class DiscoverConfig:
    roots: list[DiscoverRoot]


@dataclass(frozen=True)
class SourceSpec:
    source: str
    format_cluster: str
    discover: DiscoverConfig
    artifact_hints: tuple[str, ...] = ()
    status: str = "active"
    explicit_only: bool = False


_CATALOG: tuple[SourceSpec, ...] = (
    SourceSpec(
        source="claude-code",
        format_cluster="jsonl",
        artifact_hints=("jsonl", "transcript"),
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(path="~/.claude/projects", include=["**/*.jsonl"], priority=20),
                DiscoverRoot(path="~/.claude/transcripts", include=["*.jsonl"], priority=10),
            ]
        ),
    ),
    SourceSpec(
        source="codex",
        format_cluster="mixed",
        artifact_hints=("sqlite", "jsonl", "history", "index", "archive"),
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(
                    path="~/.codex",
                    include=["**/*.jsonl", "**/*.db", "**/*.sqlite", "config.toml"],
                    priority=20,
                ),
            ]
        ),
    ),
    SourceSpec(
        source="pi",
        format_cluster="jsonl",
        artifact_hints=("jsonl", "session", "transcript", "thinking", "tool-call"),
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(
                    path="~/.pi/agent/sessions",
                    include=["**/*.jsonl"],
                    priority=20,
                ),
            ]
        ),
    ),
    SourceSpec(
        source="opencode",
        format_cluster="sqlite",
        artifact_hints=("sqlite",),
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(
                    # opencode*.db keeps the include pattern aligned with the
                    # adapter markdown regex ^opencode.*\.db$ and
                    # inherently excludes WAL/SHM sidecars (*.db-wal/*.db-shm).
                    path="~/.local/share/opencode",
                    include=["opencode*.db"],
                    priority=20,
                )
            ]
        ),
    ),
    SourceSpec(
        source="cursor",
        format_cluster="mixed",
        artifact_hints=("json", "jsonl", "sqlite", "chatSessions", "composerData"),
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
                    priority=20,
                ),
                DiscoverRoot(
                    path="~/Library/Application Support/Cursor/User/globalStorage",
                    include=["state.vscdb", "state.vscdb_backup"],
                    priority=15,
                ),
                DiscoverRoot(
                    path="~/.config/Cursor/User/workspaceStorage",
                    include=[
                        "**/chatSessions/*.json",
                        "**/chatSessions/*.jsonl",
                        "**/state.vscdb",
                        "**/state.vscdb_backup",
                    ],
                    priority=10,
                ),
                DiscoverRoot(
                    path="~/.config/Cursor/User/globalStorage",
                    include=["state.vscdb", "state.vscdb_backup"],
                    priority=9,
                ),
            ]
        ),
    ),
    SourceSpec(
        source="copilot",
        format_cluster="mixed",
        artifact_hints=("json", "jsonl", "sqlite", "events", "chatSessions"),
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(
                    path="~/.copilot/session-state",
                    include=["**/events.jsonl", "**/workspace.yaml"],
                    priority=20,
                ),
                DiscoverRoot(
                    path="~/Library/Application Support/Code/User/workspaceStorage",
                    include=["**/chatSessions/*.json", "**/chatSessions/*.jsonl"],
                    priority=12,
                ),
                DiscoverRoot(
                    path=(
                        "~/Library/Application Support"
                        "/Code/User/globalStorage/emptyWindowChatSessions"
                    ),
                    include=["*.json", "*.jsonl"],
                    priority=11,
                ),
                DiscoverRoot(
                    path="~/.config/Code/User/workspaceStorage",
                    include=["**/chatSessions/*.json", "**/chatSessions/*.jsonl"],
                    priority=10,
                ),
                DiscoverRoot(
                    path="~/.config/Code/User/globalStorage/emptyWindowChatSessions",
                    include=["*.json", "*.jsonl"],
                    priority=9,
                ),
            ]
        ),
    ),
    SourceSpec(
        source="antigravity",
        format_cluster="mixed",
        artifact_hints=("workflow", "markdown", "metadata", "browser-recording"),
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(
                    path="~/.gemini/antigravity",
                    include=[
                        "brain/**/*.md",
                        "brain/**/*.md.metadata.json",
                        "browser_recordings/*/metadata.json",
                    ],
                    priority=20,
                ),
            ]
        ),
    ),
    SourceSpec(
        source="hermes",
        format_cluster="mixed",
        artifact_hints=("sqlite", "json"),
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(
                    path="~/.hermes",
                    include=["state.db", "sessions/*.json"],
                    priority=20,
                )
            ]
        ),
    ),
    SourceSpec(
        source="gemini-cli",
        format_cluster="mixed",
        artifact_hints=("json", "chat", "checkpoint"),
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(
                    path="~/.gemini/tmp",
                    include=["**/chats/**/*.json", "**/checkpoints/**/*.json"],
                    priority=20,
                )
            ]
        ),
    ),
)


def _chatgpt_web_spec() -> SourceSpec | None:
    """Build the ChatGPT Web source only from an explicit configured root."""
    try:
        from syke.config import chatgpt_web_source_root

        root = chatgpt_web_source_root()
    except Exception:
        root = None
    if root is None:
        return None
    return SourceSpec(
        source="chatgpt-web",
        format_cluster="json",
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(
                    path=str(root),
                    include=["archive.json", "indexes/conversations.jsonl"],
                    priority=30,
                )
            ]
        ),
        artifact_hints=("archive.json", "conversations.jsonl", "normalized", "projectId"),
        explicit_only=True,
    )


def active_sources() -> tuple[SourceSpec, ...]:
    chatgpt_web = _chatgpt_web_spec()
    if chatgpt_web is None:
        return _CATALOG
    return (chatgpt_web, *_CATALOG)


def get_source(source: str) -> SourceSpec | None:
    return next((spec for spec in active_sources() if spec.source == source), None)


def is_source_selected(
    spec: SourceSpec,
    selected_sources: tuple[str, ...] | list[str] | None,
) -> bool:
    """Apply persisted source selection, including explicit-only sources."""
    if getattr(spec, "explicit_only", False):
        return selected_sources is not None and spec.source in selected_sources
    return selected_sources is None or spec.source in (selected_sources or ())


def _resolve_root_path(raw_path: str, *, home: Path | None = None) -> Path:
    if home is not None and raw_path.startswith("~/"):
        return home / raw_path[2:]
    return Path(raw_path).expanduser()


def _pi_excluded_paths(*, home: Path | None = None) -> tuple[Path, ...]:
    base_home = (home or Path.home()).expanduser().resolve()
    syke_roots = {base_home / ".syke"}
    if home is None:
        workspace_override = os.getenv("SYKE_WORKSPACE_ROOT")
        if workspace_override:
            syke_roots.add(Path(workspace_override).expanduser().resolve())

    excluded = [root / "sessions" for root in syke_roots]
    if home is None:
        agent_override = os.getenv("SYKE_PI_AGENT_DIR")
        excluded.append(
            Path(agent_override).expanduser().resolve()
            if agent_override
            else base_home / ".syke" / "pi-agent"
        )
    else:
        excluded.append(base_home / ".syke" / "pi-agent")
    return tuple(path.resolve() for path in excluded)


def is_excluded_discovered_path(
    spec: SourceSpec,
    path: Path,
    *,
    home: Path | None = None,
) -> bool:
    """Reject Pi paths that resolve into Syke-owned runtime state."""
    if spec.source != "pi":
        return False
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        return True
    return any(
        resolved == excluded or excluded in resolved.parents
        for excluded in _pi_excluded_paths(home=home)
    )


def iter_discovered_files(spec: SourceSpec, *, home: Path | None = None) -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()
    for root in spec.discover.roots:
        root_path = _resolve_root_path(root.path, home=home)
        if root_path.is_file():
            try:
                resolved = root_path.resolve()
            except OSError:
                continue
            if is_excluded_discovered_path(spec, resolved, home=home):
                continue
            if resolved not in seen:
                seen.add(resolved)
                files.append(resolved)
            continue
        if not root_path.exists() or not root_path.is_dir():
            continue
        for pattern in root.include or ["**/*"]:
            for match in root_path.glob(pattern):
                if not match.is_file():
                    continue
                try:
                    resolved = match.resolve()
                except OSError:
                    continue
                if is_excluded_discovered_path(spec, resolved, home=home):
                    continue
                if resolved in seen:
                    continue
                seen.add(resolved)
                files.append(resolved)
    return sorted(files)


def discovered_roots(spec: SourceSpec, *, home: Path | None = None) -> list[Path]:
    roots: list[Path] = []
    for root in spec.discover.roots:
        root_path = _resolve_root_path(root.path, home=home)
        if root_path.exists():
            try:
                resolved = root_path.resolve()
            except OSError:
                continue
            if is_excluded_discovered_path(spec, resolved, home=home):
                continue
            roots.append(resolved)
    return roots
