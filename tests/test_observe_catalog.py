from __future__ import annotations

from pathlib import Path

from syke.observe.catalog import get_source, iter_discovered_files
from syke.observe.seeds import get_seed_adapter_md_path


def test_pi_source_discovers_external_sessions_recursively(tmp_path: Path) -> None:
    sessions = tmp_path / ".pi" / "agent" / "sessions"
    first = sessions / "--project--" / "session.jsonl"
    second = sessions / "--project--" / "subagent" / "run-0" / "session.jsonl"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text('{"type":"session"}\n', encoding="utf-8")
    second.write_text('{"type":"session"}\n', encoding="utf-8")
    (sessions / "README.txt").parent.mkdir(parents=True, exist_ok=True)
    (sessions / "README.txt").write_text("ignore", encoding="utf-8")

    spec = get_source("pi")
    assert spec is not None
    assert spec.format_cluster == "jsonl"
    assert iter_discovered_files(spec, home=tmp_path) == [first.resolve(), second.resolve()]


def test_pi_source_rejects_sessions_symlinked_into_syke_workspace(tmp_path: Path) -> None:
    syke_sessions = tmp_path / ".syke" / "sessions"
    syke_sessions.mkdir(parents=True)
    (syke_sessions / "self.jsonl").write_text('{"type":"session"}\n', encoding="utf-8")

    pi_sessions = tmp_path / ".pi" / "agent" / "sessions"
    pi_sessions.parent.mkdir(parents=True)
    pi_sessions.symlink_to(syke_sessions, target_is_directory=True)

    spec = get_source("pi")
    assert spec is not None
    assert iter_discovered_files(spec, home=tmp_path) == []


def test_pi_seed_requires_bounded_extraction() -> None:
    seed = get_seed_adapter_md_path("pi")
    assert seed is not None
    content = seed.read_text(encoding="utf-8")

    assert "at most 100 files" in content
    assert "2,000 lines or 1 MiB per file" in content
    assert "at most 200 rendered records" in content
    assert "per file" in content
    assert "sort | head -n 100" in content
    assert "| head -n 200" in content


def test_pi_source_has_a_seed_adapter_and_excludes_syke_runtime_history() -> None:
    spec = get_source("pi")

    assert spec is not None
    assert all(".syke" not in root.path for root in spec.discover.roots)
    seed = get_seed_adapter_md_path("pi")
    assert seed is not None
    content = seed.read_text(encoding="utf-8")
    assert "~/.pi/agent/sessions/**/*.jsonl" in content
    assert "~/.syke/sessions/" in content
