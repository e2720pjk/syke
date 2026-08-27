"""OpenCode legacy/v2 schema, discovery, and adapter bootstrap contracts."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from syke.observe import bootstrap
from syke.observe.catalog import get_source, iter_discovered_files

_MAX_RAW_JSON = 4096
_MAX_TEXT = 64
_PAGE_SIZE = 2
_ALLOWED_TABLES = {
    "session_v2",
    "session_message",
    "session",
    "message",
    "part",
    "project",
    "workspace",
}

_V2_SCHEMA = """
CREATE TABLE session_v2 (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    workspace_id TEXT,
    parent_id TEXT,
    fork_session_id TEXT,
    fork_boundary TEXT,
    slug TEXT,
    directory TEXT,
    path TEXT,
    title TEXT,
    version TEXT,
    share_url TEXT,
    summary_additions INTEGER,
    summary_deletions INTEGER,
    summary_files INTEGER,
    summary_diffs TEXT,
    metadata TEXT,
    cost REAL,
    tokens_input INTEGER,
    tokens_output INTEGER,
    tokens_reasoning INTEGER,
    tokens_cache_read INTEGER,
    tokens_cache_write INTEGER,
    revert TEXT,
    permission TEXT,
    agent TEXT,
    model TEXT,
    time_created INTEGER,
    time_updated INTEGER,
    time_compacting INTEGER,
    time_archived INTEGER,
    time_suspended INTEGER,
    resume_attempts INTEGER,
    time_idle INTEGER,
    time_viewed INTEGER,
    idle_outcome TEXT
);
CREATE TABLE session_message (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    type TEXT NOT NULL,
    seq INTEGER NOT NULL,
    time_created INTEGER,
    time_updated INTEGER,
    data TEXT
);
CREATE INDEX session_message_session_seq
    ON session_message (session_id, seq);
"""

_LEGACY_SCHEMA = """
CREATE TABLE session (
    id TEXT PRIMARY KEY,
    title TEXT,
    time_created INTEGER,
    time_updated INTEGER,
    agent TEXT,
    model TEXT
);
CREATE TABLE message (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    time_created INTEGER,
    time_updated INTEGER,
    data TEXT
);
CREATE TABLE part (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    time_created INTEGER,
    time_updated INTEGER,
    data TEXT
);
"""


def _new_connection(*, legacy: bool, v2: bool) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    if v2:
        conn.executescript(_V2_SCHEMA)
    if legacy:
        conn.executescript(_LEGACY_SCHEMA)
    return conn


def _make_opencode_fixture() -> sqlite3.Connection:
    """Build an anonymous both-schema DB with divergent duplicate messages."""
    conn = _new_connection(legacy=True, v2=True)

    def add_v2_session(
        session_id: str,
        title: str,
        created: int,
        updated: int,
        *,
        parent_id: str | None = None,
        fork_session_id: str | None = None,
        fork_boundary: str | None = None,
    ) -> None:
        conn.execute(
            "INSERT INTO session_v2 "
            "(id, parent_id, fork_session_id, fork_boundary, title, agent, model, "
            "time_created, time_updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                parent_id,
                fork_session_id,
                fork_boundary,
                title,
                "build-agent",
                "model-anon",
                created,
                updated,
            ),
        )

    add_v2_session("v2-root", "Synthetic root", 100, 700)
    add_v2_session(
        "v2-fork",
        "Synthetic fork",
        200,
        600,
        parent_id="v2-root",
        fork_session_id="v2-root",
        fork_boundary="v2-msg-1",
    )
    # Legacy recency is deliberately newer; v2 still owns duplicate metadata.
    add_v2_session("overlap", "V2 title", 300, 800)

    conn.executemany(
        "INSERT INTO session_message "
        "(id, session_id, type, seq, time_created, time_updated, data) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("v2-msg-0", "v2-root", "system", 0, 100, 100, '{"text":"system marker"}'),
            ("v2-msg-1", "v2-root", "user", 1, 110, 110, '{"text":"anonymous request"}'),
            (
                "v2-msg-2",
                "v2-root",
                "assistant",
                2,
                120,
                120,
                json.dumps(
                    {
                        "agent": "build-agent",
                        "model": "model-anon",
                        "content": [
                            {"type": "text", "text": "anonymous answer"},
                            {"type": "reasoning", "text": "opaque reasoning blob"},
                            {
                                "type": "tool",
                                "state": {
                                    "status": "completed",
                                    "input": {"path": "src/example.py"},
                                    "content": "x" * 200,
                                    "metadata": {"duration_ms": 2},
                                },
                            },
                            {"type": "unknown-future-item", "payload": "skip me"},
                        ],
                    }
                ),
            ),
            (
                "v2-msg-3",
                "v2-root",
                "assistant",
                3,
                130,
                130,
                json.dumps(
                    {
                        "content": [
                            {
                                "type": "tool",
                                "state": {
                                    "status": "error",
                                    "input": {"command": "false"},
                                    "content": "bounded failed output",
                                    "error": "anonymous failure",
                                },
                            }
                        ]
                    }
                ),
            ),
            (
                "v2-msg-4",
                "v2-root",
                "assistant",
                4,
                135,
                135,
                json.dumps(
                    {
                        "content": [
                            {
                                "type": "tool",
                                "state": {
                                    "status": "future-status",
                                    "content": "future output",
                                },
                            }
                        ]
                    }
                ),
            ),
            ("v2-msg-5", "v2-root", "compaction", 5, 140, 140, '{"description":"compact"}'),
            ("v2-msg-6", "v2-root", "synthetic", 6, 150, 150, '{"text":"synthetic"}'),
            ("v2-msg-7", "v2-root", "system", 7, 160, 160, "{malformed json"),
            ("v2-msg-8", "v2-root", "assistant", 8, 165, 165, "{malformed assistant json"),
            (
                "v2-msg-9",
                "v2-root",
                "assistant",
                9,
                170,
                170,
                json.dumps({"content": [{"type": "future-item"}]}),
            ),
            (
                "v2-msg-10",
                "v2-root",
                "model-switched",
                10,
                175,
                175,
                '{"model":"future-model"}',
            ),
            (
                "v2-fork-msg-1",
                "v2-fork",
                "user",
                1,
                210,
                210,
                '{"text":"fork request"}',
            ),
            (
                "shared-msg",
                "overlap",
                "assistant",
                1,
                200,
                200,
                json.dumps({"content": [{"type": "text", "text": "v2 shared payload"}]}),
            ),
            (
                "v2-overlap-later",
                "overlap",
                "assistant",
                2,
                400,
                400,
                json.dumps({"content": [{"type": "text", "text": "v2 later"}]}),
            ),
        ],
    )

    conn.executemany(
        "INSERT INTO session (id, title, time_created, time_updated, agent, model) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("legacy-only", "Legacy history", 10, 50, "legacy-agent", "legacy-model"),
            ("overlap", "Legacy title", 20, 900, "legacy-agent", "legacy-model"),
        ],
    )
    conn.executemany(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (
                "legacy-before",
                "overlap",
                100,
                100,
                '{"role":"user","text":"legacy before"}',
            ),
            (
                "shared-msg",
                "overlap",
                200,
                200,
                '{"role":"user","text":"legacy conflicting payload"}',
            ),
            (
                "legacy-msg",
                "overlap",
                250,
                250,
                '{"role":"assistant","text":"legacy fallback"}',
            ),
            (
                "legacy-after",
                "overlap",
                500,
                500,
                '{"role":"assistant","text":"legacy after"}',
            ),
        ],
    )
    conn.executemany(
        "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                "shared-shadow-part",
                "shared-msg",
                "overlap",
                201,
                201,
                json.dumps(
                    {
                        "type": "tool",
                        "state": {
                            "status": "completed",
                            "output": "legacy shadow output must not win",
                        },
                    }
                ),
            ),
            (
                "legacy-text-part",
                "legacy-msg",
                "overlap",
                251,
                251,
                json.dumps({"type": "text", "text": "legacy part text"}),
            ),
            (
                "legacy-tool-part",
                "legacy-msg",
                "overlap",
                252,
                252,
                json.dumps(
                    {
                        "type": "tool",
                        "state": {
                            "status": "completed",
                            "input": {"command": "printf safe"},
                            "output": "legacy tool output",
                        },
                    }
                ),
            ),
            (
                "legacy-reasoning-part",
                "legacy-msg",
                "overlap",
                253,
                253,
                json.dumps({"type": "reasoning", "text": "opaque legacy reasoning"}),
            ),
            (
                "legacy-compaction-part",
                "legacy-msg",
                "overlap",
                254,
                254,
                json.dumps({"type": "compaction", "auto": True}),
            ),
            (
                "legacy-step-finish-part",
                "legacy-msg",
                "overlap",
                255,
                255,
                json.dumps({"type": "step-finish", "reason": "stop"}),
            ),
            (
                "legacy-after-tool",
                "legacy-after",
                "overlap",
                501,
                501,
                json.dumps(
                    {
                        "type": "tool",
                        "state": {"status": "future-status", "output": "future legacy output"},
                    }
                ),
            ),
        ],
    )
    conn.commit()
    return conn


def _detect_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name IN "
        "('session_v2', 'session_message', 'session', 'message', 'part', 'project', 'workspace') "
        "ORDER BY name LIMIT ?",
        (len(_ALLOWED_TABLES),),
    )
    return {row[0] for row in rows}


def _read_metadata(conn: sqlite3.Connection) -> list[tuple[Any, ...]]:
    """Read metadata with a schema branch; mixed CTE is never run when absent."""
    tables = _detect_tables(conn)
    if {"session_v2", "session"} <= tables:
        rows = conn.execute(
            """
            WITH merged AS (
              SELECT id, title, time_created, time_updated, agent, model, 2 AS source_rank
              FROM session_v2
              UNION ALL
              SELECT id, title, time_created, time_updated, agent, model, 1 AS source_rank
              FROM session
            ), chosen AS (
              SELECT merged.*,
                     ROW_NUMBER() OVER (
                       PARTITION BY id
                       ORDER BY source_rank DESC, time_updated DESC,
                                time_created DESC, id ASC
                     ) AS row_number
              FROM merged
            ), recency AS (
              SELECT id, MAX(COALESCE(time_updated, time_created)) AS latest_time
              FROM merged
              GROUP BY id
            )
            SELECT chosen.id, chosen.title, chosen.time_created,
                   recency.latest_time, chosen.agent, chosen.model
            FROM chosen
            JOIN recency ON recency.id = chosen.id
            WHERE chosen.row_number = 1
            ORDER BY recency.latest_time DESC, chosen.id ASC
            LIMIT ?
            """,
            (100,),
        ).fetchall()
        return rows
    if "session_v2" in tables:
        return conn.execute(
            "SELECT id, title, time_created, time_updated, agent, model "
            "FROM session_v2 ORDER BY time_updated DESC, id ASC LIMIT ?",
            (100,),
        ).fetchall()
    if "session" in tables:
        return conn.execute(
            "SELECT id, title, time_created, time_updated, agent, model "
            "FROM session ORDER BY time_updated DESC, id ASC LIMIT ?",
            (100,),
        ).fetchall()
    return []


def _bounded_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if len(value) <= _MAX_TEXT:
        return value
    return value[:_MAX_TEXT] + "..."


def _parse_json(raw_data: Any) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw_data)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _read_v2_rows(
    conn: sqlite3.Connection, session_id: str, *, page_size: int = _PAGE_SIZE
) -> list[tuple[Any, ...]]:
    if "session_message" not in _detect_tables(conn):
        return []
    rows: list[tuple[Any, ...]] = []
    offset = 0
    while True:
        page = conn.execute(
            "SELECT id, seq, type, time_created, time_updated, substr(data, 1, ?) "
            "FROM session_message WHERE session_id = ? "
            "ORDER BY seq ASC, time_created ASC, id ASC LIMIT ? OFFSET ?",
            (_MAX_RAW_JSON, session_id, page_size, offset),
        ).fetchall()
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += len(page)


def _read_legacy_rows(
    conn: sqlite3.Connection, session_id: str, *, page_size: int = _PAGE_SIZE
) -> list[tuple[Any, ...]]:
    if "message" not in _detect_tables(conn):
        return []
    rows: list[tuple[Any, ...]] = []
    offset = 0
    while True:
        page = conn.execute(
            "SELECT id, time_created, time_updated, substr(data, 1, ?) "
            "FROM message WHERE session_id = ? "
            "ORDER BY time_created ASC, id ASC LIMIT ? OFFSET ?",
            (_MAX_RAW_JSON, session_id, page_size, offset),
        ).fetchall()
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += len(page)


def _read_legacy_parts(
    conn: sqlite3.Connection,
    session_id: str,
    message_ids: list[str],
    *,
    page_size: int = _PAGE_SIZE,
) -> list[tuple[Any, ...]]:
    if "part" not in _detect_tables(conn) or not message_ids:
        return []
    placeholders = ", ".join("?" for _ in message_ids)
    rows: list[tuple[Any, ...]] = []
    offset = 0
    while True:
        page = conn.execute(
            "SELECT id, message_id, time_created, time_updated, substr(data, 1, ?) "
            f"FROM part WHERE session_id = ? AND message_id IN ({placeholders}) "
            "ORDER BY message_id ASC, time_created ASC, id ASC LIMIT ? OFFSET ?",
            (_MAX_RAW_JSON, session_id, *message_ids, page_size, offset),
        ).fetchall()
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += len(page)


def _parse_v2_row(row: tuple[Any, ...]) -> dict[str, Any] | None:
    message_id, seq, message_type, time_created, _time_updated, raw_data = row
    if message_type not in {"user", "assistant"}:
        return None
    data = _parse_json(raw_data)
    if data is None:
        return None
    if message_type == "user":
        text = _bounded_text(data.get("text"))
        if text is None:
            return None
        content = [{"type": "text", "text": text}]
    else:
        content = []
        raw_content = data.get("content")
        if not isinstance(raw_content, list):
            return None
        for item in raw_content:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "text":
                text = _bounded_text(item.get("text"))
                if text is not None:
                    content.append({"type": "text", "text": text})
            elif item_type == "reasoning":
                content.append({"type": "reasoning"})
            elif item_type == "tool":
                state = item.get("state")
                if not isinstance(state, dict):
                    continue
                status = state.get("status")
                if not isinstance(status, str):
                    status = "unknown"
                content.append(
                    {
                        "type": "tool",
                        "status": _bounded_text(status),
                        "content": _bounded_text(state.get("content")),
                        "error": _bounded_text(state.get("error")),
                    }
                )
        if not content:
            return None
    event_time = time_created if isinstance(time_created, int) else 0
    return {
        "id": message_id,
        "source": "v2",
        "seq": seq,
        "time_created": time_created,
        "event_time": event_time,
        "role": message_type,
        "content": content,
        "merge_key": (event_time, 0, seq, event_time, message_id),
    }


def _parse_legacy_row(
    row: tuple[Any, ...], parts_by_message: dict[str, list[tuple[Any, ...]]]
) -> dict[str, Any] | None:
    message_id, time_created, _time_updated, raw_data = row
    data = _parse_json(raw_data)
    if data is None:
        return None
    role = data.get("role")
    if role not in {"user", "assistant"}:
        return None
    content: list[dict[str, Any]] = []
    for _part_id, _message_id, _part_time, _part_updated, raw_part in parts_by_message.get(
        message_id, []
    ):
        part = _parse_json(raw_part)
        if part is None:
            continue
        part_type = part.get("type")
        if part_type == "text":
            text = _bounded_text(part.get("text"))
            if text is not None:
                content.append({"type": "text", "text": text})
        elif part_type == "reasoning":
            content.append({"type": "reasoning"})
        elif part_type == "tool":
            state = part.get("state")
            if not isinstance(state, dict):
                continue
            status = state.get("status")
            if not isinstance(status, str):
                status = "unknown"
            content.append(
                {
                    "type": "tool",
                    "status": _bounded_text(status),
                    "content": _bounded_text(state.get("output")),
                    "error": _bounded_text(state.get("error")),
                }
            )
    if not content:
        fallback = _bounded_text(data.get("text"))
        if fallback is None:
            return None
        content.append({"type": "text", "text": fallback})
    event_time = time_created if isinstance(time_created, int) else 0
    return {
        "id": message_id,
        "source": "legacy",
        "seq": None,
        "time_created": time_created,
        "event_time": event_time,
        "role": role,
        "content": content,
        "merge_key": (event_time, 1, 0, event_time, message_id),
    }


def _merge_messages(
    conn: sqlite3.Connection, session_id: str, *, page_size: int = _PAGE_SIZE
) -> list[dict[str, Any]]:
    """Model the adapter's separate reads and stable-ID keyed merge."""
    tables = _detect_tables(conn)
    v2_by_id: dict[str, dict[str, Any]] = {}
    v2_ids: set[str] = set()
    if "session_message" in tables:
        for raw_row in _read_v2_rows(conn, session_id, page_size=page_size):
            v2_ids.add(raw_row[0])
            parsed = _parse_v2_row(raw_row)
            if parsed is not None:
                v2_by_id.setdefault(parsed["id"], parsed)

    legacy_rows = _read_legacy_rows(conn, session_id, page_size=page_size)
    legacy_only_rows = [row for row in legacy_rows if row[0] not in v2_ids]
    legacy_ids = [row[0] for row in legacy_only_rows]
    parts_by_message: dict[str, list[tuple[Any, ...]]] = {}
    for part_row in _read_legacy_parts(conn, session_id, legacy_ids, page_size=page_size):
        parts_by_message.setdefault(part_row[1], []).append(part_row)

    legacy_by_id: dict[str, dict[str, Any]] = {}
    for raw_row in legacy_only_rows:
        parsed = _parse_legacy_row(raw_row, parts_by_message)
        if parsed is not None:
            legacy_by_id.setdefault(parsed["id"], parsed)

    merged = list(v2_by_id.values()) + list(legacy_by_id.values())
    return sorted(merged, key=lambda row: row["merge_key"])


def _make_single_schema_fixture(kind: str) -> sqlite3.Connection:
    conn = _new_connection(legacy=kind == "legacy", v2=kind == "v2")
    if kind == "legacy":
        conn.execute(
            "INSERT INTO session (id, title, time_created, time_updated, agent, model) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("legacy-only", "Legacy only", 1, 2, "legacy-agent", "legacy-model"),
        )
        conn.execute(
            "INSERT INTO message (id, session_id, time_created, time_updated, data) "
            "VALUES (?, ?, ?, ?, ?)",
            ("legacy-message", "legacy-only", 3, 3, '{"role":"user","text":"legacy"}'),
        )
    else:
        conn.execute(
            "INSERT INTO session_v2 (id, title, time_created, time_updated, agent, model) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("v2-only", "V2 only", 1, 2, "v2-agent", "v2-model"),
        )
        conn.execute(
            "INSERT INTO session_message "
            "(id, session_id, type, seq, time_created, time_updated, data) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("v2-message", "v2-only", "user", 1, 3, 3, '{"text":"v2"}'),
        )
    conn.commit()
    return conn


def test_schema_detection_branches_for_pure_legacy_pure_v2_and_both() -> None:
    fixtures = {
        "legacy": _make_single_schema_fixture("legacy"),
        "v2": _make_single_schema_fixture("v2"),
        "both": _make_opencode_fixture(),
    }
    try:
        assert _detect_tables(fixtures["legacy"]) == {"session", "message", "part"}
        assert _detect_tables(fixtures["v2"]) == {"session_v2", "session_message"}
        assert {"session", "message", "part", "session_v2", "session_message"} <= _detect_tables(
            fixtures["both"]
        )

        legacy_metadata = _read_metadata(fixtures["legacy"])
        v2_metadata = _read_metadata(fixtures["v2"])
        both_metadata = _read_metadata(fixtures["both"])
        assert legacy_metadata[0][0] == "legacy-only"
        assert v2_metadata[0][0] == "v2-only"
        assert _merge_messages(fixtures["legacy"], "legacy-only")[0]["role"] == "user"
        assert _merge_messages(fixtures["v2"], "v2-only")[0]["role"] == "user"
        assert {row[0] for row in both_metadata} == {
            "legacy-only",
            "overlap",
            "v2-root",
            "v2-fork",
        }
    finally:
        for conn in fixtures.values():
            conn.close()


def test_v2_fixture_reconstructs_root_and_fork_without_sensitive_content() -> None:
    conn = _make_opencode_fixture()
    try:
        root = conn.execute(
            "SELECT parent_id, fork_session_id, fork_boundary FROM session_v2 WHERE id = ? LIMIT 1",
            ("v2-root",),
        ).fetchone()
        fork = conn.execute(
            "SELECT parent_id, fork_session_id, fork_boundary FROM session_v2 WHERE id = ? LIMIT 1",
            ("v2-fork",),
        ).fetchone()
        assert root == (None, None, None)
        assert fork == ("v2-root", "v2-root", "v2-msg-1")

        turns = _merge_messages(conn, "v2-root")
        assert [(turn["seq"], turn["role"]) for turn in turns] == [
            (1, "user"),
            (2, "assistant"),
            (3, "assistant"),
            (4, "assistant"),
        ]
        assistant_items = turns[1]["content"]
        assert assistant_items[0] == {"type": "text", "text": "anonymous answer"}
        assert assistant_items[1] == {"type": "reasoning"}
        assert assistant_items[2]["status"] == "completed"
        assert len(assistant_items[2]["content"]) <= _MAX_TEXT + 3
        assert turns[2]["content"][0]["status"] == "error"
        assert turns[2]["content"][0]["error"] == "anonymous failure"
        assert turns[3]["content"][0]["status"] == "future-status"
        assert _merge_messages(conn, "v2-fork") == [
            {
                "id": "v2-fork-msg-1",
                "source": "v2",
                "seq": 1,
                "time_created": 210,
                "event_time": 210,
                "role": "user",
                "content": [{"type": "text", "text": "fork request"}],
                "merge_key": (210, 0, 1, 210, "v2-fork-msg-1"),
            }
        ]
    finally:
        conn.close()


def test_message_level_keyed_merge_v2_wins_divergent_id_and_adds_legacy_only() -> None:
    conn = _make_opencode_fixture()
    try:
        messages = _merge_messages(conn, "overlap", page_size=1)
        assert [message["id"] for message in messages] == [
            "legacy-before",
            "shared-msg",
            "legacy-msg",
            "v2-overlap-later",
            "legacy-after",
        ]
        shared = next(message for message in messages if message["id"] == "shared-msg")
        assert shared["source"] == "v2"
        assert shared["role"] == "assistant"
        assert shared["content"] == [{"type": "text", "text": "v2 shared payload"}]
        rendered = json.dumps(messages)
        assert "legacy conflicting payload" not in rendered
        assert "legacy shadow output must not win" not in rendered
        assert [message["merge_key"] for message in messages] == sorted(
            message["merge_key"] for message in messages
        )

        v2_rows = _read_v2_rows(conn, "overlap", page_size=1)
        assert [(row[1], row[0]) for row in v2_rows] == [
            (1, "shared-msg"),
            (2, "v2-overlap-later"),
        ]
        legacy_rows = _read_legacy_rows(conn, "overlap", page_size=1)
        assert [(row[1], row[0]) for row in legacy_rows] == [
            (100, "legacy-before"),
            (200, "shared-msg"),
            (250, "legacy-msg"),
            (500, "legacy-after"),
        ]
    finally:
        conn.close()


def test_v2_id_owns_duplicate_even_when_payload_is_malformed() -> None:
    conn = _new_connection(legacy=True, v2=True)
    try:
        conn.execute(
            "INSERT INTO session_v2 (id, title, time_created, time_updated) VALUES (?, ?, ?, ?)",
            ("same-session", "V2", 1, 1),
        )
        conn.execute(
            "INSERT INTO session (id, title, time_created, time_updated) VALUES (?, ?, ?, ?)",
            ("same-session", "legacy", 1, 1),
        )
        conn.execute(
            "INSERT INTO session_message "
            "(id, session_id, type, seq, time_created, time_updated, data) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("same-id", "same-session", "assistant", 1, 1, 1, "{malformed"),
        )
        conn.execute(
            "INSERT INTO message (id, session_id, time_created, time_updated, data) "
            "VALUES (?, ?, ?, ?, ?)",
            ("same-id", "same-session", 1, 1, '{"role":"user","text":"legacy"}'),
        )
        conn.commit()
        assert _merge_messages(conn, "same-session") == []
    finally:
        conn.close()


def test_legacy_text_parts_tools_and_filtered_parts_reconstruct() -> None:
    conn = _make_opencode_fixture()
    try:
        messages = _merge_messages(conn, "overlap", page_size=2)
        legacy = next(message for message in messages if message["id"] == "legacy-msg")
        assert legacy["source"] == "legacy"
        assert legacy["role"] == "assistant"
        assert legacy["content"] == [
            {"type": "text", "text": "legacy part text"},
            {"type": "tool", "status": "completed", "content": "legacy tool output", "error": None},
            {"type": "reasoning"},
        ]
        after = next(message for message in messages if message["id"] == "legacy-after")
        assert after["content"] == [
            {
                "type": "tool",
                "status": "future-status",
                "content": "future legacy output",
                "error": None,
            },
        ]
        assert all(
            item.get("type") not in {"compaction", "step-finish"} for item in legacy["content"]
        )
    finally:
        conn.close()


def test_legacy_v2_session_dedup_prefers_v2_and_keeps_union_recency() -> None:
    conn = _make_opencode_fixture()
    try:
        rows = _read_metadata(conn)
        by_id = {row[0]: row for row in rows}
        assert len(by_id) == len(rows)
        assert by_id["overlap"] == ("overlap", "V2 title", 300, 900, "build-agent", "model-anon")
        assert "legacy-only" in by_id
        assert "v2-root" in by_id
        assert "v2-fork" in by_id
    finally:
        conn.close()


def test_adapter_documents_statuses_detection_merge_and_bounds() -> None:
    seed_path = bootstrap.get_seed_adapter_md_path("opencode")
    assert seed_path is not None
    text = seed_path.read_text(encoding="utf-8")
    assert "**`completed`** or **`error`**" in text
    assert "Detect the schema before querying" in text
    assert "stable `message.id`" in text
    assert "substr(data, 1, ?)" in text
    assert "LIMIT ? OFFSET ?" in text
    assert "`JOIN` or `UNION ALL` legacy chat rows with v2 chat rows" in text
    assert "state.content" in text
    assert "state.output" in text


def test_bootstrap_upgrades_known_seed_but_preserves_custom_adapter(
    tmp_path: Path, monkeypatch
) -> None:
    old_seed = "# old managed seed\n"
    monkeypatch.setitem(
        bootstrap.KNOWN_SEED_HASHES, "opencode", (bootstrap._sha256_text(old_seed),)
    )

    old_target = tmp_path / "old" / "adapters" / "opencode.md"
    old_target.parent.mkdir(parents=True)
    old_target.write_text(old_seed, encoding="utf-8")
    upgraded = bootstrap.ensure_adapters(tmp_path / "old", selected_sources=("opencode",))
    assert upgraded[0].status == "upgraded"
    seed_path = bootstrap.get_seed_adapter_md_path("opencode")
    assert seed_path is not None
    assert old_target.read_text(encoding="utf-8") == seed_path.read_text(encoding="utf-8")

    custom_root = tmp_path / "custom"
    custom_target = custom_root / "adapters" / "opencode.md"
    custom_target.parent.mkdir(parents=True)
    custom_target.write_text("# user customization\n", encoding="utf-8")
    custom = bootstrap.ensure_adapters(custom_root, selected_sources=("opencode",))
    assert custom[0].status == "customized"
    assert "not overwritten" in custom[0].detail
    assert "delete" in custom[0].detail
    assert custom_target.read_text(encoding="utf-8") == "# user customization\n"
    hints = bootstrap.customized_adapter_hints(custom_root, selected_sources=("opencode",))
    assert [(hint.source, hint.status) for hint in hints] == [("opencode", "customized")]


def test_opencode_discovery_excludes_wal_shm_and_non_db_files(tmp_path: Path) -> None:
    root = tmp_path / ".local" / "share" / "opencode"
    root.mkdir(parents=True)
    for filename in (
        "opencode.db",
        "opencode-channel.db",
        "opencode.db-wal",
        "opencode.db-shm",
        "opencode.sqlite",
        "other.db",
    ):
        (root / filename).write_bytes(b"synthetic fixture")

    spec = get_source("opencode")
    assert spec is not None
    found = iter_discovered_files(spec, home=tmp_path)
    assert [path.name for path in found] == ["opencode-channel.db", "opencode.db"]
