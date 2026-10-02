from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from syke.llm import pi_install as pi_client


def _write_test_pi_install(
    prefix: Path,
    *,
    pi_version: str = pi_client.PI_PACKAGE_VERSION,
    root_pi_version: str | None = None,
) -> Path:
    root_pi_version = root_pi_version or pi_version
    (prefix / "package.json").parent.mkdir(parents=True, exist_ok=True)
    (prefix / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {
                    pi_client.PI_PACKAGE: root_pi_version,
                    pi_client.PI_SCHEMA_PACKAGE: pi_client.PI_SCHEMA_VERSION,
                }
            }
        ),
        encoding="utf-8",
    )
    package_root = prefix / "node_modules" / "@earendil-works" / "pi-coding-agent"
    package_root.mkdir(parents=True, exist_ok=True)
    (package_root / "package.json").write_text(
        json.dumps({"name": pi_client.PI_PACKAGE, "version": pi_version}),
        encoding="utf-8",
    )
    cli = package_root / "dist" / "cli.js"
    cli.parent.mkdir(parents=True, exist_ok=True)
    cli.write_text("console.log('pi');", encoding="utf-8")
    schema_root = prefix / "node_modules" / pi_client.PI_SCHEMA_PACKAGE
    schema_root.mkdir(parents=True, exist_ok=True)
    (schema_root / "package.json").write_text(
        json.dumps({"name": pi_client.PI_SCHEMA_PACKAGE, "version": pi_client.PI_SCHEMA_VERSION}),
        encoding="utf-8",
    )
    return cli


def _patch_pi_install_paths(monkeypatch, pi_home: Path) -> tuple[Path, Path, Path]:
    pi_prefix = pi_home / "pi"
    pi_bin = pi_home / "bin" / "pi"
    pi_node = pi_home / "bin" / "node"
    package_root = pi_prefix / "node_modules" / "@earendil-works" / "pi-coding-agent"
    monkeypatch.setattr(pi_client, "PI_LOCAL_PREFIX", pi_prefix)
    monkeypatch.setattr(pi_client, "PI_PACKAGE_ROOT", package_root)
    monkeypatch.setattr(pi_client, "PI_CLI_JS", package_root / "dist" / "cli.js")
    monkeypatch.setattr(pi_client, "PI_BIN", pi_bin)
    monkeypatch.setattr(pi_client, "PI_NODE_BIN", pi_node)
    return pi_prefix, pi_bin, pi_node


def _write_fake_pi_install_binaries(tmp_path: Path) -> tuple[Path, Path]:
    node = tmp_path / "node"
    node.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo v22.23.2; exit 0; fi\n'
        'if [ "$1" = "-e" ]; then exit 0; fi\n'
        'if [ "$2" = "--version" ]; then echo shim-pi-version; exit 0; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    node.chmod(0o755)

    npm = tmp_path / "npm"
    npm.write_text(
        f"""#!{sys.executable}
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
prefix = Path(args[args.index("--prefix") + 1])
pi_package = {pi_client.PI_PACKAGE!r}
requested_version = next(arg for arg in args if arg.startswith(pi_package + "@")).rsplit("@", 1)[1]
pi_version = os.environ.get("FAKE_PI_VERSION", requested_version)
schema_package = {pi_client.PI_SCHEMA_PACKAGE!r}
schema_version = {pi_client.PI_SCHEMA_VERSION!r}

log_path = os.environ.get("FAKE_NPM_LOG")
if log_path:
    with Path(log_path).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(args) + "\\n")

prefix.mkdir(parents=True, exist_ok=True)
(prefix / "package.json").write_text(
    json.dumps({{"dependencies": {{pi_package: pi_version, schema_package: schema_version}}}}),
    encoding="utf-8",
)
pi_root = prefix / "node_modules" / Path(*pi_package.split("/"))
pi_root.mkdir(parents=True, exist_ok=True)
(pi_root / "package.json").write_text(
    json.dumps({{"name": pi_package, "version": pi_version}}),
    encoding="utf-8",
)
cli = pi_root / "dist" / "cli.js"
cli.parent.mkdir(parents=True, exist_ok=True)
cli.write_text("console.log('pi');", encoding="utf-8")
schema_root = prefix / "node_modules" / Path(*schema_package.split("/"))
schema_root.mkdir(parents=True, exist_ok=True)
(schema_root / "package.json").write_text(
    json.dumps({{"name": schema_package, "version": schema_version}}),
    encoding="utf-8",
)
""",
        encoding="utf-8",
    )
    npm.chmod(0o755)
    return node, npm


def test_pi_install_pipeline_executes_and_replaces_atomically(tmp_path: Path, monkeypatch) -> None:
    pi_home = tmp_path / "syke-home"
    pi_prefix, pi_bin, pi_node = _patch_pi_install_paths(monkeypatch, pi_home)
    old_marker = pi_prefix / "old-runtime"
    old_marker.parent.mkdir(parents=True)
    old_marker.write_text("old", encoding="utf-8")
    old_launcher_target = tmp_path / "old-cli"
    old_launcher_target.write_text("old", encoding="utf-8")
    pi_bin.parent.mkdir(parents=True, exist_ok=True)
    pi_bin.symlink_to(old_launcher_target)
    node, npm = _write_fake_pi_install_binaries(tmp_path)
    npm_log = tmp_path / "npm.log"

    monkeypatch.setattr(pi_client, "_NODE_CANDIDATES", [])
    monkeypatch.setattr(pi_client, "_NPM_CANDIDATES", [npm])
    monkeypatch.setattr(
        pi_client.shutil, "which", lambda name: str(node) if name == "node" else None
    )
    monkeypatch.setenv("FAKE_NPM_LOG", str(npm_log))
    monkeypatch.setenv("FAKE_PI_VERSION", "0.0.0")

    node_bin = pi_client.ensure_node_binary()
    with pytest.raises(RuntimeError, match=f"at {pi_client.PI_PACKAGE_VERSION}"):
        pi_client._install_pi_runtime(node_bin)

    assert old_marker.read_text(encoding="utf-8") == "old"
    assert pi_bin.is_symlink()
    assert list(pi_prefix.parent.glob(".pi.staging-*")) == []

    monkeypatch.setenv("FAKE_PI_VERSION", pi_client.PI_PACKAGE_VERSION)
    pi_client._install_pi_runtime(node_bin)
    launcher = Path(pi_client.ensure_pi_binary())

    assert launcher == pi_bin
    assert launcher.exists()
    assert not launcher.is_symlink()
    assert pi_node.is_symlink()
    assert pi_node.resolve() == node.resolve()
    assert not old_marker.exists()
    assert list(pi_prefix.parent.glob(".pi.staging-*")) == []
    assert list(pi_prefix.parent.glob(".pi.backup-*")) == []
    pi_client._validate_pi_install(pi_prefix)

    launcher_text = launcher.read_text(encoding="utf-8")
    assert str(pi_node) in launcher_text
    assert str(pi_client.PI_CLI_JS) in launcher_text
    launched = subprocess.run(
        [str(launcher), "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert launched.stdout.strip() == "shim-pi-version"

    install_commands = [json.loads(line) for line in npm_log.read_text().splitlines()]
    assert len(install_commands) == 2
    for command in install_commands:
        assert pi_client.PI_PACKAGE_SPEC in command
        assert pi_client.PI_SCHEMA_SPEC in command
        assert "--save-exact" in command
        assert "--ignore-scripts" in command

    assert pi_client.ensure_pi_binary() == str(launcher)
    assert len(npm_log.read_text().splitlines()) == 2


def test_node_selection_rejects_incomplete_runtimes_and_uses_valid_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    _pi_prefix, _pi_bin, pi_node = _patch_pi_install_paths(monkeypatch, tmp_path / "home")
    old_node = tmp_path / "old-node"
    supported_node = tmp_path / "supported-node"
    old_node.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then echo v22.18.0; exit 0; fi\nexit 0\n',
        encoding="utf-8",
    )
    supported_node.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then echo v22.23.2; exit 0; fi\nexit 0\n',
        encoding="utf-8",
    )
    old_node.chmod(0o755)
    supported_node.chmod(0o755)
    monkeypatch.setattr(pi_client, "_NODE_CANDIDATES", [supported_node])
    monkeypatch.setattr(pi_client.shutil, "which", lambda name: str(old_node))

    assert pi_client.ensure_node_binary() == pi_node
    assert pi_node.resolve() == supported_node.resolve()

    def run_without_zstd(cmd, **_kwargs):
        if cmd[1] == "--version":
            return subprocess.CompletedProcess(cmd, 0, "v22.23.2\n", "")
        return subprocess.CompletedProcess(cmd, 1, "", "Zstandard support is unavailable")

    monkeypatch.setattr(pi_client.subprocess, "run", run_without_zstd)
    supported, detail = pi_client._node_supports_pi_runtime(supported_node)

    assert supported is False
    assert "Zstandard support is unavailable" in detail


def test_ensure_pi_binary_preserves_and_uses_a_newer_local_version(
    tmp_path: Path, monkeypatch
) -> None:
    pi_prefix, _pi_bin, _pi_node = _patch_pi_install_paths(monkeypatch, tmp_path / "home")
    major, minor, patch = (int(part) for part in pi_client.PI_PACKAGE_VERSION.split("."))
    newer_version = f"{major}.{minor}.{patch + 1}"
    _write_test_pi_install(pi_prefix, pi_version=newer_version, root_pi_version=newer_version)

    monkeypatch.setattr(pi_client, "ensure_node_binary", lambda: tmp_path / "node")
    monkeypatch.setattr(
        pi_client,
        "_resolve_npm_binary",
        lambda: (_ for _ in ()).throw(AssertionError("newer Pi must not be replaced")),
    )

    assert pi_client.ensure_pi_binary() == str(pi_client.PI_BIN)
    assert pi_client._installed_pi_version(pi_prefix) == newer_version
