"""Recognize ChatGPTExporter archives using metadata, without ingesting content."""

from __future__ import annotations

import json
import os
from pathlib import Path


def _metadata(path: Path, fingerprint: str | None = None) -> dict:
    if not path.is_file():
        raise ValueError(f"Archive metadata is not a regular file: {path}")
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or data.get("provider") != "chatgpt-web":
        raise ValueError(f"Not ChatGPTExporter metadata: {path}")
    if type(data.get("schemaVersion")) is not int or data["schemaVersion"] != 1:
        raise ValueError(f"Unsupported archive schema version: {path}")
    saved_fingerprint = data.get("workspaceFingerprint")
    if not isinstance(saved_fingerprint, str) or not saved_fingerprint:
        raise ValueError(f"Missing workspace fingerprint: {path}")
    if fingerprint is not None and saved_fingerprint != fingerprint:
        raise ValueError(f"Workspace fingerprint mismatch: {path}")
    return data


def _pointer(archive: Path, value: object) -> Path:
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError(f"Invalid evidence pointer in {archive}")
    relative = Path(value)
    resolved = (archive / relative).resolve()
    if relative.is_absolute() or ".." in relative.parts or not resolved.is_relative_to(archive):
        raise ValueError(f"Evidence pointer escapes archive: {value}")
    return resolved


def _archive_info(archive: Path) -> dict:
    inventory = _metadata(_pointer(archive, "inventory.json"))
    fingerprint = inventory["workspaceFingerprint"]
    if (
        not isinstance(inventory.get("conversations"), list)
        or not isinstance(inventory.get("projects", []), list)
        or not isinstance(inventory.get("chains"), list)
        or any(
            not isinstance(c, dict) or not isinstance(c.get("scope"), str)
            for c in inventory["chains"]
        )
    ):
        raise ValueError(f"Invalid conversation/project/scope inventory: {archive}")
    warnings: list[str] = []
    manifest_path = _pointer(archive, "archive.json")
    manifest = _metadata(manifest_path, fingerprint) if manifest_path.is_file() else {}
    if not manifest:
        warnings.append("Archive manifest is absent; export/audit may be unfinished.")
    saved = retained = missing = 0
    index = _pointer(archive, "indexes/conversations.jsonl")
    if index.is_file():
        # ponytail: metadata/pointers only, not content hashes; use Exporter's audit for integrity.
        with index.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                if (
                    not isinstance(row, dict)
                    or not isinstance(row.get("conversationId"), str)
                    or row.get("logicalKey") != f"{fingerprint}/{row['conversationId']}"
                    or type(row.get("absentFromCurrentInventory", False)) is not bool
                ):
                    raise ValueError(f"Invalid conversation index row: {index}")
                for key in ("normalizedPath", "rawPath"):
                    evidence = _pointer(archive, row.get(key))
                    if not evidence.is_file() or not os.access(evidence, os.R_OK):
                        missing += 1
                saved += 1
                retained += row.get("absentFromCurrentInventory", False)
    else:
        warnings.append("Conversation index is absent; read saved conversation JSON directly.")
        for evidence in archive.glob("conversations/*/conversation.json"):
            _pointer(archive, str(evidence.relative_to(archive)))
            if evidence.is_file():
                saved += 1
    if missing:
        warnings.append(f"{missing} indexed evidence file(s) are missing or unreadable.")
    inventory_complete = inventory.get("complete") is True and all(
        chain.get("complete") is True for chain in inventory.get("chains", [])
    )
    if not inventory_complete:
        warnings.append("Saved inventory pagination is incomplete.")
    normalizer = manifest.get("normalizerVersion")
    if manifest and normalizer != "chatgpt-web-v1":
        if normalizer != "unknown" or saved or inventory["conversations"]:
            raise ValueError(f"Unsupported normalizer version {normalizer!r}: {archive}")
    report_path = _pointer(archive, "reports/validation.json")
    report = _metadata(report_path, fingerprint) if report_path.is_file() else {}
    terminal_state = report.get("terminalState")
    if terminal_state != "complete":
        warnings.append(f"Archive audit is {terminal_state or 'unavailable'}.")
    return {
        "path": str(archive),
        "workspace_fingerprint": fingerprint,
        "inventory_generated_at": inventory.get("generatedAt"),
        "inventory_complete": inventory_complete,
        "inventory_conversations": len(inventory["conversations"]),
        "saved_conversations": saved,
        "retained_conversations": retained if index.is_file() else None,
        "project_count": len(inventory.get("projects", [])),
        "scopes": sorted({chain["scope"] for chain in inventory.get("chains", [])}),
        "normalizer_version": normalizer,
        "audit_state": terminal_state,
        "warnings": warnings,
    }


def probe_chatgpt_path(path: Path, *, home: Path | None = None) -> dict:
    """A collection can contain supported and unsupported archives; report both."""
    result = {
        "path": str(path.expanduser()),
        "recognized": False,
        "readable": False,
        "runtime_readable": None,
        "state": "unrecognized",
        "archives": [],
        "warnings": [],
        "error": None,
    }
    try:
        root = path.expanduser().resolve()
        result["path"] = str(root)
        home = (home or Path.home()).resolve()
        if not root.is_relative_to(home):
            result.update(state="outside_home", runtime_readable=False)
            raise ValueError(
                "Archive must be inside the runtime's readable home; "
                "symlinks cannot bypass this boundary."
            )
        if not root.exists():
            result.update(state="missing", runtime_readable=False)
            raise ValueError(f"Archive path does not exist: {root}")
        if not root.is_dir() or not os.access(root, os.R_OK | os.X_OK):
            result.update(state="unreadable", runtime_readable=False)
            raise ValueError(f"Archive directory is not readable: {root}")
        result["readable"] = True
        candidates = (
            [root]
            if (root / "inventory.json").exists() or (root / "archive.json").exists()
            else sorted(root.glob("ChatGPTExport-*"))
        )
        for candidate in candidates:
            try:
                archive = candidate.resolve()
                if not archive.is_relative_to(home):
                    raise ValueError(f"Archive target is outside the readable home: {candidate}")
                result["archives"].append(_archive_info(archive))
            except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError) as exc:
                result["warnings"].append(str(exc))
        if not result["archives"]:
            raise ValueError(
                "; ".join(result["warnings"]) or f"No supported ChatGPTExporter archive at {root}"
            )
        result["recognized"] = True
        warnings = result["warnings"]
        for archive in result["archives"]:
            warnings.extend(archive["warnings"])
        result["state"] = (
            "partial"
            if warnings
            else "available"
            if any(a["saved_conversations"] for a in result["archives"])
            else "empty"
        )
    except (OSError, ValueError, RuntimeError) as exc:
        result["error"] = str(exc)
    return result
