"""Persist source selection and explicit local evidence paths."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from syke import config
from syke.observe.catalog import active_sources, get_source

SOURCE_SELECTION_FILE = "source_selection.json"
logger = logging.getLogger(__name__)
_WRITER_LOCK = threading.RLock()


def _selection_path(user_id: str) -> Path:
    _ = user_id
    return config.SYKE_HOME / SOURCE_SELECTION_FILE


def _normalize_sources(sources: list[str] | tuple[str, ...]) -> list[str]:
    normalized: list[str] = []
    for source in sources:
        source_id = str(source).strip()
        if not source_id or source_id in normalized:
            continue
        if get_source(source_id) is None:
            raise ValueError(f"Unknown source: {source_id}")
        normalized.append(source_id)
    return normalized


def read_source_state(user_id: str) -> dict:
    """Read v1/v2 without writes. Invalid state must never restore default roots."""
    path = _selection_path(user_id)
    try:
        if not path.exists():
            return {"selected_sources": None, "source_paths": {}}
        if not path.is_file():
            raise ValueError("configuration is not a regular file")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or type(payload.get("schema_version")) is not int:
            raise ValueError("unsupported schema")
        version = payload["schema_version"]
        if version not in (1, 2):
            raise ValueError("unsupported schema")
        selected = payload.get("selected_sources")
        if selected is None and version == 2 and "selected_sources" in payload:
            pass  # Preserve the unrestricted selection mode when only paths are added.
        elif isinstance(selected, list):
            selected = _normalize_sources(selected)
        else:
            raise ValueError("selected_sources is not a list")
        paths = payload.get("source_paths", {}) if version == 1 else payload.get("source_paths")
        if not isinstance(paths, dict):
            raise ValueError("source_paths is not an object")
        for source, roots in paths.items():
            spec = get_source(source)
            if spec is None or not spec.configurable_paths:
                raise ValueError(f"Source does not support explicit paths: {source}")
            if not isinstance(roots, list) or any(
                not isinstance(root, str)
                or not root
                or "\0" in root
                or not Path(root).expanduser().is_absolute()
                for root in roots
            ):
                raise ValueError(f"Invalid source paths: {source}")
        return {"selected_sources": selected, "source_paths": paths}
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        raise ValueError(f"Invalid source configuration at {path}: {exc}") from exc


def get_selected_sources(user_id: str) -> tuple[str, ...] | None:
    try:
        selected = read_source_state(user_id)["selected_sources"]
        return tuple(selected) if selected is not None else None
    except ValueError as exc:
        logger.warning("%s; failing closed", exc)
        return ()


def get_source_paths(source: str) -> tuple[str, ...] | None:
    """None uses catalog defaults; an explicit empty tuple uses no roots."""
    try:
        paths = read_source_state(config.DEFAULT_USER)["source_paths"].get(source)
        return tuple(paths) if paths is not None else None
    except ValueError as exc:
        logger.warning("%s; failing closed", exc)
        return ()


@contextmanager
def _locked_state(user_id: str):
    path = _selection_path(user_id)
    with _WRITER_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.with_suffix(".lock").open("a+b") as lock:
            if os.name == "nt":
                import msvcrt

                if lock.seek(0, os.SEEK_END) == 0:
                    lock.write(b"\0")
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield read_source_state(user_id)
            finally:
                if os.name == "nt":
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _write_state(user_id: str, state: dict) -> None:
    paths = state["source_paths"]
    payload = {
        "schema_version": 2 if paths or state["selected_sources"] is None else 1,
        "selected_sources": state["selected_sources"],
        "updated_at": datetime.now(UTC).isoformat(),
    }
    if payload["schema_version"] == 2:
        payload["source_paths"] = paths
    path = _selection_path(user_id)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def set_selected_sources(user_id: str, sources: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    selected = _normalize_sources(sources)
    with _locked_state(user_id) as state:
        state["selected_sources"] = selected
        _write_state(user_id, state)
    return tuple(selected)


def _require_configurable_source(source: str) -> None:
    spec = get_source(source)
    if spec is None:
        raise ValueError(f"Unknown source: {source}")
    if not spec.configurable_paths:
        raise ValueError(
            f"Source {source} uses native discovery and does not support path registration"
        )


def _registered_root(roots: list[str], path: Path) -> str | None:
    for root in roots:
        if root == str(path):
            return root
        try:
            if Path(root).samefile(path):
                return root
        except OSError:
            continue
    return None


def register_source_path(user_id: str, source: str, path: Path) -> dict:
    from syke.observe.bootstrap import ensure_adapters
    from syke.observe.catalog import probe_source_path

    _require_configurable_source(source)
    probe = probe_source_path(source, path)
    if not probe["recognized"] or not probe["readable"]:
        raise ValueError(probe.get("error") or f"No supported archive at {path}")
    root = probe["path"]
    with _locked_state(user_id) as state:
        # Install guidance before committing activation; never touch graph/MEMEX.
        workspace = config.user_workspace_dir(user_id)
        adapter = ensure_adapters(workspace, selected_sources=(source,))[0]
        if (
            adapter.status == "skipped"
            or not Path(adapter.detail).is_file()
            or not os.access(adapter.detail, os.R_OK)
        ):
            raise ValueError(f"Source guide is unavailable: {adapter.detail}")
        roots = state["source_paths"].setdefault(source, [])
        changed = _registered_root(roots, Path(root)) is None
        if changed:
            roots.append(root)
        selected = state["selected_sources"]
        if selected is not None and source not in selected:
            selected.append(source)
            changed = True
        if changed:
            _write_state(user_id, state)
    return {
        **probe,
        "source": source,
        "enabled": True,
        "changed": changed,
        "adapter": adapter.detail,
    }


def remove_source_path(user_id: str, source: str, path: Path) -> dict:
    _require_configurable_source(source)
    root = str(path.expanduser().resolve())
    with _locked_state(user_id) as state:
        roots = state["source_paths"].get(source, [])
        registered = _registered_root(roots, Path(root))
        if registered is None:
            raise ValueError(f"Path is not registered for {source}: {root}")
        roots.remove(registered)
        selected = state["selected_sources"]
        if not roots:
            if selected is None:
                selected = [spec.source for spec in active_sources()]
            state["selected_sources"] = [item for item in selected if item != source]
        _write_state(user_id, state)
        enabled = state["selected_sources"] is None or source in state["selected_sources"]
    return {"source": source, "removed": root, "paths": roots, "enabled": enabled}
