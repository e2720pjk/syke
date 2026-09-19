from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from syke.observe.catalog import active_sources, get_source, is_source_selected
from syke.observe.chatgpt_web import (
    ChatGPTWebSourceError,
    inspect_chatgpt_web_archive,
    prepare_chatgpt_web_source,
    project_chatgpt_web_archive,
    project_normalized_conversation,
)


def _body(
    conversation_id: str,
    *,
    fingerprint: str = "fixture-fingerprint",
    latest_text: str = "current answer",
) -> dict[str, object]:
    root = "node-root"
    user = "node-user"
    alternate = "node-alternate"
    current = "node-current"
    tool = "node-tool"
    return {
        "provider": "chatgpt-web",
        "schemaVersion": 1,
        "normalizerVersion": "chatgpt-web-v1",
        "workspaceFingerprint": fingerprint,
        "conversationId": conversation_id,
        "logicalKey": f"{fingerprint}/{conversation_id}",
        "title": "Fixture conversation",
        "currentNodeId": current,
        "rootNodeIds": [root],
        "nodes": [
            {"id": root, "parentId": None, "childIds": [user], "messageId": None},
            {
                "id": user,
                "parentId": root,
                "childIds": [alternate, current],
                "messageId": user,
            },
            {"id": alternate, "parentId": user, "childIds": [], "messageId": alternate},
            {"id": current, "parentId": user, "childIds": [tool], "messageId": current},
            {"id": tool, "parentId": current, "childIds": [], "messageId": tool},
        ],
        "messages": [
            {
                "id": user,
                "nodeId": user,
                "author": {"role": "user"},
                "parts": [{"kind": "text", "text": "older requirement"}],
            },
            {
                "id": alternate,
                "nodeId": alternate,
                "author": {"role": "assistant"},
                "parts": [{"kind": "text", "text": "alternate branch secret"}],
            },
            {
                "id": current,
                "nodeId": current,
                "role": "assistant",
                "parts": [
                    {"kind": "text", "text": latest_text},
                    {"kind": "code", "text": "print('useful')", "raw": "raw secret"},
                    {"kind": "asset", "assetId": "asset-1", "assetPath": "/private/asset"},
                ],
            },
            {
                "id": tool,
                "nodeId": tool,
                "role": "tool",
                "parts": [{"kind": "text", "text": "tool output secret"}],
                "extensions": {"secret": "extension secret"},
            },
        ],
        "memberships": [{"scope": "main"}],
        "extensions": {"raw_secret": "must not appear"},
    }


def _row(
    conversation_id: str,
    *,
    memberships: list[dict[str, str]],
    create_time: object | None = None,
    update_time: object | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "logicalKey": f"fixture-fingerprint/{conversation_id}",
        "conversationId": conversation_id,
        "title": "Fixture conversation",
        "memberships": memberships,
        "normalizedPath": f"conversations/{conversation_id}/conversation.json",
        "normalizedHash": f"hash-{conversation_id}",
    }
    if create_time is not None:
        row["createTime"] = create_time
    if update_time is not None:
        row["updateTime"] = update_time
    return row


def _make_archive(
    tmp_path: Path,
    rows: list[dict[str, object]],
    *,
    bodies: dict[str, dict[str, object]] | None = None,
) -> Path:
    root = tmp_path / "export"
    (root / "indexes").mkdir(parents=True)
    (root / "conversations").mkdir()
    (root / "archive.json").write_text(
        json.dumps(
            {
                "provider": "chatgpt-web",
                "normalizerVersion": "chatgpt-web-v1",
                "schemaVersion": 1,
                "workspaceFingerprint": "fixture-fingerprint",
            }
        ),
        encoding="utf-8",
    )
    (root / "indexes" / "conversations.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    for conversation_id, body in (bodies or {}).items():
        body_dir = root / "conversations" / conversation_id
        body_dir.mkdir(parents=True)
        (body_dir / "conversation.json").write_text(json.dumps(body), encoding="utf-8")
    return root


def test_chatgpt_web_is_configured_but_not_selected_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SYKE_CHATGPT_WEB_ROOT", str(tmp_path))
    spec = get_source("chatgpt-web")

    assert spec is not None
    assert spec.explicit_only is True
    assert not is_source_selected(spec, None)
    assert is_source_selected(spec, ("chatgpt-web",))
    assert any(item.source == "chatgpt-web" for item in active_sources())


def test_chatgpt_web_is_not_registered_without_a_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYKE_CHATGPT_WEB_ROOT", raising=False)
    assert get_source("chatgpt-web") is None
    assert all(item.source != "chatgpt-web" for item in active_sources())


def test_setup_defaults_exclude_explicit_only_sources() -> None:
    from syke.cli_commands.setup import _select_agent_sources

    inspect_info = {
        "sources": [
            {"source": "chatgpt-web", "detected": True, "explicit_only": True},
            {"source": "codex", "detected": True},
        ]
    }

    selected, _, unknown = _select_agent_sources(inspect_info, ())
    assert selected == ["codex"]
    assert unknown == []

    selected, _, unknown = _select_agent_sources(inspect_info, ("chatgpt-web",))
    assert selected == ["chatgpt-web"]
    assert unknown == []

    inspect_info["selected_sources"] = ["chatgpt-web"]
    selected, _, unknown = _select_agent_sources(inspect_info, ())
    assert selected == ["chatgpt-web", "codex"]
    assert unknown == []


def test_interactive_setup_default_excludes_explicit_only_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syke.cli_support.setup_support import choose_setup_sources_interactive

    captured: dict[str, object] = {}

    def select_many(entries, *, title, default_indices):
        captured["default_indices"] = default_indices
        return default_indices

    monkeypatch.setattr(
        "syke.cli_support.auth_flow.term_menu_select_many",
        select_many,
    )
    sources = [
        {"source": "chatgpt-web", "detected": True, "explicit_only": True, "files_found": 1},
        {"source": "codex", "detected": True, "files_found": 1},
    ]

    assert choose_setup_sources_interactive(sources) == ["codex"]
    assert captured["default_indices"] == [1]


def test_environment_overrides_are_exact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import syke.config as config

    monkeypatch.setenv("SYKE_CHATGPT_WEB_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "SYKE_CHATGPT_WEB_EXCLUDED_PROJECT_IDS",
        " project-a,project-b,project-a ",
    )

    assert config.chatgpt_web_source_root() == tmp_path.resolve()
    assert config.chatgpt_web_excluded_project_ids() == ("project-a", "project-b")


def test_registry_reports_a_missing_archive_as_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing_archive = tmp_path / "missing"
    missing_archive.mkdir()
    monkeypatch.setenv("SYKE_CHATGPT_WEB_ROOT", str(missing_archive))

    from syke.observe.registry import HarnessRegistry

    health = HarnessRegistry().check_health("chatgpt-web")

    assert health.status == "invalid"
    assert health.files_found == 0
    assert health.details["error_reason"] == "archive_manifest_missing"


def test_registry_reports_a_valid_configured_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_archive(tmp_path, [])
    monkeypatch.setenv("SYKE_CHATGPT_WEB_ROOT", str(root))

    from syke.observe.registry import HarnessRegistry

    health = HarnessRegistry().check_health("chatgpt-web")

    assert health.status == "healthy"
    assert health.files_found == 2
    assert health.details["conversations_discovered"] == 0


@pytest.mark.parametrize(
    ("terminal_state", "partial_assets", "findings"),
    [
        ("complete", 0, []),
        (
            "conversations_complete_assets_partial",
            1,
            [{"severity": "error", "code": "ASSET_HASH_MISMATCH"}],
        ),
    ],
)
def test_setup_detects_conversation_complete_exporter_archives_with_retained_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal_state: str,
    partial_assets: int,
    findings: list[dict[str, str]],
) -> None:
    current = _row("current", memberships=[{"scope": "main"}])
    retained = _row("retained", memberships=[{"scope": "main"}])
    retained["absentFromCurrentInventory"] = True
    root = _make_archive(
        tmp_path,
        [current, retained],
        bodies={"current": _body("current"), "retained": _body("retained")},
    )
    index_path = root / "indexes" / "conversations.jsonl"
    index_hash = hashlib.sha256(index_path.read_bytes()).hexdigest()
    manifest_path = root / "archive.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["currentIndexHashes"] = {"conversations": index_hash}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (root / "reports").mkdir()
    (root / "reports" / "validation.json").write_text(
        json.dumps(
            {
                "provider": "chatgpt-web",
                "schemaVersion": 1,
                "workspaceFingerprint": "fixture-fingerprint",
                "terminalState": terminal_state,
                "expectedConversationCount": 1,
                "completeConversationCount": 1,
                "extraRetainedConversationCount": 1,
                "partialAssetReferenceCount": partial_assets,
                "projectCount": 0,
                "conversationsIndexHash": index_hash,
                "findings": findings,
            }
        ),
        encoding="utf-8",
    )
    (root / "inventory.json").write_text(
        json.dumps(
            {
                "provider": "chatgpt-web",
                "schemaVersion": 1,
                "workspaceFingerprint": "fixture-fingerprint",
                "complete": True,
                "chains": [{"complete": True}],
                "conversations": [{"conversationId": "current"}],
                "absentConversations": [
                    {"conversationId": "retained"},
                    {"conversationId": "older-unretained"},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SYKE_CHATGPT_WEB_ROOT", str(root))

    from syke.cli_support.setup_support import setup_source_inventory

    source = next(
        item for item in setup_source_inventory("test") if item["source"] == "chatgpt-web"
    )
    assert source["detected"] is True
    assert source["selectable"] is True
    assert source["files_found"] == 2
    archive = source["archive"]
    assert isinstance(archive, dict)
    assert archive["expected_conversation_count"] == 1
    assert archive["complete_conversation_count"] == 1
    assert archive["extra_retained_conversation_count"] == 1
    assert archive["conversations_discovered"] == 2

    run = project_chatgpt_web_archive(root, output_path=tmp_path / "projection.jsonl")
    assert run.accepted is True
    assert run.conversations_processed == 2


@pytest.mark.parametrize(
    ("terminal_state", "findings", "reason"),
    [
        ("incomplete", [], "archive_incomplete"),
        (
            "complete",
            [{"severity": "error", "code": "CONVERSATION_GRAPH_MISMATCH"}],
            "archive_validation_findings",
        ),
    ],
)
def test_inspect_rejects_incomplete_or_inconsistent_exporter_validation(
    tmp_path: Path,
    terminal_state: str,
    findings: list[dict[str, str]],
    reason: str,
) -> None:
    root = _make_archive(
        tmp_path,
        [_row("one", memberships=[{"scope": "main"}])],
    )
    (root / "reports").mkdir()
    (root / "reports" / "validation.json").write_text(
        json.dumps(
            {
                "provider": "chatgpt-web",
                "schemaVersion": 1,
                "workspaceFingerprint": "fixture-fingerprint",
                "findings": findings,
                "terminalState": terminal_state,
                "expectedConversationCount": 1,
                "completeConversationCount": 1,
            }
        ),
        encoding="utf-8",
    )

    diagnostics = inspect_chatgpt_web_archive(root)

    assert diagnostics["archive_validated"] is False
    assert diagnostics["error"]["reason"] == reason


def test_index_hash_mismatch_blocks_acceptance(tmp_path: Path) -> None:
    root = _make_archive(
        tmp_path,
        [_row("one", memberships=[{"scope": "main"}])],
    )
    manifest_path = root / "archive.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["currentIndexHashes"] = {"conversations": "0" * 64}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    diagnostics = inspect_chatgpt_web_archive(root)

    assert diagnostics["archive_validated"] is False
    assert diagnostics["error"]["reason"] == "archive_hash_mismatch"


def test_inspect_rejects_validation_count_mismatch(tmp_path: Path) -> None:
    root = _make_archive(
        tmp_path,
        [_row("one", memberships=[{"scope": "main"}])],
    )
    (root / "reports").mkdir()
    (root / "reports" / "validation.json").write_text(
        json.dumps(
            {
                "provider": "chatgpt-web",
                "schemaVersion": 1,
                "workspaceFingerprint": "fixture-fingerprint",
                "findings": [],
                "terminalState": "complete",
                "expectedConversationCount": 1,
                "completeConversationCount": 1,
                "extraRetainedConversationCount": 1,
            }
        ),
        encoding="utf-8",
    )

    diagnostics = inspect_chatgpt_web_archive(root)

    assert diagnostics["archive_validated"] is False
    assert diagnostics["error"]["reason"] == "conversation_count_mismatch"
    assert diagnostics["conversations_discovered"] == 1


def test_present_validation_metadata_must_be_complete(tmp_path: Path) -> None:
    root = _make_archive(
        tmp_path,
        [_row("one", memberships=[{"scope": "main"}])],
    )
    (root / "inventory.json").write_text(
        json.dumps(
            {
                "provider": "chatgpt-web",
                "schemaVersion": 1,
                "workspaceFingerprint": "fixture-fingerprint",
                "complete": False,
                "absentConversations": [],
                "conversations": [],
            }
        ),
        encoding="utf-8",
    )

    diagnostics = inspect_chatgpt_web_archive(root)

    assert diagnostics["archive_validated"] is False
    assert diagnostics["error"]["reason"] == "archive_incomplete"


def test_inventory_can_include_records_omitted_from_normalized_index(tmp_path: Path) -> None:
    row = _row("one", memberships=[{"scope": "main"}])
    root = _make_archive(tmp_path, [row], bodies={"one": _body("one")})
    (root / "reports").mkdir()
    (root / "reports" / "validation.json").write_text(
        json.dumps(
            {
                "provider": "chatgpt-web",
                "schemaVersion": 1,
                "workspaceFingerprint": "fixture-fingerprint",
                "findings": [],
                "terminalState": "complete",
                "expectedConversationCount": 1,
                "completeConversationCount": 1,
            }
        ),
        encoding="utf-8",
    )
    (root / "inventory.json").write_text(
        json.dumps(
            {
                "provider": "chatgpt-web",
                "schemaVersion": 1,
                "workspaceFingerprint": "fixture-fingerprint",
                "complete": True,
                "absentConversations": [],
                "conversations": [{"conversationId": "one"}, {"conversationId": "omitted"}],
            }
        ),
        encoding="utf-8",
    )

    run = project_chatgpt_web_archive(root, [], output_path=tmp_path / "projection.jsonl")

    assert run.accepted is True
    assert run.conversations_discovered == 1
    assert run.conversations_processed == 1


def test_empty_exclusion_list_keeps_source_valid(tmp_path: Path) -> None:
    row = _row("one", memberships=[{"scope": "main"}])
    root = _make_archive(tmp_path, [row], bodies={"one": _body("one")})

    run = project_chatgpt_web_archive(root, [], output_path=tmp_path / "projection.jsonl")

    assert run.accepted is True
    assert run.excluded_project_ids == ()
    assert run.conversations_eligible == 1
    assert run.conversations_processed == 1


def test_exclusion_is_deny_wins_before_body_read(tmp_path: Path) -> None:
    rows = [
        _row("excluded", memberships=[{"scope": "main"}]),
        _row(
            "excluded",
            memberships=[{"scope": "project", "projectId": "project-x"}],
        ),
    ]
    output = tmp_path / "projection.jsonl"
    run = project_chatgpt_web_archive(
        _make_archive(tmp_path, rows),
        ["project-x"],
        output_path=output,
    )

    assert run.conversations_discovered == 1
    assert run.conversations_eligible == 0
    assert run.conversations_skipped == 1
    assert run.conversations_failed == 0
    assert output.read_text(encoding="utf-8") == ""


def test_projection_follows_current_branch_and_allowlists_content() -> None:
    projected = project_normalized_conversation(_body("conversation-1"))

    assert projected["branch_node_ids"] == ["node-root", "node-user", "node-current"]
    messages = projected["messages"]
    assert isinstance(messages, list)
    assert [message["message_id"] for message in messages] == ["node-user", "node-current"]
    text = json.dumps(projected)
    assert "alternate branch secret" not in text
    assert "tool output secret" not in text
    assert "raw secret" not in text
    assert "/private/asset" not in text
    assert "extension secret" not in text
    assert messages[-1]["parts"][-1] == {
        "kind": "asset",
        "marker": "[asset omitted]",
        "asset_id": "asset-1",
    }


def test_latest_messages_and_latest_tail_win_the_budget() -> None:
    body = _body("conversation-1", latest_text="FINAL REQUIREMENT B")
    body["messages"][-2]["parts"] = [{"kind": "text", "text": "FINAL REQUIREMENT B"}]
    projected = project_normalized_conversation(
        body,
        max_conversation_chars=len("FINAL REQUIREMENT B"),
    )
    messages = projected["messages"]
    assert isinstance(messages, list)
    assert [message["message_id"] for message in messages] == ["node-current"]
    assert messages[0]["parts"][0]["text"] == "FINAL REQUIREMENT B"

    body = _body("conversation-2", latest_text="old context ... FINAL REQUIREMENT B")
    body["messages"][-2]["parts"] = [
        {"kind": "text", "text": "old context ... FINAL REQUIREMENT B"}
    ]
    projected = project_normalized_conversation(
        body,
        max_part_chars=len("FINAL REQUIREMENT B"),
        max_conversation_chars=len("FINAL REQUIREMENT B"),
    )
    messages = projected["messages"]
    assert isinstance(messages, list)
    assert messages[-1]["parts"][0]["text"] == "FINAL REQUIREMENT B"
    assert messages[-1]["parts"][0]["truncated"] is True


def test_conversation_recency_is_deterministic(tmp_path: Path) -> None:
    rows = [
        _row("older", memberships=[{"scope": "main"}], update_time=500),
        _row("newest", memberships=[{"scope": "main"}], update_time="2026-01-01T00:00:00Z"),
        _row("fallback", memberships=[{"scope": "main"}], create_time=1_500, update_time={}),
        _row("tie-b", memberships=[{"scope": "main"}], update_time=1_000),
        _row("tie-a", memberships=[{"scope": "main"}], update_time=1_000),
    ]
    bodies = {row["conversationId"]: _body(row["conversationId"]) for row in rows}
    output = tmp_path / "projection.jsonl"
    run = project_chatgpt_web_archive(
        _make_archive(tmp_path, rows, bodies=bodies),
        output_path=output,
    )

    assert run.accepted is True
    assert [json.loads(line)["conversation_id"] for line in output.read_text().splitlines()] == [
        "newest",
        "fallback",
        "tie-a",
        "tie-b",
        "older",
    ]


def test_projection_budget_failure_is_rejected(tmp_path: Path) -> None:
    row = _row("one", memberships=[{"scope": "main"}])
    root = _make_archive(tmp_path, [row], bodies={"one": _body("one")})

    run = project_chatgpt_web_archive(
        root,
        output_path=tmp_path / "projection.jsonl",
        max_projection_bytes=1,
    )

    assert run.accepted is False
    assert run.conversations_processed == 0
    assert run.failure_reasons["projection_read_limit"] == 1


def test_projection_cannot_write_inside_exporter_archive(tmp_path: Path) -> None:
    root = _make_archive(tmp_path, [])

    with pytest.raises(ChatGPTWebSourceError) as exc_info:
        project_chatgpt_web_archive(root, output_path=root / "projection.jsonl")

    assert exc_info.value.reason == "projection_archive_overlap"


def test_prepare_rejects_archive_inside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    archive = workspace / "export"
    archive.mkdir(parents=True)

    with pytest.raises(ChatGPTWebSourceError) as exc_info:
        prepare_chatgpt_web_source(workspace, root=archive, excluded_project_ids=[])

    assert exc_info.value.reason == "archive_workspace_overlap"


def test_body_failure_does_not_replace_previous_projection(tmp_path: Path) -> None:
    root = _make_archive(
        tmp_path,
        [_row("one", memberships=[{"scope": "main"}])],
        bodies={"one": _body("different")},
    )
    workspace = tmp_path / "workspace"
    projection = workspace / "sources" / "chatgpt-web" / "projection.jsonl"
    projection.parent.mkdir(parents=True)
    projection.write_text("previous\n", encoding="utf-8")

    run = prepare_chatgpt_web_source(workspace, root=root, excluded_project_ids=[])

    assert run.accepted is False
    assert run.conversations_failed == 1
    assert projection.read_text(encoding="utf-8") == "previous\n"
    report = json.loads((projection.parent / "run.json").read_text(encoding="utf-8"))
    assert report["accepted"] is False
    assert str(root) not in json.dumps(report)


def test_deselection_revokes_projection_and_psyche_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _make_archive(
        tmp_path,
        [_row("one", memberships=[{"scope": "main"}])],
        bodies={"one": _body("one")},
    )
    monkeypatch.setenv("SYKE_CHATGPT_WEB_ROOT", str(root))
    monkeypatch.setenv("SYKE_CHATGPT_WEB_EXCLUDED_PROJECT_IDS", "")

    from syke.runtime.workspace import initialize_workspace

    workspace = tmp_path / "workspace"
    initialize_workspace(workspace_root=workspace, selected_sources=("chatgpt-web",))
    assert (workspace / "sources/chatgpt-web/projection.jsonl").exists()
    psyche = (workspace / "PSYCHE.md").read_text()
    assert "sources/chatgpt-web/projection.jsonl" in psyche
    assert str(root) not in psyche

    initialize_workspace(workspace_root=workspace, selected_sources=())

    assert not (workspace / "sources/chatgpt-web").exists()
    assert "chatgpt-web" not in (workspace / "PSYCHE.md").read_text()

    # Removing the configuration must not resurrect a stale dynamic adapter in
    # the prompt surface either.
    monkeypatch.delenv("SYKE_CHATGPT_WEB_ROOT", raising=False)
    initialize_workspace(workspace_root=workspace, selected_sources=None)
    assert "chatgpt-web" not in (workspace / "PSYCHE.md").read_text()


def test_symlinked_index_directory_is_rejected(tmp_path: Path) -> None:
    root = _make_archive(tmp_path, [])
    real_index = root / "indexes" / "conversations.jsonl"
    real_index.unlink()
    outside = tmp_path / "outside-index"
    outside.write_text("", encoding="utf-8")
    (root / "indexes").rmdir()
    (root / "indexes").symlink_to(outside.parent, target_is_directory=True)

    diagnostics = inspect_chatgpt_web_archive(root)

    assert diagnostics["archive_validated"] is False
    assert diagnostics["error"]["reason"] == "archive_path_escape"


def test_path_escape_is_rejected_during_index_validation(tmp_path: Path) -> None:
    root = _make_archive(
        tmp_path,
        [_row("one", memberships=[{"scope": "main"}])],
    )
    index_path = root / "indexes" / "conversations.jsonl"
    row = json.loads(index_path.read_text(encoding="utf-8"))
    row["normalizedPath"] = "../outside.json"
    index_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    diagnostics = inspect_chatgpt_web_archive(root)

    assert diagnostics["archive_validated"] is False
    assert diagnostics["index_validated"] is False
    assert diagnostics["conversations_discovered"] == 0
    assert diagnostics["index_failures"][0]["reason"] in {
        "archive_path_escape",
        "invalid_archive_path",
    }


def test_configured_archive_root_is_never_a_sandbox_read_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = tmp_path / "export"
    archive.mkdir()
    monkeypatch.setenv("SYKE_CHATGPT_WEB_ROOT", str(archive))
    monkeypatch.setenv("SYKE_SANDBOX_HARNESS_PATHS", str(archive))

    from syke.runtime.sandbox import _harness_read_paths, generate_seatbelt_profile

    assert str(archive.resolve()) not in _harness_read_paths(selected_sources=("chatgpt-web",))
    profile = generate_seatbelt_profile(
        tmp_path / "workspace",
        selected_sources=("chatgpt-web",),
    )
    assert f'(allow file-read* (subpath "{archive.resolve()}"))' not in profile
    assert f'(deny file-read* (subpath "{archive.resolve()}"))' in profile


def test_invalid_archive_is_reported_without_body_reads(tmp_path: Path) -> None:
    root = tmp_path / "not-an-archive"
    root.mkdir()

    diagnostics = inspect_chatgpt_web_archive(root)

    assert diagnostics["archive_validated"] is False
    assert diagnostics["error"]["reason"] == "archive_manifest_missing"
    with pytest.raises(ChatGPTWebSourceError):
        prepare_chatgpt_web_source(tmp_path / "workspace", root=root, excluded_project_ids=[])
