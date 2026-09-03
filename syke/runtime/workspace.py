"""
Workspace path constants for the Pi agent runtime.

~/.syke/ is the agent's home and the canonical data store.
Everything lives here: syke.db, MEMEX, PSYCHE, adapters, sessions.
"""

from __future__ import annotations

import os
from pathlib import Path

_WORKSPACE_ROOT_OVERRIDE = os.environ.get("SYKE_WORKSPACE_ROOT", "~/.syke")
WORKSPACE_ROOT = Path(os.path.expanduser(_WORKSPACE_ROOT_OVERRIDE))

# Session storage for Pi JSONL audit trail
SESSIONS_DIR = WORKSPACE_ROOT / "sessions"

# Canonical learned-memory database
SYKE_DB = WORKSPACE_ROOT / "syke.db"

# Memex projected from canonical memory
MEMEX_PATH = WORKSPACE_ROOT / "MEMEX.md"


def initialize_workspace(
    *,
    workspace_root: Path | None = None,
    home: Path | None = None,
    selected_sources: tuple[str, ...] | None = None,
) -> None:
    """Create the workspace structure and source-reader prompt surface.

    Called at setup/daemon startup and before direct Pi cycles. Creates dirs,
    installs adapter markdowns from seeds, and writes PSYCHE.md. Idempotent.
    ``workspace_root`` and ``home`` are injectable for replay/custom runtimes;
    omitted values preserve the process-wide workspace behavior.

    MEMEX.md is NOT written here — synthesis owns MEMEX creation.
    syke.db is NOT created here — SykeDB constructor handles that.
    """
    import logging

    logger = logging.getLogger(__name__)
    root = workspace_root or WORKSPACE_ROOT
    sessions_dir = SESSIONS_DIR if workspace_root is None else root / "sessions"

    root.mkdir(parents=True, exist_ok=True)
    sessions_dir.mkdir(parents=True, exist_ok=True)

    from syke.observe.bootstrap import ensure_adapters

    ensure_adapters(root, selected_sources=selected_sources)

    from syke.runtime.psyche_md import write_psyche_md

    write_psyche_md(root, home=home, selected_sources=selected_sources)

    logger.debug("Workspace initialized at %s", root)
