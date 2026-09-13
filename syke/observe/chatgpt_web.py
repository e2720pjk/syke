"""Bounded, projection-first observation for one ChatGPTExporter archive.

ChatGPT Web is deliberately not read from inside Pi.  Syke validates the
explicitly configured archive, enumerates its conversation index, applies
project scope before opening conversation bodies, and writes an allowlisted
current-branch projection into the Syke workspace.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

logger = logging.getLogger(__name__)

CHATGPT_WEB_SOURCE = "chatgpt-web"
EXPECTED_PROVIDER = "chatgpt-web"
EXPECTED_SCHEMA_VERSION = 1
EXPECTED_NORMALIZER_VERSION = "chatgpt-web-v1"

# Hard ceilings keep a selected snapshot from becoming an unbounded Pi input.
MAX_ARCHIVE_METADATA_BYTES = 1 * 1024 * 1024
MAX_INDEX_BYTES = 64 * 1024 * 1024
MAX_INDEX_ROW_BYTES = 1 * 1024 * 1024
MAX_PROJECT_METADATA_BYTES = 2 * 1024 * 1024
MAX_CONVERSATION_BYTES = 32 * 1024 * 1024
MAX_PART_CHARS = 32_000
MAX_CONVERSATION_CHARS = 96_000
MAX_PROJECTION_BYTES = 16 * 1024 * 1024
MAX_DIAGNOSTIC_ITEMS = 128

_KNOWN_SCOPES = {"main", "project", "shared", "archived"}


class ChatGPTWebSourceError(RuntimeError):
    """A fail-closed archive or projection error."""

    def __init__(self, reason: str, detail: str, *, conversation_id: str | None = None):
        self.reason = reason
        self.detail = detail
        self.conversation_id = conversation_id
        super().__init__(detail)

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "source": CHATGPT_WEB_SOURCE,
            "reason": self.reason,
            "detail": self.detail,
        }
        if self.conversation_id:
            result["conversation_id"] = self.conversation_id
        return result


@dataclass(frozen=True)
class _ArchiveInfo:
    root: Path
    provider: str
    schema_version: int
    normalizer_version: str
    workspace_fingerprint: str
    expected_conversation_count: int | None = None
    complete_conversation_count: int | None = None
    validation_terminal_state: str | None = None
    partial_asset_reference_count: int | None = None
    validation_project_count: int | None = None


@dataclass
class _IndexRow:
    conversation_id: str
    logical_key: str
    title: str
    normalized_path: Path
    normalized_hash: str | None
    memberships: list[dict[str, str]]
    create_time: object | None
    update_time: object | None


@dataclass(frozen=True)
class _IndexRead:
    rows: tuple[_IndexRow, ...]
    raw_rows: int
    failures: tuple[dict[str, object], ...]
    failure_reasons: Counter[str]


@dataclass
class ChatGPTWebRun:
    """Aggregate diagnostics for one projection attempt.

    ``to_dict`` is intentionally Pi-safe: it omits the configured archive
    path, project names, and any conversation content.  ``inspect`` is the
    human/control diagnostic surface and may expose those operational values.
    """

    root: str = ""
    archive_validated: bool = False
    accepted: bool = False
    provider: str | None = None
    schema_version: int | None = None
    normalizer_version: str | None = None
    workspace_fingerprint: str | None = None
    index_rows: int = 0
    conversations_discovered: int = 0
    conversations_eligible: int = 0
    conversations_processed: int = 0
    conversations_skipped: int = 0
    conversations_failed: int = 0
    projection_bytes: int = 0
    projection_path: str | None = None
    expected_conversation_count: int | None = None
    complete_conversation_count: int | None = None
    validation_terminal_state: str | None = None
    partial_asset_reference_count: int | None = None
    validation_project_count: int | None = None
    excluded_project_ids: tuple[str, ...] = ()
    projects: list[dict[str, object]] = field(default_factory=list)
    skipped: list[dict[str, object]] = field(default_factory=list)
    failed: list[dict[str, object]] = field(default_factory=list)
    skip_reasons: Counter[str] = field(default_factory=Counter)
    failure_reasons: Counter[str] = field(default_factory=Counter)
    warnings: list[str] = field(default_factory=list)

    def add_skip(
        self,
        reason: str,
        *,
        conversation_id: str | None = None,
        project_id: str | None = None,
        detail: str | None = None,
    ) -> None:
        self.conversations_skipped += 1
        self.skip_reasons[reason] += 1
        if len(self.skipped) >= MAX_DIAGNOSTIC_ITEMS:
            return
        item: dict[str, object] = {"reason": reason}
        if conversation_id:
            item["conversation_id"] = conversation_id
        if project_id:
            item["project_id"] = project_id
        if detail:
            item["detail"] = detail[:500]
        self.skipped.append(item)

    def add_failure(
        self,
        reason: str,
        *,
        conversation_id: str | None = None,
        detail: str | None = None,
    ) -> None:
        self.conversations_failed += 1
        self.failure_reasons[reason] += 1
        if len(self.failed) >= MAX_DIAGNOSTIC_ITEMS:
            return
        item: dict[str, object] = {"reason": reason}
        if conversation_id:
            item["conversation_id"] = conversation_id
        if detail:
            item["detail"] = detail[:500]
        self.failed.append(item)

    def to_dict(self) -> dict[str, object]:
        """Serialize bounded diagnostics without the raw archive boundary."""
        return {
            "source": CHATGPT_WEB_SOURCE,
            "archive_validated": self.archive_validated,
            "accepted": self.accepted,
            "provider": self.provider,
            "schema_version": self.schema_version,
            "normalizer_version": self.normalizer_version,
            "workspace_fingerprint": self.workspace_fingerprint,
            "index_rows": self.index_rows,
            "conversations_discovered": self.conversations_discovered,
            "conversations_eligible": self.conversations_eligible,
            "conversations_processed": self.conversations_processed,
            "conversations_skipped": self.conversations_skipped,
            "conversations_failed": self.conversations_failed,
            "projection_bytes": self.projection_bytes,
            "projection_path": self.projection_path,
            "expected_conversation_count": self.expected_conversation_count,
            "complete_conversation_count": self.complete_conversation_count,
            "validation_terminal_state": self.validation_terminal_state,
            "partial_asset_reference_count": self.partial_asset_reference_count,
            "validation_project_count": self.validation_project_count,
            "excluded_project_ids": list(self.excluded_project_ids),
            "skipped": self.skipped,
            "failed": self.failed,
            "skip_reasons": dict(sorted(self.skip_reasons.items())),
            "failure_reasons": dict(sorted(self.failure_reasons.items())),
            "warnings": self.warnings[:MAX_DIAGNOSTIC_ITEMS],
        }


def _safe_identifier(value: object, field_name: str, *, allow_slash: bool = False) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 1024
        or any(ord(char) < 32 for char in value)
    ):
        raise ChatGPTWebSourceError("invalid_archive_identity", f"{field_name} is invalid")
    if "\\" in value or (not allow_slash and "/" in value):
        raise ChatGPTWebSourceError("invalid_archive_identity", f"{field_name} is invalid")
    return value


def _assert_no_symlink_components(root: Path, path: Path) -> None:
    """Reject symlinked archive components, including links to safe targets."""
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ChatGPTWebSourceError(
            "archive_path_escape", "archive path is outside the archive root"
        ) from exc
    current = root
    for component in relative.parts:
        current /= component
        if current.is_symlink():
            raise ChatGPTWebSourceError(
                "archive_path_escape", f"archive path component is symlinked: {component}"
            )


def _safe_archive_path(
    root: Path,
    raw_path: object,
    *,
    expected_conversation_id: str | None = None,
) -> Path:
    if not isinstance(raw_path, str) or not raw_path or len(raw_path) > 2048:
        raise ChatGPTWebSourceError("invalid_archive_path", "normalizedPath is invalid")
    if "\x00" in raw_path or "\\" in raw_path:
        raise ChatGPTWebSourceError("invalid_archive_path", "normalizedPath is invalid")
    path = PurePosixPath(raw_path)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ChatGPTWebSourceError("archive_path_escape", "normalizedPath escapes the archive")
    if len(path.parts) < 3 or path.parts[0] != "conversations" or path.name != "conversation.json":
        raise ChatGPTWebSourceError(
            "invalid_archive_path", "normalizedPath is not a conversation body"
        )
    if expected_conversation_id is not None and path.parts != (
        "conversations",
        expected_conversation_id,
        "conversation.json",
    ):
        raise ChatGPTWebSourceError(
            "invalid_archive_path", "normalizedPath does not match conversationId"
        )

    archive_root = root.resolve()
    raw_candidate = root / Path(*path.parts)
    _assert_no_symlink_components(root, raw_candidate)
    try:
        candidate = raw_candidate.resolve()
        candidate.relative_to(archive_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ChatGPTWebSourceError(
            "archive_path_escape", "normalizedPath escapes the archive"
        ) from exc
    return candidate


def _load_json(
    path: Path,
    *,
    max_bytes: int,
    missing_reason: str,
    invalid_reason: str,
    archive_root: Path | None = None,
) -> object:
    try:
        if archive_root is not None:
            _assert_no_symlink_components(archive_root, path)
        elif path.is_symlink():
            raise ChatGPTWebSourceError(
                "archive_path_escape", f"archive file is symlinked: {path.name}"
            )
        if not path.is_file():
            raise ChatGPTWebSourceError(
                missing_reason, f"required archive file is missing: {path.name}"
            )
        if path.stat().st_size > max_bytes:
            raise ChatGPTWebSourceError(
                "read_limit_exceeded", f"archive file exceeds {max_bytes} bytes"
            )
        return json.loads(path.read_text(encoding="utf-8"))
    except ChatGPTWebSourceError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ChatGPTWebSourceError(
            invalid_reason, f"could not read archive file: {path.name}"
        ) from exc


def _sha256_file(path: Path, *, max_bytes: int, archive_root: Path | None = None) -> str:
    try:
        if archive_root is not None:
            _assert_no_symlink_components(archive_root, path)
        elif path.is_symlink():
            raise ChatGPTWebSourceError(
                "archive_path_escape", f"indexed file is symlinked: {path.name}"
            )
        if path.stat().st_size > max_bytes:
            raise ChatGPTWebSourceError(
                "read_limit_exceeded", "indexed file exceeds the read limit"
            )
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except ChatGPTWebSourceError:
        raise
    except OSError as exc:
        raise ChatGPTWebSourceError(
            "archive_read_failed", "could not hash the conversation index"
        ) from exc


def _optional_json(root: Path, relative: str, *, max_bytes: int) -> object | None:
    path = root / relative
    _assert_no_symlink_components(root, path)
    if not path.exists():
        return None
    return _load_json(
        path,
        max_bytes=max_bytes,
        missing_reason="archive_incomplete",
        invalid_reason="invalid_archive_json",
        archive_root=root,
    )

def _validated_count(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ChatGPTWebSourceError("invalid_validation_report", f"{field_name} is invalid")
    return value


def _validate_optional_metadata(
    root: Path, manifest: Mapping[str, Any], index_path: Path
) -> dict[str, int | str | None]:
    """Validate exporter completeness/hash metadata when the archive ships it."""
    current_hashes = manifest.get("currentIndexHashes")
    if current_hashes is not None:
        if not isinstance(current_hashes, dict):
            raise ChatGPTWebSourceError(
                "invalid_archive_metadata", "currentIndexHashes must be an object"
            )
        expected_hash = current_hashes.get("conversations")
        if not isinstance(expected_hash, str) or len(expected_hash) != 64:
            raise ChatGPTWebSourceError(
                "invalid_archive_metadata", "currentIndexHashes.conversations is invalid"
            )
        if (
            _sha256_file(index_path, max_bytes=MAX_INDEX_BYTES, archive_root=root) != expected_hash
        ):
            raise ChatGPTWebSourceError(
                "archive_hash_mismatch", "conversation index hash does not match archive"
            )

    result: dict[str, int | str | None] = {
        "expected": None,
        "complete": None,
        "terminal": None,
        "partial_assets": None,
        "projects": None,
    }

    report_path = root / "reports" / "validation.json"
    report = _optional_json(root, "reports/validation.json", max_bytes=MAX_ARCHIVE_METADATA_BYTES)
    if report_path.is_file():
        if not isinstance(report, dict):
            raise ChatGPTWebSourceError(
                "invalid_validation_report", "validation.json must contain an object"
            )
        for key, expected in (
            ("provider", EXPECTED_PROVIDER),
            ("schemaVersion", EXPECTED_SCHEMA_VERSION),
            ("workspaceFingerprint", manifest.get("workspaceFingerprint")),
        ):
            if report.get(key) != expected:
                raise ChatGPTWebSourceError(
                    "invalid_validation_report", f"validation.json {key} does not match archive"
                )
        findings = report.get("findings", [])
        if not isinstance(findings, list) or findings:
            raise ChatGPTWebSourceError(
                "archive_validation_findings", "validation.json contains findings"
            )
        terminal = report.get("terminalState")
        if not isinstance(terminal, str) or not terminal.startswith("conversations_complete"):
            raise ChatGPTWebSourceError(
                "archive_incomplete", "conversation validation is not complete"
            )
        expected_count = _validated_count(
            report.get("expectedConversationCount"), "expectedConversationCount"
        )
        complete_count = _validated_count(
            report.get("completeConversationCount"), "completeConversationCount"
        )
        if expected_count is None or complete_count is None or expected_count != complete_count:
            raise ChatGPTWebSourceError(
                "archive_incomplete", "expected and completed conversation counts differ"
            )
        result["expected"] = expected_count
        result["complete"] = complete_count
        result["projects"] = _validated_count(report.get("projectCount"), "projectCount")
        partial_assets = report.get("partialAssetReferenceCount", 0)
        result["partial_assets"] = _validated_count(partial_assets, "partialAssetReferenceCount")
        result["terminal"] = terminal
        report_hash = report.get("conversationsIndexHash")
        if report_hash is not None:
            if not isinstance(report_hash, str) or len(report_hash) != 64:
                raise ChatGPTWebSourceError(
                    "invalid_validation_report", "validation report index hash is invalid"
                )
            if (
                _sha256_file(index_path, max_bytes=MAX_INDEX_BYTES, archive_root=root)
                != report_hash
            ):
                raise ChatGPTWebSourceError(
                    "archive_hash_mismatch", "validation report index hash does not match archive"
                )

    inventory_path = root / "inventory.json"
    inventory = _optional_json(root, "inventory.json", max_bytes=MAX_ARCHIVE_METADATA_BYTES)
    inventory_count: int | None = None
    if inventory_path.is_file():
        if not isinstance(inventory, dict):
            raise ChatGPTWebSourceError(
                "invalid_inventory", "inventory.json must contain an object"
            )
        for key, expected in (
            ("provider", EXPECTED_PROVIDER),
            ("schemaVersion", EXPECTED_SCHEMA_VERSION),
            ("workspaceFingerprint", manifest.get("workspaceFingerprint")),
        ):
            if inventory.get(key) != expected:
                raise ChatGPTWebSourceError(
                    "invalid_inventory", f"inventory.json {key} does not match archive"
                )
        if inventory.get("complete") is not True:
            raise ChatGPTWebSourceError("archive_incomplete", "inventory.json is not complete")
        absent = inventory.get("absentConversations", [])
        if not isinstance(absent, list) or absent:
            raise ChatGPTWebSourceError("archive_incomplete", "inventory has absent conversations")
        conversations = inventory.get("conversations")
        if not isinstance(conversations, list):
            raise ChatGPTWebSourceError(
                "invalid_inventory", "inventory conversations must be a list"
            )
        inventory_count = len(conversations)
        chains = inventory.get("chains", [])
        if not isinstance(chains, list) or any(
            not isinstance(chain, dict) or chain.get("complete") is not True for chain in chains
        ):
            raise ChatGPTWebSourceError("archive_incomplete", "an inventory chain is incomplete")

    if inventory_count is not None:
        # The inventory is the exporter listing, while the validated count is
        # the normalized conversation index. The listing may contain records
        # that the normalizer intentionally omits; it must not contain fewer
        # records than the validated snapshot claims.
        if result["expected"] is not None and inventory_count < result["expected"]:
            raise ChatGPTWebSourceError(
                "archive_incomplete", "inventory has fewer records than validation"
            )
        if result["expected"] is None:
            result["expected"] = inventory_count
    return result


def _validate_archive(root: str | Path) -> _ArchiveInfo:
    raw_root = Path(root).expanduser()
    if raw_root.is_symlink():
        raise ChatGPTWebSourceError(
            "archive_path_escape", "configured archive root must not be a symlink"
        )
    try:
        archive_root = raw_root.resolve()
    except (OSError, RuntimeError) as exc:
        raise ChatGPTWebSourceError(
            "archive_root_unreadable", "could not resolve archive root"
        ) from exc
    if not archive_root.is_dir():
        raise ChatGPTWebSourceError(
            "archive_root_missing", "configured archive root is not a directory"
        )

    manifest_path = archive_root / "archive.json"
    manifest = _load_json(
        manifest_path,
        max_bytes=MAX_ARCHIVE_METADATA_BYTES,
        missing_reason="archive_manifest_missing",
        invalid_reason="invalid_archive_json",
        archive_root=archive_root,
    )
    if not isinstance(manifest, dict):
        raise ChatGPTWebSourceError("invalid_archive_manifest", "archive.json must be an object")
    if manifest.get("provider") != EXPECTED_PROVIDER:
        raise ChatGPTWebSourceError("unsupported_provider", "archive provider is not chatgpt-web")
    if manifest.get("schemaVersion") != EXPECTED_SCHEMA_VERSION or isinstance(
        manifest.get("schemaVersion"), bool
    ):
        raise ChatGPTWebSourceError("unsupported_schema", "archive schemaVersion is unsupported")
    if manifest.get("normalizerVersion") != EXPECTED_NORMALIZER_VERSION:
        raise ChatGPTWebSourceError(
            "unsupported_normalizer", "archive normalizerVersion is unsupported"
        )
    fingerprint = _safe_identifier(manifest.get("workspaceFingerprint"), "workspaceFingerprint")
    if len(fingerprint) > 256:
        raise ChatGPTWebSourceError("invalid_archive_identity", "workspaceFingerprint is invalid")

    index_path = archive_root / "indexes" / "conversations.jsonl"
    _assert_no_symlink_components(archive_root, index_path)
    if not index_path.is_file():
        raise ChatGPTWebSourceError("index_missing", "conversation index is missing")
    metadata = _validate_optional_metadata(archive_root, manifest, index_path)
    return _ArchiveInfo(
        root=archive_root,
        provider=EXPECTED_PROVIDER,
        schema_version=EXPECTED_SCHEMA_VERSION,
        normalizer_version=EXPECTED_NORMALIZER_VERSION,
        workspace_fingerprint=fingerprint,
        expected_conversation_count=metadata["expected"],
        complete_conversation_count=metadata["complete"],
        validation_terminal_state=metadata["terminal"],
        partial_asset_reference_count=metadata["partial_assets"],
        validation_project_count=metadata["projects"],
    )


def _normalize_memberships(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ChatGPTWebSourceError("invalid_membership", "memberships must be a list")
    result: list[dict[str, str]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ChatGPTWebSourceError("invalid_membership", "membership must be an object")
        scope = item.get("scope")
        if not isinstance(scope, str) or scope not in _KNOWN_SCOPES:
            raise ChatGPTWebSourceError("invalid_membership", "membership scope is unsupported")
        membership: dict[str, str] = {"scope": scope}
        if scope == "project":
            project_id = item.get("projectId")
            membership["project_id"] = _safe_identifier(project_id, "projectId")
        key = tuple(sorted(membership.items()))
        if key not in seen:
            seen.add(key)
            result.append(membership)
    return result


def _merge_memberships(rows: Iterable[list[dict[str, str]]]) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for memberships in rows:
        for membership in memberships:
            safe: dict[str, str] = {}
            scope = membership.get("scope")
            if scope in _KNOWN_SCOPES:
                safe["scope"] = scope
            if scope == "project" and isinstance(membership.get("project_id"), str):
                safe["project_id"] = membership["project_id"]
            if "scope" not in safe or (scope == "project" and "project_id" not in safe):
                continue
            key = tuple(sorted(safe.items()))
            if key not in seen:
                seen.add(key)
                merged.append(safe)
    return merged


def _parse_index_row(payload: object, *, root: Path) -> _IndexRow:
    if not isinstance(payload, dict):
        raise ChatGPTWebSourceError("invalid_index_row", "index row must be an object")
    conversation_id = _safe_identifier(payload.get("conversationId"), "conversationId")
    logical_key = _safe_identifier(payload.get("logicalKey"), "logicalKey", allow_slash=True)
    title = payload.get("title", "")
    if not isinstance(title, str):
        title = ""
    normalized_hash = payload.get("normalizedHash")
    if normalized_hash is not None:
        normalized_hash = _safe_identifier(normalized_hash, "normalizedHash")[:256]
    return _IndexRow(
        conversation_id=conversation_id,
        logical_key=logical_key,
        title=title[:512],
        normalized_path=_safe_archive_path(
            root,
            payload.get("normalizedPath"),
            expected_conversation_id=conversation_id,
        ),
        normalized_hash=normalized_hash[:256] if normalized_hash else None,
        memberships=_normalize_memberships(payload.get("memberships", [])),
        create_time=payload.get("createTime"),
        update_time=payload.get("updateTime"),
    )


def _index_failure(
    failures: list[dict[str, object]],
    reasons: Counter[str],
    reason: str,
    *,
    line_number: int,
    detail: str,
    conversation_id: str | None = None,
) -> None:
    reasons[reason] += 1
    if len(failures) >= MAX_DIAGNOSTIC_ITEMS:
        return
    item: dict[str, object] = {"line": line_number, "reason": reason, "detail": detail[:500]}
    if conversation_id:
        item["conversation_id"] = conversation_id
    failures.append(item)


def _read_index(archive: _ArchiveInfo) -> _IndexRead:
    path = archive.root / "indexes" / "conversations.jsonl"
    try:
        if path.stat().st_size > MAX_INDEX_BYTES:
            raise ChatGPTWebSourceError(
                "read_limit_exceeded", "conversation index exceeds the read limit"
            )
    except OSError as exc:
        raise ChatGPTWebSourceError("index_missing", "conversation index cannot be read") from exc

    failures: list[dict[str, object]] = []
    reasons: Counter[str] = Counter()
    rows_by_id: dict[str, _IndexRow] = {}
    raw_rows = 0
    try:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                raw_rows += 1
                if len(line.encode("utf-8")) > MAX_INDEX_ROW_BYTES:
                    _index_failure(
                        failures,
                        reasons,
                        "index_row_too_large",
                        line_number=line_number,
                        detail="index row exceeds the read limit",
                    )
                    continue
                try:
                    payload = json.loads(line)
                    row = _parse_index_row(payload, root=archive.root)
                except ChatGPTWebSourceError as exc:
                    conversation_id = (
                        payload.get("conversationId")
                        if isinstance(payload, dict)
                        and isinstance(payload.get("conversationId"), str)
                        else None
                    )
                    _index_failure(
                        failures,
                        reasons,
                        exc.reason,
                        line_number=line_number,
                        detail=exc.detail,
                        conversation_id=conversation_id,
                    )
                    continue
                except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                    _index_failure(
                        failures,
                        reasons,
                        "invalid_json",
                        line_number=line_number,
                        detail=f"{exc.__class__.__name__}: invalid index row",
                    )
                    continue

                previous = rows_by_id.get(row.conversation_id)
                if previous is None:
                    rows_by_id[row.conversation_id] = row
                    continue
                if (
                    previous.logical_key != row.logical_key
                    or previous.normalized_path != row.normalized_path
                    or (
                        previous.normalized_hash is not None
                        and row.normalized_hash is not None
                        and previous.normalized_hash != row.normalized_hash
                    )
                ):
                    _index_failure(
                        failures,
                        reasons,
                        "duplicate_conversation_conflict",
                        line_number=line_number,
                        detail="duplicate conversation rows disagree on identity, path, or hash",
                        conversation_id=row.conversation_id,
                    )
                    continue
                previous.memberships = _merge_memberships((previous.memberships, row.memberships))
                if not previous.title and row.title:
                    previous.title = row.title
                if previous.update_time is None:
                    previous.update_time = row.update_time
                if previous.create_time is None:
                    previous.create_time = row.create_time
                if previous.normalized_hash is None:
                    previous.normalized_hash = row.normalized_hash
    except ChatGPTWebSourceError:
        raise
    except (OSError, UnicodeDecodeError) as exc:
        raise ChatGPTWebSourceError(
            "index_read_failed", "could not read conversation index"
        ) from exc

    return _IndexRead(tuple(rows_by_id.values()), raw_rows, tuple(failures), reasons)


def _project_summaries(root: Path, fingerprint: str) -> tuple[list[dict[str, object]], list[str]]:
    projects_root = root / "projects"
    _assert_no_symlink_components(root, projects_root)
    if not projects_root.is_dir():
        return [], []
    projects: list[dict[str, object]] = []
    warnings: list[str] = []
    try:
        entries = sorted(
            (
                entry
                for entry in projects_root.iterdir()
                if entry.is_dir() and not entry.is_symlink()
            ),
            key=lambda entry: entry.name,
        )
    except OSError as exc:
        return [], [f"project metadata unavailable ({exc.__class__.__name__})"]
    for entry in entries:
        project_id = entry.name
        if "/" in project_id or "\\" in project_id or not project_id:
            warnings.append("ignored invalid project directory")
            continue
        metadata_path = entry / "metadata.json"
        _assert_no_symlink_components(root, metadata_path)
        item: dict[str, object] = {
            "project_id": project_id,
            "name": "",
            "metadata": "missing",
            "project_key": f"{CHATGPT_WEB_SOURCE}/{fingerprint}/{project_id}",
        }
        try:
            if metadata_path.is_symlink():
                item["metadata"] = "invalid"
                warnings.append(f"project metadata is symlinked ({project_id})")
                projects.append(item)
                continue
            if not metadata_path.is_file():
                projects.append(item)
                continue
            if metadata_path.stat().st_size > MAX_PROJECT_METADATA_BYTES:
                item["metadata"] = "too_large"
                warnings.append(f"project metadata too large ({project_id})")
                projects.append(item)
                continue
            raw = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("projectId") not in (None, project_id):
                item["metadata"] = "invalid"
                warnings.append(f"project metadata identity mismatch ({project_id})")
            else:
                item["metadata"] = "ok"
                name = raw.get("name")
                if isinstance(name, str):
                    item["name"] = name[:256]
            projects.append(item)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            item["metadata"] = "invalid"
            warnings.append(f"project metadata unreadable ({project_id})")
            projects.append(item)
    return projects, warnings


def _excluded_project(memberships: Iterable[Mapping[str, str]], excluded: set[str]) -> str | None:
    for membership in memberships:
        if membership.get("scope") == "project" and membership.get("project_id") in excluded:
            return membership["project_id"]
    return None


def _timestamp_epoch(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
        return result if math.isfinite(result) else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            result = float(text)
            return result if math.isfinite(result) else None
        except ValueError:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.timestamp()
    return None


def _recency_key(row: _IndexRow) -> tuple[float, str]:
    timestamp = _timestamp_epoch(row.update_time)
    if timestamp is None:
        timestamp = _timestamp_epoch(row.create_time)
    return (timestamp if timestamp is not None else float("-inf"), row.conversation_id)


def _safe_scalar(value: object) -> object | None:
    if isinstance(value, str):
        return value[:256]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    return None


def _body_identity_error(
    body: Mapping[str, Any], archive: _ArchiveInfo, row: _IndexRow
) -> ChatGPTWebSourceError | None:
    if (
        body.get("provider") != archive.provider
        or body.get("schemaVersion") != archive.schema_version
        or body.get("normalizerVersion") != archive.normalizer_version
    ):
        return ChatGPTWebSourceError(
            "conversation_schema_mismatch",
            "normalized conversation markers do not match archive",
            conversation_id=row.conversation_id,
        )
    if body.get("workspaceFingerprint") != archive.workspace_fingerprint:
        return ChatGPTWebSourceError(
            "conversation_identity_mismatch",
            "normalized conversation fingerprint does not match archive",
            conversation_id=row.conversation_id,
        )
    if (
        body.get("conversationId") != row.conversation_id
        or body.get("logicalKey") != row.logical_key
    ):
        return ChatGPTWebSourceError(
            "conversation_identity_mismatch",
            "normalized conversation identity does not match index",
            conversation_id=row.conversation_id,
        )
    body_hash = body.get("normalizedHash")
    if body_hash is not None:
        if not isinstance(body_hash, str):
            return ChatGPTWebSourceError(
                "conversation_identity_mismatch",
                "normalized conversation hash is invalid",
                conversation_id=row.conversation_id,
            )
        try:
            _safe_identifier(body_hash, "normalizedHash")
        except ChatGPTWebSourceError:
            return ChatGPTWebSourceError(
                "conversation_identity_mismatch",
                "normalized conversation hash is invalid",
                conversation_id=row.conversation_id,
            )
    if row.normalized_hash and body_hash is not None and body_hash != row.normalized_hash:
        return ChatGPTWebSourceError(
            "conversation_identity_mismatch",
            "normalized conversation hash does not match index",
            conversation_id=row.conversation_id,
        )
    return None


def _graph_identifier(value: object, field_name: str) -> str:
    try:
        return _safe_identifier(value, field_name)
    except ChatGPTWebSourceError as exc:
        raise ChatGPTWebSourceError(
            "invalid_conversation_graph", f"{field_name} is invalid"
        ) from exc


def _validate_graph(
    body: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[str]]:
    nodes_raw = body.get("nodes")
    messages_raw = body.get("messages")
    roots_raw = body.get("rootNodeIds")
    current_id = body.get("currentNodeId")
    if (
        not isinstance(nodes_raw, list)
        or not isinstance(messages_raw, list)
        or not isinstance(roots_raw, list)
        or not isinstance(current_id, str)
    ):
        raise ChatGPTWebSourceError(
            "invalid_conversation_graph", "normalized conversation graph is incomplete"
        )

    nodes: dict[str, dict[str, Any]] = {}
    for raw in nodes_raw:
        if not isinstance(raw, dict):
            raise ChatGPTWebSourceError("invalid_conversation_graph", "node identity is invalid")
        node_id = _graph_identifier(raw.get("id"), "node id")
        if node_id in nodes:
            raise ChatGPTWebSourceError("invalid_conversation_graph", "node identity is duplicated")
        parent_raw = raw.get("parentId")
        parent = None if parent_raw is None else _graph_identifier(parent_raw, "node parentId")
        children = raw.get("childIds")
        if not isinstance(children, list):
            raise ChatGPTWebSourceError("invalid_conversation_graph", "node childIds is invalid")
        child_ids = [_graph_identifier(child, "node childId") for child in children]
        message_raw = raw.get("messageId")
        message_id = (
            None if message_raw is None else _graph_identifier(message_raw, "node messageId")
        )
        nodes[node_id] = {
            "parentId": parent,
            "childIds": child_ids,
            "messageId": message_id,
        }

    roots = [_graph_identifier(root, "root node id") for root in roots_raw]
    if len(roots) != len(roots_raw) or len(set(roots)) != len(roots):
        raise ChatGPTWebSourceError("invalid_conversation_graph", "rootNodeIds is invalid")
    root_set = set(roots)
    if not root_set or any(root not in nodes for root in roots):
        raise ChatGPTWebSourceError(
            "invalid_conversation_graph", "rootNodeIds references an unknown node"
        )
    for node_id, node in nodes.items():
        parent = node["parentId"]
        if (parent is None) != (node_id in root_set):
            raise ChatGPTWebSourceError(
                "invalid_conversation_graph", "node/root relationship is inconsistent"
            )
        if parent is not None and (parent not in nodes or node_id not in nodes[parent]["childIds"]):
            raise ChatGPTWebSourceError(
                "invalid_conversation_graph", "node parent relationship is inconsistent"
            )
        for child in node["childIds"]:
            if child not in nodes or nodes[child]["parentId"] != node_id:
                raise ChatGPTWebSourceError(
                    "invalid_conversation_graph", "node child relationship is inconsistent"
                )

    messages: dict[str, dict[str, Any]] = {}
    for raw in messages_raw:
        if not isinstance(raw, dict):
            raise ChatGPTWebSourceError("invalid_conversation_graph", "message identity is invalid")
        message_id = _graph_identifier(raw.get("id"), "message id")
        if message_id in messages:
            raise ChatGPTWebSourceError(
                "invalid_conversation_graph", "message identity is duplicated"
            )
        node_raw = raw.get("nodeId")
        node_id = None if node_raw is None else _graph_identifier(node_raw, "message nodeId")
        if node_id is not None and node_id not in nodes:
            raise ChatGPTWebSourceError("invalid_conversation_graph", "message nodeId is invalid")
        messages[message_id] = raw
    for node_id, node in nodes.items():
        message_id = node["messageId"]
        if message_id is None:
            continue
        if message_id not in messages:
            raise ChatGPTWebSourceError(
                "invalid_conversation_graph", "node references an unknown message"
            )
        message_node_id = messages[message_id].get("nodeId")
        if message_node_id is not None and message_node_id != node_id:
            raise ChatGPTWebSourceError(
                "invalid_conversation_graph", "node/message relationship is inconsistent"
            )
    for message_id, message in messages.items():
        message_node_id = message.get("nodeId")
        if message_node_id is not None and nodes[message_node_id]["messageId"] not in (
            None,
            message_id,
        ):
            raise ChatGPTWebSourceError(
                "invalid_conversation_graph", "message/node relationship is inconsistent"
            )

    current_id = _graph_identifier(current_id, "currentNodeId")
    if current_id not in nodes:
        raise ChatGPTWebSourceError(
            "invalid_conversation_graph", "currentNodeId references an unknown node"
        )
    # Detect cycles and ensure every node reaches one declared root.
    for start in nodes:
        seen: set[str] = set()
        current = start
        while current is not None:
            if current in seen:
                raise ChatGPTWebSourceError(
                    "invalid_conversation_graph", "node parent graph contains a cycle"
                )
            seen.add(current)
            parent = nodes[current]["parentId"]
            if parent is None:
                break
            current = parent
    branch_reverse: list[str] = []
    current = current_id
    while current is not None:
        branch_reverse.append(current)
        current = nodes[current]["parentId"]
    return nodes, messages, list(reversed(branch_reverse))


def _message_role(message: Mapping[str, Any]) -> str | None:
    role = message.get("role")
    if not isinstance(role, str):
        author = message.get("author")
        role = author.get("role") if isinstance(author, dict) else None
    return role if isinstance(role, str) else None


def _safe_part_id(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or "\x00" in value
        or "/" in value
        or "\\" in value
    ):
        return None
    return value


def _part_marker(part: Mapping[str, Any]) -> dict[str, object] | None:
    kind = part.get("kind")
    if kind == "asset":
        result: dict[str, object] = {"kind": "asset", "marker": "[asset omitted]"}
        asset_id = _safe_part_id(part.get("assetId"))
        if asset_id:
            result["asset_id"] = asset_id
        return result
    if kind == "citation":
        return {"kind": "citation", "marker": "[citation omitted]"}
    return None


def _bounded_text(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[-limit:], True


def _project_parts(
    parts: object, *, remaining: int, max_part_chars: int
) -> tuple[list[dict[str, object]], int]:
    if not isinstance(parts, list):
        raise ChatGPTWebSourceError("invalid_conversation_graph", "message.parts is invalid")
    selected: list[dict[str, object]] = []
    consumed = 0
    for raw in reversed(parts):
        if not isinstance(raw, dict):
            continue
        kind = raw.get("kind")
        if kind in {"asset", "citation"}:
            marker = _part_marker(raw)
            if marker is not None:
                selected.append(marker)
            continue
        if kind not in {"text", "code"} or not isinstance(raw.get("text"), str):
            continue
        available = max(0, remaining - consumed)
        if available <= 0 or max_part_chars <= 0:
            continue
        text, truncated = _bounded_text(raw["text"], min(max_part_chars, available))
        if not text:
            continue
        projected: dict[str, object] = {"kind": kind, "text": text}
        if truncated or len(raw["text"]) > len(text):
            projected["truncated"] = True
        language = raw.get("language")
        if kind == "code" and isinstance(language, str):
            projected["language"] = language[:64]
        selected.append(projected)
        consumed += len(text)
    selected.reverse()
    return selected, consumed


def project_normalized_conversation(
    body: Mapping[str, Any],
    *,
    workspace_fingerprint: str | None = None,
    expected_conversation_id: str | None = None,
    logical_key: str | None = None,
    normalized_hash: str | None = None,
    memberships: Iterable[Mapping[str, str]] | None = None,
    title: str | None = None,
    create_time: object | None = None,
    update_time: object | None = None,
    max_part_chars: int = MAX_PART_CHARS,
    max_conversation_chars: int = MAX_CONVERSATION_CHARS,
) -> dict[str, object]:
    """Project only the current graph branch and allowlisted content fields."""
    max_part_chars = max(0, min(int(max_part_chars), MAX_PART_CHARS))
    max_conversation_chars = max(0, min(int(max_conversation_chars), MAX_CONVERSATION_CHARS))
    if not isinstance(body, Mapping):
        raise ChatGPTWebSourceError(
            "invalid_conversation", "normalized conversation must be an object"
        )
    fingerprint = _safe_identifier(
        workspace_fingerprint or body.get("workspaceFingerprint"), "workspaceFingerprint"
    )
    if len(fingerprint) > 256:
        raise ChatGPTWebSourceError(
            "invalid_archive_identity", "conversation fingerprint is invalid"
        )
    conversation_id = body.get("conversationId")
    if expected_conversation_id is not None and conversation_id != expected_conversation_id:
        raise ChatGPTWebSourceError(
            "conversation_identity_mismatch",
            "normalized conversation identity does not match index",
            conversation_id=expected_conversation_id,
        )
    conversation_id = _safe_identifier(conversation_id, "conversationId")
    if (
        body.get("provider") != EXPECTED_PROVIDER
        or body.get("schemaVersion") != EXPECTED_SCHEMA_VERSION
        or body.get("normalizerVersion") != EXPECTED_NORMALIZER_VERSION
    ):
        raise ChatGPTWebSourceError(
            "conversation_schema_mismatch",
            "normalized conversation markers are unsupported",
            conversation_id=conversation_id,
        )
    if body.get("workspaceFingerprint") != fingerprint:
        raise ChatGPTWebSourceError(
            "conversation_identity_mismatch",
            "normalized conversation fingerprint is inconsistent",
            conversation_id=conversation_id,
        )
    if logical_key is not None and body.get("logicalKey") != logical_key:
        raise ChatGPTWebSourceError(
            "conversation_identity_mismatch",
            "normalized conversation logical key is inconsistent",
            conversation_id=conversation_id,
        )
    body_hash = body.get("normalizedHash")
    if body_hash is not None:
        if not isinstance(body_hash, str):
            raise ChatGPTWebSourceError(
                "conversation_identity_mismatch",
                "normalized conversation hash is invalid",
                conversation_id=conversation_id,
            )
        try:
            _safe_identifier(body_hash, "normalizedHash")
        except ChatGPTWebSourceError as exc:
            raise ChatGPTWebSourceError(
                "conversation_identity_mismatch",
                "normalized conversation hash is invalid",
                conversation_id=conversation_id,
            ) from exc
    if normalized_hash is not None and body_hash is not None and body_hash != normalized_hash:
        raise ChatGPTWebSourceError(
            "conversation_identity_mismatch",
            "normalized conversation hash is inconsistent",
            conversation_id=conversation_id,
        )

    nodes, messages_by_id, branch_node_ids = _validate_graph(body)
    output_memberships = (
        _normalize_memberships(body.get("memberships", []))
        if memberships is None
        else _merge_memberships([list(memberships)])
    )
    raw_title = title if isinstance(title, str) else body.get("title", "")
    safe_title = raw_title[:512] if isinstance(raw_title, str) else ""
    safe_normalized_hash = (
        _safe_identifier(normalized_hash, "normalizedHash") if normalized_hash is not None else None
    )
    raw_logical_key = logical_key if logical_key is not None else body.get("logicalKey", "")
    safe_logical_key = _safe_identifier(raw_logical_key, "logicalKey", allow_slash=True)
    result: dict[str, object] = {
        "source": CHATGPT_WEB_SOURCE,
        "conversation_id": conversation_id,
        "conversation_key": f"{CHATGPT_WEB_SOURCE}/{fingerprint}/{conversation_id}",
        "logical_key": safe_logical_key,
        "title": safe_title,
        "workspace_fingerprint": fingerprint,
        "memberships": output_memberships,
        "create_time": _safe_scalar(
            create_time if create_time is not None else body.get("createTime")
        ),
        "update_time": _safe_scalar(
            update_time if update_time is not None else body.get("updateTime")
        ),
        "normalized_hash": safe_normalized_hash,
        "branch_node_ids": branch_node_ids,
        "messages": [],
    }

    remaining = max(0, int(max_conversation_chars))
    projected_messages: list[dict[str, object]] = []
    for node_id in reversed(branch_node_ids):
        node = nodes[node_id]
        message_id = node["messageId"]
        if message_id is None:
            continue
        message = messages_by_id[message_id]
        if _message_role(message) not in {"user", "assistant"}:
            continue
        parts, consumed = _project_parts(
            message.get("parts"),
            remaining=remaining,
            max_part_chars=max(0, int(max_part_chars)),
        )
        if not parts:
            continue
        message_key = f"{CHATGPT_WEB_SOURCE}/{fingerprint}/{conversation_id}/{message_id}"
        projected: dict[str, object] = {
            "message_id": message_id,
            "message_key": message_key,
            "role": _message_role(message),
            "create_time": _safe_scalar(message.get("createTime")),
            "update_time": _safe_scalar(message.get("updateTime")),
            "parts": parts,
        }
        projected_messages.append(projected)
        remaining -= consumed
    projected_messages.reverse()
    result["messages"] = projected_messages
    return result


def _read_conversation_body(path: Path) -> object:
    return _load_json(
        path,
        max_bytes=MAX_CONVERSATION_BYTES,
        missing_reason="normalized_body_missing",
        invalid_reason="normalized_body_invalid",
    )


def _normalize_excluded_project_ids(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _safe_identifier(value, "excluded_project_id")
        if item not in seen:
            seen.add(item)
            result.append(item)
    return tuple(sorted(result))


def project_chatgpt_web_archive(
    root: str | Path,
    excluded_project_ids: Iterable[str] = (),
    *,
    output_path: Path | None = None,
    max_part_chars: int = MAX_PART_CHARS,
    max_conversation_chars: int = MAX_CONVERSATION_CHARS,
    max_projection_bytes: int = MAX_PROJECTION_BYTES,
) -> ChatGPTWebRun:
    """Validate and project an archive; exclusion is applied before body reads."""
    max_part_chars = max(0, min(int(max_part_chars), MAX_PART_CHARS))
    max_conversation_chars = max(0, min(int(max_conversation_chars), MAX_CONVERSATION_CHARS))
    max_projection_bytes = max(0, min(int(max_projection_bytes), MAX_PROJECTION_BYTES))
    archive = _validate_archive(root)
    if output_path is not None:
        destination = Path(output_path).expanduser().resolve()
        if _paths_overlap(destination, archive.root):
            raise ChatGPTWebSourceError(
                "projection_archive_overlap",
                "projection output must remain outside the exporter archive",
            )
    index = _read_index(archive)
    excluded = _normalize_excluded_project_ids(excluded_project_ids)
    projects, project_warnings = _project_summaries(archive.root, archive.workspace_fingerprint)
    run = ChatGPTWebRun(
        root=str(archive.root),
        archive_validated=not index.failures,
        provider=archive.provider,
        schema_version=archive.schema_version,
        normalizer_version=archive.normalizer_version,
        workspace_fingerprint=archive.workspace_fingerprint,
        index_rows=index.raw_rows,
        conversations_discovered=len(index.rows),
        expected_conversation_count=archive.expected_conversation_count,
        complete_conversation_count=archive.complete_conversation_count,
        validation_terminal_state=archive.validation_terminal_state,
        partial_asset_reference_count=archive.partial_asset_reference_count,
        validation_project_count=archive.validation_project_count,
        excluded_project_ids=excluded,
        projects=projects,
        warnings=project_warnings,
    )
    if archive.partial_asset_reference_count:
        run.warnings.append(
            f"asset references are partial ({archive.partial_asset_reference_count}); "
            "assets were not read"
        )
    if (
        archive.expected_conversation_count is not None
        and archive.expected_conversation_count != len(index.rows)
    ):
        run.archive_validated = False
        run.failure_reasons["conversation_count_mismatch"] += 1
        run.failed.append(
            {
                "reason": "conversation_count_mismatch",
                "detail": "validated conversation count differs from index",
            }
        )
    if (
        archive.complete_conversation_count is not None
        and archive.complete_conversation_count != len(index.rows)
    ):
        run.archive_validated = False
        run.failure_reasons["conversation_count_mismatch"] += 1
        run.failed.append(
            {
                "reason": "conversation_count_mismatch",
                "detail": "complete conversation count differs from index",
            }
        )
    if (
        archive.validation_project_count is not None
        and len(projects) != archive.validation_project_count
    ):
        run.archive_validated = False
        run.failure_reasons["project_count_mismatch"] += 1
        run.failed.append(
            {
                "reason": "project_count_mismatch",
                "detail": "validated project count differs from project metadata",
            }
        )
    for failure in index.failures:
        run.add_failure(
            str(failure.get("reason", "invalid_index")),
            conversation_id=failure.get("conversation_id")
            if isinstance(failure.get("conversation_id"), str)
            else None,
            detail=str(failure.get("detail", "invalid index row")),
        )
    if not run.archive_validated:
        run.accepted = False
        return run

    excluded_set = set(excluded)
    eligible: list[_IndexRow] = []
    for row in index.rows:
        denied_project = _excluded_project(row.memberships, excluded_set)
        if denied_project is not None:
            # This happens before any stat/open of the normalized body.
            run.add_skip(
                "excluded_project", conversation_id=row.conversation_id, project_id=denied_project
            )
        else:
            eligible.append(row)
    run.conversations_eligible = len(eligible)
    ordered = sorted(eligible, key=lambda row: (-_recency_key(row)[0], _recency_key(row)[1]))
    handle = None
    projection_limit_hit = max_projection_bytes == 0
    try:
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            handle = output_path.open("w", encoding="utf-8")
        for row in ordered:
            try:
                if projection_limit_hit:
                    run.add_failure(
                        "projection_read_limit",
                        conversation_id=row.conversation_id,
                        detail="projection byte limit reached before body read",
                    )
                    continue
                body = _read_conversation_body(row.normalized_path)
                if not isinstance(body, Mapping):
                    raise ChatGPTWebSourceError(
                        "normalized_body_invalid",
                        "normalized conversation is not an object",
                        conversation_id=row.conversation_id,
                    )
                identity_error = _body_identity_error(body, archive, row)
                if identity_error is not None:
                    raise identity_error
                projected = project_normalized_conversation(
                    body,
                    workspace_fingerprint=archive.workspace_fingerprint,
                    expected_conversation_id=row.conversation_id,
                    logical_key=row.logical_key,
                    normalized_hash=row.normalized_hash,
                    memberships=row.memberships,
                    title=row.title,
                    create_time=row.create_time,
                    update_time=row.update_time,
                    max_part_chars=max_part_chars,
                    max_conversation_chars=max_conversation_chars,
                )
                serialized = json.dumps(projected, ensure_ascii=False, separators=(",", ":")) + "\n"
                encoded_size = len(serialized.encode("utf-8"))
                if run.projection_bytes + encoded_size > max_projection_bytes:
                    projection_limit_hit = True
                    run.add_failure(
                        "projection_read_limit",
                        conversation_id=row.conversation_id,
                        detail="projection byte limit reached",
                    )
                    continue
                if handle is not None:
                    handle.write(serialized)
                run.projection_bytes += encoded_size
                run.conversations_processed += 1
            except ChatGPTWebSourceError as exc:
                run.add_failure(exc.reason, conversation_id=row.conversation_id, detail=exc.detail)
            except (OSError, TypeError, ValueError) as exc:
                run.add_failure(
                    "projection_failed",
                    conversation_id=row.conversation_id,
                    detail=f"{exc.__class__.__name__}: projection failed",
                )
    finally:
        if handle is not None:
            handle.close()
    run.accepted = run.archive_validated and run.conversations_failed == 0
    return run


def inspect_chatgpt_web_archive(
    root: str | Path,
    excluded_project_ids: Iterable[str] = (),
) -> dict[str, object]:
    """Return human-facing aggregate archive/index/project diagnostics.

    Conversation bodies are never opened by this function.
    """
    try:
        archive = _validate_archive(root)
        excluded = _normalize_excluded_project_ids(excluded_project_ids)
        index = _read_index(archive)
        projects, warnings = _project_summaries(archive.root, archive.workspace_fingerprint)
        if archive.partial_asset_reference_count:
            warnings.append(
                f"asset references are partial ({archive.partial_asset_reference_count}); "
                "assets were not read"
            )
        validation_error: ChatGPTWebSourceError | None = None
        if index.failures:
            first_failure = index.failures[0]
            conversation_id = first_failure.get("conversation_id")
            validation_error = ChatGPTWebSourceError(
                str(first_failure.get("reason", "invalid_index")),
                str(first_failure.get("detail", "conversation index validation failed")),
                conversation_id=conversation_id if isinstance(conversation_id, str) else None,
            )
        elif (
            archive.expected_conversation_count is not None
            and len(index.rows) != archive.expected_conversation_count
        ):
            validation_error = ChatGPTWebSourceError(
                "conversation_count_mismatch",
                "expected conversation count differs from index",
            )
        elif (
            archive.complete_conversation_count is not None
            and len(index.rows) != archive.complete_conversation_count
        ):
            validation_error = ChatGPTWebSourceError(
                "conversation_count_mismatch",
                "complete conversation count differs from index",
            )
        elif (
            archive.validation_project_count is not None
            and len(projects) != archive.validation_project_count
        ):
            validation_error = ChatGPTWebSourceError(
                "archive_incomplete", "project metadata count differs from validation report"
            )
        membership_counts: Counter[str] = Counter()
        excluded_count = 0
        excluded_set = set(excluded)
        for row in index.rows:
            for membership in row.memberships:
                membership_counts[membership["scope"]] += 1
            if _excluded_project(row.memberships, excluded_set) is not None:
                excluded_count += 1
        result: dict[str, object] = {
            "source": CHATGPT_WEB_SOURCE,
            "root": str(archive.root),
            "archive_validated": validation_error is None,
            "provider": archive.provider,
            "schema_version": archive.schema_version,
            "normalizer_version": archive.normalizer_version,
            "workspace_fingerprint": archive.workspace_fingerprint,
            "expected_conversation_count": archive.expected_conversation_count,
            "complete_conversation_count": archive.complete_conversation_count,
            "validation_terminal_state": archive.validation_terminal_state,
            "partial_asset_reference_count": archive.partial_asset_reference_count,
            "validation_project_count": archive.validation_project_count,
            "index_rows": index.raw_rows,
            "conversations_discovered": len(index.rows),
            "index_failures": list(index.failures),
            "index_failure_count": len(index.failures),
            "index_validated": not index.failures,
            "index_failure_reasons": dict(sorted(index.failure_reasons.items())),
            "projects": projects,
            "membership_counts": dict(sorted(membership_counts.items())),
            "excluded_project_ids": list(excluded),
            "excluded_conversations": excluded_count,
            "warnings": warnings,
        }
        if validation_error is not None:
            result["error"] = validation_error.to_dict()
        return result
    except ChatGPTWebSourceError as exc:
        return {
            "source": CHATGPT_WEB_SOURCE,
            "root": str(root),
            "archive_validated": False,
            "index_validated": False,
            "conversations_discovered": 0,
            "error": exc.to_dict(),
        }


def _paths_overlap(first: Path, second: Path) -> bool:
    try:
        first.relative_to(second)
        return True
    except ValueError:
        try:
            second.relative_to(first)
            return True
        except ValueError:
            return False


def prepare_chatgpt_web_source(
    workspace_root: Path,
    *,
    root: str | Path,
    excluded_project_ids: Iterable[str] = (),
) -> ChatGPTWebRun:
    """Refresh the Pi-readable projection atomically.

    Validation errors are raised; a body/projection failure returns a rejected
    run and leaves the previous accepted projection untouched.
    """
    raw_archive_root = Path(root).expanduser()
    if raw_archive_root.is_symlink():
        raise ChatGPTWebSourceError(
            "archive_path_escape", "configured archive root must not be a symlink"
        )
    archive_root = raw_archive_root.resolve()
    workspace = workspace_root.expanduser().resolve()
    if _paths_overlap(archive_root, workspace):
        raise ChatGPTWebSourceError(
            "archive_workspace_overlap", "selected archive root overlaps the Syke workspace"
        )
    excluded = _normalize_excluded_project_ids(excluded_project_ids)
    sources_dir = workspace / "sources"
    if sources_dir.is_symlink() or (sources_dir.exists() and not sources_dir.is_dir()):
        raise ChatGPTWebSourceError(
            "workspace_path_invalid", "workspace sources path is not a real directory"
        )
    sources_dir.mkdir(parents=True, exist_ok=True)
    source_dir = sources_dir / CHATGPT_WEB_SOURCE
    if source_dir.is_symlink() or (source_dir.exists() and not source_dir.is_dir()):
        raise ChatGPTWebSourceError(
            "workspace_path_invalid", "ChatGPT Web source path is not a real directory"
        )
    source_dir.mkdir(parents=True, exist_ok=True)
    projection_path = source_dir / "projection.jsonl"
    report_path = source_dir / "run.json"
    temp_projection: Path | None = None
    temp_report: Path | None = None
    try:
        fd, name = tempfile.mkstemp(dir=source_dir, prefix=".projection-", suffix=".tmp")
        os.close(fd)
        temp_projection = Path(name)
        fd, name = tempfile.mkstemp(dir=source_dir, prefix=".run-", suffix=".tmp")
        os.close(fd)
        temp_report = Path(name)
        try:
            run = project_chatgpt_web_archive(
                archive_root,
                excluded,
                output_path=temp_projection,
            )
        except ChatGPTWebSourceError as exc:
            report = {
                "source": CHATGPT_WEB_SOURCE,
                "archive_validated": False,
                "accepted": False,
                "projection_path": str(projection_path.relative_to(workspace)),
                "error": exc.to_dict(),
            }
            temp_report.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            temp_report.replace(report_path)
            raise
        run.projection_path = str(projection_path.relative_to(workspace))
        if not run.accepted:
            temp_projection.unlink(missing_ok=True)
            run.warnings.append("projection was not accepted; previous projection retained")
        temp_report.write_text(
            json.dumps(run.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        # Stage the report before replacing the accepted projection so a
        # report-write failure cannot leave a new projection with stale run
        # diagnostics. The projection itself is always replaced by rename.
        if run.accepted:
            temp_projection.replace(projection_path)
        temp_report.replace(report_path)
        logger.info(
            "Prepared ChatGPT Web projection: discovered=%d excluded=%d "
            "eligible=%d processed=%d failed=%d",
            run.conversations_discovered,
            run.skip_reasons.get("excluded_project", 0),
            run.conversations_eligible,
            run.conversations_processed,
            run.conversations_failed,
        )
        return run
    finally:
        for path in (temp_projection, temp_report):
            if path is not None:
                path.unlink(missing_ok=True)


__all__ = [
    "CHATGPT_WEB_SOURCE",
    "ChatGPTWebRun",
    "ChatGPTWebSourceError",
    "EXPECTED_NORMALIZER_VERSION",
    "EXPECTED_PROVIDER",
    "EXPECTED_SCHEMA_VERSION",
    "MAX_CONVERSATION_BYTES",
    "MAX_CONVERSATION_CHARS",
    "MAX_PART_CHARS",
    "MAX_PROJECTION_BYTES",
    "inspect_chatgpt_web_archive",
    "prepare_chatgpt_web_source",
    "project_chatgpt_web_archive",
    "project_normalized_conversation",
]
