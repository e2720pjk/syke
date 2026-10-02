from __future__ import annotations

from pathlib import Path

import pytest

from syke import config
from syke.cli_support.setup_support import setup_source_inventory
from syke.observe.bootstrap import ensure_adapters
from syke.observe.catalog import active_sources, discovered_roots, get_source, iter_discovered_files
from syke.observe.seeds import get_seed_adapter_md_path
from syke.runtime.prompt_context import build_prompt
from syke.source_selection import set_selected_sources

_MEMORY_MARKER_BY_SOURCE = {
    "antigravity": "`~/.gemini/antigravity/brain/`",
    "claude-code": "`~/.claude/projects/<project-key>/memory/MEMORY.md`",
    "codex": "`~/.codex/memories/`",
    "copilot": "No separate local durable memory surface is confirmed.",
    "cursor": "`aicontext.personalContext`",
    "hermes": "`memories/MEMORY.md`",
    "chatgpt-web": "`source/account/memories.json`",
    "opencode": "No separate harness-owned durable memory surface is present",
    "pi": "No separate harness-owned durable memory surface is present",
}


def test_all_seed_adapters_deploy_and_resolve_from_flat_workspace(tmp_path: Path) -> None:
    specs = active_sources()
    results = ensure_adapters(tmp_path)
    adapters_dir = tmp_path / "adapters"

    assert {(result.source, result.status) for result in results} == {
        (spec.source, "installed") for spec in specs
    }
    for spec in specs:
        seed = get_seed_adapter_md_path(spec.source)
        deployed = adapters_dir / f"{spec.source}.md"

        assert seed is not None
        assert deployed.is_file()
        assert deployed.read_bytes() == seed.read_bytes()


def test_antigravity_catalog_unifies_current_product_family_transcripts(
    tmp_path: Path,
) -> None:
    desktop_artifact = tmp_path / ".gemini/antigravity/brain/desktop/task.md"
    cli_transcript = (
        tmp_path / ".gemini/antigravity-cli/brain/cli/.system_generated/logs/transcript.jsonl"
    )
    ide_transcript = (
        tmp_path / ".gemini/antigravity-ide/brain/ide/.system_generated/logs/transcript.jsonl"
    )
    for path, content in (
        (desktop_artifact, "# Task\n\nKeep the product family current.\n"),
        (
            cli_transcript,
            '{"step_index":0,"source":"USER_EXPLICIT","type":"USER_INPUT",'
            '"status":"DONE","created_at":"2026-08-23T06:30:30Z",'
            '"content":"Observe the Antigravity CLI."}\n',
        ),
        (
            ide_transcript,
            '{"step_index":1,"source":"MODEL","type":"PLANNER_RESPONSE",'
            '"status":"DONE","created_at":"2026-08-23T06:30:31Z",'
            '"content":"IDE observation complete."}\n',
        ),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    spec = get_source("antigravity")

    assert spec is not None
    assert {root.path for root in spec.discover.roots} == {
        "~/.gemini/antigravity",
        "~/.gemini/antigravity-cli",
        "~/.gemini/antigravity-ide",
    }
    assert iter_discovered_files(spec, home=tmp_path) == sorted(
        [desktop_artifact.resolve(), cli_transcript.resolve(), ide_transcript.resolve()]
    )


def test_antigravity_guide_covers_current_transcripts_and_authority() -> None:
    seed = get_seed_adapter_md_path("antigravity")

    assert seed is not None
    content = seed.read_text(encoding="utf-8")
    for marker in (
        "~/.gemini/antigravity-cli/brain/",
        "~/.gemini/antigravity-ide/brain/",
        "transcript_full.jsonl",
        "USER_INPUT",
        "PLANNER_RESPONSE",
        "invoke_subagent",
        "incomplete trailing line",
        "parent or user accepts it",
    ):
        assert marker in content


def test_pi_catalog_discovers_default_sessions_without_syke_runtime_history(
    tmp_path: Path,
) -> None:
    external_session = tmp_path / ".pi/agent/sessions/project/session.jsonl"
    external_session.parent.mkdir(parents=True)
    external_session.write_text('{"type":"session","version":3}\n', encoding="utf-8")
    internal_session = tmp_path / ".syke/control/sessions/synthesis.jsonl"
    internal_session.parent.mkdir(parents=True)
    internal_session.write_text('{"type":"session","version":3}\n', encoding="utf-8")

    spec = get_source("pi")

    assert spec is not None
    assert iter_discovered_files(spec, home=tmp_path) == [external_session.resolve()]


def test_seed_adapters_separate_project_instructions_from_harness_memory() -> None:
    for spec in active_sources():
        seed = get_seed_adapter_md_path(spec.source)

        assert seed is not None
        content = seed.read_text(encoding="utf-8")
        project_start = content.index("## Project instructions")
        memory_start = content.index("## Harness memory")
        distribution_start = content.index("## Distribution")

        assert project_start < memory_start < distribution_start
        assert _MEMORY_MARKER_BY_SOURCE[spec.source] in content[memory_start:distribution_start]


def test_chatgpt_guide_covers_native_graph_and_archive_boundaries() -> None:
    seed = get_seed_adapter_md_path("chatgpt-web")
    assert seed is not None
    content = seed.read_text(encoding="utf-8")
    for marker in (
        "raw-complete.json.detailPath",
        "nodes[].parentId",
        "messages[].nodeId",
        "their values may be equal",
        "Copy identifiers verbatim",
        "Independent cross-source evidence",
        "messages[].selected",
        "GRAPH_*",
        "Alternative branches",
        "parts[].raw",
        "extensions.chatgpt.author.role",
        "artifacts-complete.json",
        "inventory.json.absentConversations",
        "absentFromCurrentInventory",
        "extraRetainedConversationCount",
        "not a live complete ChatGPT account",
    ):
        assert marker in content


@pytest.mark.parametrize("archive_dir", ["", "ChatGPTExport-fixture"])
@pytest.mark.parametrize("has_index", [False, True])
def test_chatgpt_discovers_supplied_archives_with_or_without_indexes(
    tmp_path: Path, monkeypatch, chatgpt_archive, archive_dir: str, has_index: bool
) -> None:
    home = tmp_path / "home"
    archive = chatgpt_archive(home / ".syke-chatgpt-web" / archive_dir, indexed=has_index)
    conversation = archive / "conversations" / "fixture" / "conversation.json"
    conversation.with_suffix(".json.part").write_text("{}", encoding="utf-8")
    outside = home / "Downloads" / "ChatGPTExport-unselected" / "indexes"
    outside.mkdir(parents=True)
    (outside / "conversations.jsonl").write_text("{}\n", encoding="utf-8")
    expected = [conversation.resolve()]
    if has_index:
        index = archive / "indexes" / "conversations.jsonl"
        expected.append(index.resolve())
    monkeypatch.setenv("HOME", str(home))
    spec = get_source("chatgpt-web")

    assert spec is not None
    assert iter_discovered_files(spec, home=home) == sorted(expected)
    assert discovered_roots(spec, home=home) == [(home / ".syke-chatgpt-web").resolve()]
    row = next(row for row in setup_source_inventory() if row["source"] == "chatgpt-web")
    assert row["detected"] is True
    assert row["files_found"] == len(expected)


@pytest.mark.parametrize("context", ["ask", "synthesis"])
def test_chatgpt_uses_existing_selection_and_prompt_routes(
    tmp_path: Path, monkeypatch, chatgpt_archive, db, user_id: str, context: str
) -> None:
    home = tmp_path / "home"
    # A supplied archive can be linked into the conventional discovery root.
    archive = chatgpt_archive(home / "supplied-archive")
    home.mkdir(exist_ok=True)
    (home / ".syke-chatgpt-web").symlink_to(archive, target_is_directory=True)
    pi = home / ".pi" / "agent" / "sessions" / "project" / "session.jsonl"
    pi.parent.mkdir(parents=True)
    pi.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(config, "SYKE_HOME", tmp_path / "syke")
    selected = set_selected_sources(user_id, ["chatgpt-web", "pi"])
    workspace = tmp_path / "workspace"
    ensure_adapters(workspace, selected_sources=selected)
    changes_before = db.conn.total_changes

    prompt = build_prompt(
        workspace,
        db=db,
        user_id=user_id,
        context=context,
        now="2026-09-30T00:00:00Z",
        selected_sources=selected,
    )

    assert "- chatgpt-web:" in prompt
    assert "- pi:" in prompt
    assert "- codex:" not in prompt
    assert str(archive.resolve()) in prompt
    assert str(workspace / "adapters" / "chatgpt-web.md") in prompt
    seed = get_seed_adapter_md_path("chatgpt-web")
    assert seed is not None
    assert (workspace / "adapters" / "chatgpt-web.md").read_text() == seed.read_text()
    assert db.conn.total_changes == changes_before

    selected = set_selected_sources(user_id, ["pi"])
    prompt = build_prompt(
        workspace,
        db=db,
        user_id=user_id,
        context=context,
        now="2026-09-30T00:00:00Z",
        selected_sources=selected,
    )
    assert "- chatgpt-web:" not in prompt
    assert "- pi:" in prompt
