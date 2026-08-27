"""OpenCode legacy/v2 schema, discovery, and adapter bootstrap contracts."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from syke.observe import bootstrap
from syke.observe.catalog import get_source, iter_discovered_files

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


def _make_opencode_fixture() -> sqlite3.Connection:
    """Build a fully synthetic, anonymous legacy+v2 OpenCode database."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(_V2_SCHEMA)

    def add_v2(
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

    add_v2("v2-root", "Synthetic root", 100, 700)
    add_v2(
        "v2-fork",
        "Synthetic fork",
        200,
        600,
        parent_id="v2-root",
        fork_session_id="v2-root",
        fork_boundary="v2-msg-1",
    )
    # Deliberately make legacy recency newer to exercise the union recency rule;
    # v2 still owns the selected metadata for this overlapping ID.
    add_v2("overlap", "V2 title", 300, 800)

    conn.executemany(
        "INSERT INTO session_message (id, session_id, type, seq, time_created, time_updated, data) "
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
                                    "status": "success",
                                    "input": {"path": "src/example.py"},
                                    "content": "bounded success output",
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
            ("v2-msg-4", "v2-root", "compaction", 4, 140, 140, '{"description":"compact"}'),
            ("v2-msg-5", "v2-root", "synthetic", 5, 150, 150, '{"text":"synthetic"}'),
            ("v2-msg-6", "v2-root", "system", 6, 160, 160, '{malformed json'),
            ("v2-msg-7", "v2-root", "assistant", 7, 165, 165, '{malformed assistant json'),
            (
                "v2-msg-8",
                "v2-root",
                "assistant",
                8,
                170,
                170,
                json.dumps({"content": [{"type": "future-item"}]}),
            ),
            (
                "v2-msg-9",
                "v2-root",
                "model-switched",
                9,
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
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) "
        "VALUES (?, ?, ?, ?, ?)",
        ("legacy-msg", "overlap", 25, 25, '{"role":"user","text":"old history"}'),
    )
    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "legacy-part",
            "legacy-msg",
            "overlap",
            25,
            25,
            json.dumps(
                {
                    "type": "tool",
                    "state": {"status": "completed", "input": {}, "output": "legacy output"},
                }
            ),
        ),
    )
    conn.commit()
    return conn


def _reconstruct_v2_turns(conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    """Small test-side reconstruction of the markdown adapter contract."""
    turns: list[dict[str, Any]] = []
    rows = conn.execute(
        "SELECT seq, type, data FROM session_message "
        "WHERE session_id = ? ORDER BY seq ASC, time_created ASC, id ASC LIMIT ?",
        (session_id, 100),
    )
    for seq, message_type, raw_data in rows:
        if message_type not in {"user", "assistant"}:
            continue
        try:
            data = json.loads(raw_data)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        if message_type == "user":
            text = data.get("text")
            if isinstance(text, str):
                turns.append({"seq": seq, "role": "user", "text": text})
            continue
        content = data.get("content")
        if not isinstance(content, list):
            continue
        parsed: list[dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "text" and isinstance(item.get("text"), str):
                parsed.append({"type": "text", "text": item["text"]})
            elif item_type == "reasoning":
                parsed.append({"type": "reasoning"})
            elif item_type == "tool":
                state = item.get("state")
                if not isinstance(state, dict):
                    continue
                parsed.append(
                    {
                        "type": "tool",
                        "status": state.get("status"),
                        "content": state.get("content"),
                        "error": state.get("error"),
                    }
                )
        if parsed:
            turns.append({"seq": seq, "role": "assistant", "content": parsed})
    return turns


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

        turns = _reconstruct_v2_turns(conn, "v2-root")
        assert [(turn["seq"], turn["role"]) for turn in turns] == [
            (1, "user"),
            (2, "assistant"),
            (3, "assistant"),
        ]
        assistant_items = turns[1]["content"]
        assert assistant_items[0] == {"type": "text", "text": "anonymous answer"}
        assert assistant_items[1] == {"type": "reasoning"}
        assert assistant_items[2]["status"] == "success"
        assert assistant_items[2]["content"] == "bounded success output"
        assert turns[2]["content"][0]["status"] == "error"
        assert turns[2]["content"][0]["error"] == "anonymous failure"
        legacy_tool = json.loads(
            conn.execute(
                "SELECT data FROM part WHERE session_id = ? ORDER BY time_created, id LIMIT ?",
                ("overlap", 10),
            ).fetchone()[0]
        )
        assert legacy_tool["state"]["output"] == "legacy output"
        assert "content" not in legacy_tool["state"]
        assert _reconstruct_v2_turns(conn, "v2-fork") == [
            {"seq": 1, "role": "user", "text": "fork request"}
        ]
    finally:
        conn.close()


def test_legacy_v2_session_dedup_prefers_v2_and_keeps_union_recency() -> None:
    conn = _make_opencode_fixture()
    try:
        rows = conn.execute(
            """
            WITH merged AS (
              SELECT id, title, time_created, time_updated, agent, model, 2 AS src
              FROM session_v2
              UNION ALL
              SELECT id, title, time_created, time_updated, agent, model, 1 AS src
              FROM session
            ),
            chosen AS (
              SELECT *, ROW_NUMBER() OVER (
                PARTITION BY id ORDER BY src DESC, time_updated DESC
              ) AS rn
              FROM merged
            ),
            recency AS (
              SELECT id, MAX(time_updated) AS latest_time_updated
              FROM merged GROUP BY id
            )
            SELECT c.id, c.title, recency.latest_time_updated
            FROM chosen AS c JOIN recency ON recency.id = c.id
            WHERE c.rn = 1
            ORDER BY recency.latest_time_updated DESC, c.id ASC
            LIMIT ?
            """,
            (20,),
        ).fetchall()
        by_id = {row[0]: row for row in rows}
        assert len(by_id) == len(rows)
        assert by_id["overlap"] == ("overlap", "V2 title", 900)
        assert "legacy-only" in by_id
        assert "v2-root" in by_id
        assert "v2-fork" in by_id
    finally:
        conn.close()


def test_bootstrap_upgrades_known_seed_but_preserves_custom_adapter(tmp_path: Path, monkeypatch) -> None:
    old_seed = "# old managed seed\n"
    monkeypatch.setitem(bootstrap.KNOWN_SEED_HASHES, "opencode", (bootstrap._sha256_text(old_seed),))

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
