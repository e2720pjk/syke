from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path
from unittest.mock import patch

import click
import pytest

from syke import config
from syke.cli_support.setup_support import choose_setup_sources_interactive, setup_source_inventory
from syke.entrypoint import cli
from syke.observe.catalog import get_source, iter_discovered_files, source_inventory
from syke.runtime.prompt_context import build_prompt
from syke.source_selection import (
    _selection_path,
    get_selected_sources,
    get_source_paths,
    read_source_state,
    register_source_path,
    remove_source_path,
    set_selected_sources,
)


def chatgpt_row() -> dict:
    return next(row for row in source_inventory("test") if row["source"] == "chatgpt-web")


@pytest.mark.parametrize("selected", [None, [], ["pi", "codex"]])
def test_add_is_idempotent_and_preserves_selection_mode(
    selected, chatgpt_archive, cli_runner
) -> None:
    archive = chatgpt_archive(Path.home() / "Downloads" / "my archive")
    if selected is not None:
        set_selected_sources("test", selected)
    result = cli_runner.invoke(cli, ["source", "add", "chatgpt-web", str(archive), "--json"])
    assert result.exit_code == 0, result.output
    info = json.loads(result.stdout)
    assert info["recognized"] and info["enabled"] and info["changed"]
    assert info["runtime_readable"] is None
    assert get_selected_sources("test") == (
        None if selected is None else tuple(selected + ["chatgpt-web"])
    )
    assert get_source_paths("chatgpt-web") == (str(archive),)
    assert Path(info["adapter"]).is_file()
    assert not config.user_syke_db_path("test").exists()
    assert not (config.user_workspace_dir("test") / "MEMEX.md").exists()
    before = _selection_path("test").read_bytes()
    alias = Path.home() / "archive-alias"
    alias.symlink_to(archive, target_is_directory=True)
    result = cli_runner.invoke(cli, ["source", "add", "chatgpt-web", str(alias), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["changed"] is False
    assert _selection_path("test").read_bytes() == before
    set_selected_sources("test", ["pi"])
    assert get_source_paths("chatgpt-web") == (str(archive),)
    assert read_source_state("test")["selected_sources"] == ["pi"]


@pytest.mark.parametrize("context", ["ask", "synthesis"])
def test_registered_scope_is_shared_and_hot_loaded_without_default_fallback(
    context, chatgpt_archive, db
) -> None:
    default = chatgpt_archive(Path.home() / ".syke-chatgpt-web" / "ChatGPTExport-default")
    archive = chatgpt_archive(Path.home() / "Downloads" / "ChatGPTExport-selected")
    set_selected_sources("test", ["pi"])
    spec = get_source("chatgpt-web")
    assert spec is not None
    assert any(path.is_relative_to(default) for path in iter_discovered_files(spec))
    register_source_path("test", "chatgpt-web", archive)
    assert all(path.is_relative_to(archive) for path in iter_discovered_files(spec))
    row = chatgpt_row()
    setup_row = next(row for row in setup_source_inventory() if row["source"] == "chatgpt-web")
    assert row == setup_row
    assert row["roots"] == [str(archive)] and row["files_found"] == 2
    assert row["archives"][0]["inventory_generated_at"] == "2026-09-29T12:00:00Z"
    before = db.conn.total_changes
    prompt = build_prompt(
        config.user_workspace_dir("test"),
        db=db,
        user_id="test",
        context=context,
        selected_sources=get_selected_sources("test"),
        now="2026-09-30T00:00:00Z",
    )
    assert str(archive) in prompt and str(default) not in prompt
    assert "configured scope only" in prompt and "- pi:" in prompt
    assert db.conn.total_changes == before
    archive.rename(archive.with_name("moved"))
    row = chatgpt_row()
    assert row["enabled"] and not row["detected"] and row["state"] == "missing"
    assert row["roots"] == [str(archive)]
    assert iter_discovered_files(spec) == []


@pytest.mark.parametrize("source", ["unknown-source", "pi"])
def test_unknown_or_non_configurable_source_cannot_be_registered(
    source, chatgpt_archive, cli_runner
) -> None:
    archive = chatgpt_archive(Path.home() / "archive")
    result = cli_runner.invoke(cli, ["source", "add", source, str(archive), "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["ok"] is False
    assert not _selection_path("test").exists()


@pytest.mark.parametrize(
    "failure",
    ["empty", "missing", "provider", "schema", "normalizer", "json", "pointer", "unreadable"],
)
def test_invalid_archives_do_not_change_selection_or_install_guides(
    failure, chatgpt_archive, cli_runner, monkeypatch
) -> None:
    archive = chatgpt_archive(Path.home() / "archive")
    if failure == "missing":
        archive = Path.home() / "missing"
    elif failure == "empty":
        archive = Path.home() / "empty"
        archive.mkdir()
    elif failure == "json":
        (archive / "inventory.json").write_text("{broken", encoding="utf-8")
    elif failure == "pointer":
        index = archive / "indexes/conversations.jsonl"
        row = json.loads(index.read_text())
        row["rawPath"] = "../../private-evidence.json"
        index.write_text(json.dumps(row), encoding="utf-8")
    elif failure == "unreadable":
        from syke.observe import chatgpt_web

        access = chatgpt_web.os.access
        monkeypatch.setattr(
            chatgpt_web.os,
            "access",
            lambda path, mode: False if Path(path) == archive else access(path, mode),
        )
    else:
        manifest = archive / "archive.json"
        data = json.loads(manifest.read_text())
        field, value = {
            "provider": ("provider", "grok"),
            "schema": ("schemaVersion", 2),
            "normalizer": ("normalizerVersion", "future-v2"),
        }[failure]
        data[field] = value
        manifest.write_text(json.dumps(data), encoding="utf-8")
    set_selected_sources("test", ["pi"])
    before = _selection_path("test").read_bytes()
    result = cli_runner.invoke(cli, ["source", "add", "chatgpt-web", str(archive), "--json"])
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["ok"] is False
    assert _selection_path("test").read_bytes() == before
    assert not (config.user_workspace_dir("test") / "adapters/chatgpt-web.md").exists()


def test_home_boundary_and_symlinked_metadata_are_not_bypassed(
    tmp_path, chatgpt_archive, cli_runner
) -> None:
    outside = chatgpt_archive(tmp_path / "outside-home")
    alias = Path.home() / "alias"
    alias.symlink_to(outside, target_is_directory=True)
    for path in (outside, alias):
        result = cli_runner.invoke(cli, ["source", "add", "chatgpt-web", str(path), "--json"])
        assert result.exit_code == 1
        assert "readable home" in json.loads(result.stdout)["error"]
    archive = chatgpt_archive(Path.home() / "archive")
    (archive / "inventory.json").unlink()
    (archive / "inventory.json").symlink_to(outside / "inventory.json")
    result = cli_runner.invoke(cli, ["source", "add", "chatgpt-web", str(archive), "--json"])
    assert result.exit_code == 1
    assert "escapes archive" in json.loads(result.stdout)["error"]
    assert not _selection_path("test").exists()


@pytest.mark.parametrize("kind", ["partial", "empty", "retained"])
def test_supported_partial_empty_and_retained_archives_have_honest_status(
    kind, chatgpt_archive, cli_runner
) -> None:
    archive = chatgpt_archive(
        Path.home() / "archive",
        indexed=kind != "partial",
        partial=kind == "partial",
        empty=kind == "empty",
        retained=kind == "retained",
    )
    result = cli_runner.invoke(cli, ["source", "add", "chatgpt-web", str(archive), "--json"])
    assert result.exit_code == 0, result.output
    result = cli_runner.invoke(cli, ["source", "list", "chatgpt-web", "--json"])
    assert result.exit_code == 0, result.output
    row = json.loads(result.stdout)["sources"][0]
    assert row["enabled"] and row["detected"] and row["recognized"]
    assert row["runtime_readable"] is None
    assert row["state"] == ("available" if kind == "retained" else kind)
    info = row["archives"][0]
    assert info["saved_conversations"] == {"partial": 1, "empty": 0, "retained": 2}[kind]
    assert info["retained_conversations"] == {"partial": None, "empty": 0, "retained": 1}[kind]


def test_collection_filters_unsupported_archives_and_deduplicates_overlapping_roots(
    chatgpt_archive,
) -> None:
    collection = Path.home() / "collection"
    archive = chatgpt_archive(collection / "ChatGPTExport-supported")
    unsupported = collection / "ChatGPTExport-unsupported"
    unsupported.mkdir()
    (unsupported / "inventory.json").write_text('{"provider": "other"}')
    register_source_path("test", "chatgpt-web", collection)
    register_source_path("test", "chatgpt-web", archive)
    row = chatgpt_row()
    assert row["state"] == "partial" and len(row["archives"]) == 1
    assert row["files_found"] == 2 and row["warnings"]


def test_removing_last_path_disables_only_that_source_without_deleting_data(
    chatgpt_archive, cli_runner, db
) -> None:
    archive = chatgpt_archive(Path.home() / "archive")
    chatgpt_archive(Path.home() / ".syke-chatgpt-web")
    second = chatgpt_archive(Path.home() / "second")
    register_source_path("test", "chatgpt-web", archive)
    register_source_path("test", "chatgpt-web", second)
    before = db.conn.total_changes
    remove_source_path("test", "chatgpt-web", second)
    assert chatgpt_row()["enabled"]
    result = cli_runner.invoke(cli, ["source", "remove", "chatgpt-web", str(archive), "--json"])
    assert result.exit_code == 0, result.output
    row = chatgpt_row()
    assert not row["enabled"] and row["roots"] == [] and row["files_found"] == 0
    assert "pi" in get_selected_sources("test")
    assert archive.is_dir() and second.is_dir()
    assert db.conn.total_changes == before


def test_concurrent_registration_and_selection_keep_every_path(chatgpt_archive) -> None:
    archives = [chatgpt_archive(Path.home() / str(i)) for i in range(3)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(register_source_path, "test", "chatgpt-web", path) for path in archives
        ]
        futures.append(pool.submit(set_selected_sources, "test", ["pi"]))
        for future in futures:
            future.result()
    assert set(get_source_paths("chatgpt-web")) == {str(path) for path in archives}
    assert "pi" in get_selected_sources("test")


def test_corrupt_state_is_not_overwritten_or_used_for_default_discovery(
    chatgpt_archive, cli_runner
) -> None:
    archive = chatgpt_archive(Path.home() / "archive")
    state = _selection_path("test")
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text('{"schema_version":2,"selected_sources":[],"source_paths":"broken"}')
    before = state.read_bytes()
    result = cli_runner.invoke(cli, ["source", "add", "chatgpt-web", str(archive), "--json"])
    assert result.exit_code == 1
    assert state.read_bytes() == before
    assert get_selected_sources("test") == ()
    assert get_source_paths("chatgpt-web") == ()
    assert chatgpt_row()["state"] == "configuration_error"


def test_setup_can_add_an_archive_when_no_sources_were_detected(
    chatgpt_archive, cli_runner
) -> None:
    archive = chatgpt_archive(Path.home() / "archive")
    items = []

    @click.command()
    def choose():
        click.echo(json.dumps(choose_setup_sources_interactive(items, user_id="test")))

    with patch("syke.cli_support.auth_flow.term_menu_select_many", return_value=[0]):
        result = cli_runner.invoke(choose, input=str(archive) + "\n")
    assert result.exit_code == 0, result.output
    assert result.output.rstrip().endswith('["chatgpt-web"]')
    assert next(item for item in items if item["source"] == "chatgpt-web")["detected"]
    set_selected_sources("test", ["chatgpt-web"])
    assert get_source_paths("chatgpt-web") == (str(archive),)


def test_case_insensitive_filesystem_aliases_are_idempotent(chatgpt_archive) -> None:
    archive = chatgpt_archive(Path.home() / "SavedArchive")
    alias = archive.with_name("savedarchive")
    if not alias.is_dir():
        pytest.skip("filesystem is case-sensitive")
    register_source_path("test", "chatgpt-web", archive)
    assert register_source_path("test", "chatgpt-web", alias)["changed"] is False
    assert get_source_paths("chatgpt-web") == (str(archive),)
    remove_source_path("test", "chatgpt-web", alias)
    assert get_source_paths("chatgpt-web") == ()


def test_non_regular_metadata_does_not_block_the_cli(chatgpt_archive, cli_runner) -> None:
    import os

    if not hasattr(os, "mkfifo"):
        pytest.skip("named pipes unavailable")
    archive = chatgpt_archive(Path.home() / "archive")
    (archive / "inventory.json").unlink()
    os.mkfifo(archive / "inventory.json")
    result = cli_runner.invoke(cli, ["source", "add", "chatgpt-web", str(archive), "--json"])
    assert result.exit_code == 1
    assert "regular file" in json.loads(result.stdout)["error"]
    assert not _selection_path("test").exists()


def test_failed_atomic_write_preserves_existing_configuration(chatgpt_archive, monkeypatch) -> None:
    from syke import source_selection

    archive = chatgpt_archive(Path.home() / "archive")
    set_selected_sources("test", ["pi"])
    state = _selection_path("test")
    before = state.read_bytes()

    def fail(*_args):
        raise OSError("simulated write failure")

    monkeypatch.setattr(source_selection.os, "replace", fail)
    with pytest.raises(OSError, match="simulated"):
        register_source_path("test", "chatgpt-web", archive)
    assert state.read_bytes() == before
    assert not list(state.parent.glob(f".{state.name}.*.tmp"))


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 2, "source_paths": {}},
        {"schema_version": True, "selected_sources": ["pi"]},
        {
            "schema_version": 2,
            "selected_sources": None,
            "source_paths": {"chatgpt-web": ["~syke-no-such-user/archive"]},
        },
    ],
)
def test_invalid_state_fields_fail_closed(payload) -> None:
    state = _selection_path("test")
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps(payload), encoding="utf-8")
    assert get_selected_sources("test") == ()
    assert get_source_paths("chatgpt-web") == ()
