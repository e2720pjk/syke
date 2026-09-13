"""
Workspace path constants for the Pi agent runtime.

~/.syke/ is the agent's home and the canonical data store.
Everything lives here: syke.db, MEMEX, PSYCHE, adapters, sessions.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from syke.observe.chatgpt_web import ChatGPTWebRun

logger = logging.getLogger(__name__)

_WORKSPACE_ROOT_OVERRIDE = os.environ.get("SYKE_WORKSPACE_ROOT", "~/.syke")
WORKSPACE_ROOT = Path(os.path.expanduser(_WORKSPACE_ROOT_OVERRIDE))

# Session storage for Pi JSONL audit trail
SESSIONS_DIR = WORKSPACE_ROOT / "sessions"

# Canonical learned-memory database
SYKE_DB = WORKSPACE_ROOT / "syke.db"

# Memex projected from canonical memory
MEMEX_PATH = WORKSPACE_ROOT / "MEMEX.md"
_CHATGPT_WEB_SOURCE_DIR = Path("sources") / "chatgpt-web"


def _revoke_deselected_chatgpt_web_artifacts(
    root: Path,
    selected_sources: tuple[str, ...] | None,
) -> None:
    """Remove a deselected source's Pi-readable projection before boot."""
    if selected_sources is not None and "chatgpt-web" in selected_sources:
        return
    sources_dir = root / "sources"
    if sources_dir.is_symlink():
        logger.warning("Refusing to revoke ChatGPT Web artifacts through a symlinked sources dir")
        return
    source_dir = root / _CHATGPT_WEB_SOURCE_DIR
    if source_dir.is_symlink() or (source_dir.exists() and not source_dir.is_dir()):
        source_dir.unlink()
    elif source_dir.is_dir():
        shutil.rmtree(source_dir)


def initialize_workspace(
    *,
    workspace_root: Path | None = None,
    home: Path | None = None,
    selected_sources: tuple[str, ...] | None = None,
) -> ChatGPTWebRun | None:
    """Create the workspace structure and source-reader prompt surface.

    Called at setup/daemon startup and before direct Pi cycles. Creates dirs,
    installs adapter markdowns from seeds, and writes PSYCHE.md. Idempotent.
    ``workspace_root`` and ``home`` are injectable for replay/custom runtimes;
    omitted values preserve the process-wide workspace behavior.

    MEMEX.md is NOT written here — synthesis owns MEMEX creation.
    syke.db is NOT created here — SykeDB constructor handles that.

    Returns the ChatGPT Web refresh diagnostics when that explicit source is
    selected; otherwise returns None.
    """
    import logging

    logger = logging.getLogger(__name__)
    root = workspace_root or WORKSPACE_ROOT
    sessions_dir = SESSIONS_DIR if workspace_root is None else root / "sessions"

    root.mkdir(parents=True, exist_ok=True)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    _revoke_deselected_chatgpt_web_artifacts(root, selected_sources)

    from syke.observe.bootstrap import ensure_adapters

    ensure_adapters(root, selected_sources=selected_sources)

    source_run: ChatGPTWebRun | None = None
    if selected_sources is not None and "chatgpt-web" in selected_sources:
        from syke.config import chatgpt_web_excluded_project_ids, chatgpt_web_source_root
        from syke.observe.chatgpt_web import ChatGPTWebSourceError, prepare_chatgpt_web_source

        archive_root = chatgpt_web_source_root()
        if archive_root is None:
            raise ChatGPTWebSourceError(
                "archive_root_not_configured",
                "configure paths.sources.chatgpt_web.root before selecting chatgpt-web",
            )
        run = prepare_chatgpt_web_source(
            root,
            root=archive_root,
            excluded_project_ids=chatgpt_web_excluded_project_ids(),
        )
        source_run = run
        if not run.accepted:
            raise ChatGPTWebSourceError(
                "projection_rejected",
                "ChatGPT Web refresh failed; the previous accepted projection was retained",
            )

    from syke.runtime.psyche_md import write_psyche_md

    write_psyche_md(root, home=home, selected_sources=selected_sources)

    logger.debug("Workspace initialized at %s", root)
    return source_run
