from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from syke.entrypoint import cli
from syke.llm import pi_install, pi_update
from tests.test_pi_update import _runtime  # noqa: F401 -- shared isolated runtime fixture


def test_pi_commands_are_discoverable_in_top_level_help() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Manage your private Pi runtime" in result.output
    result = CliRunner().invoke(cli, ["pi", "--help"])
    assert result.exit_code == 0
    for command in ("status", "update", "rollback"):
        assert command in result.output


def test_pi_status_json_reports_local_choice_without_starting_runtime(runtime, monkeypatch) -> None:
    monkeypatch.setattr(
        pi_install, "ensure_pi_binary", lambda: pytest.fail("status must not start Pi")
    )
    result = CliRunner().invoke(cli, ["pi", "status", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["version"] == "0.87.1"
    assert payload["healthy"] is True
    assert not runtime.log.exists()


def test_pi_update_check_json_is_read_only(runtime, monkeypatch) -> None:
    monkeypatch.setattr(
        pi_install, "ensure_node_binary", lambda: pytest.fail("check must not launch Node")
    )
    result = CliRunner().invoke(cli, ["pi", "update", "--check", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["targetVersion"] == "1.0.0"
    assert payload["updateAvailable"] is True
    assert not runtime.log.exists()
    assert not pi_install._runtime_state_path().exists()


def test_pi_update_and_rollback_work_without_auth_or_daemon_registration(
    runtime, monkeypatch
) -> None:
    monkeypatch.setattr(
        "syke.cli_support.daemon_state.daemon_payload",
        lambda: {"running": False, "registered": False},
    )
    runner = CliRunner()
    updated = runner.invoke(cli, ["pi", "update"])
    assert updated.exit_code == 0, updated.output
    assert "0.87.1 -> 1.0.0" in updated.output
    rolled = runner.invoke(cli, ["pi", "rollback"])
    assert rolled.exit_code == 0, rolled.output
    assert "rolled back to 0.87.1" in rolled.output


def test_explicit_version_and_no_restart_flags_are_forwarded(runtime, monkeypatch) -> None:
    monkeypatch.setattr(
        "syke.cli_support.daemon_state.daemon_payload",
        lambda: pytest.fail("--no-restart must not inspect or stop the daemon"),
    )
    result = CliRunner().invoke(cli, ["pi", "update", "--version", "0.86.0", "--no-restart"])
    assert result.exit_code == 0, result.output
    assert "0.87.1 -> 0.86.0" in result.output
    assert "Restart a running daemon" in result.output
    result = CliRunner().invoke(cli, ["pi", "rollback", "--no-restart", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["version"] == "0.87.1"


@pytest.mark.parametrize("version", ["latest", "^1.0.0", "https://example.com/pi.tgz", "../pi"])
def test_invalid_explicit_version_is_a_usage_error(runtime, version) -> None:
    result = CliRunner().invoke(cli, ["pi", "update", "--version", version])
    assert result.exit_code == 2
    assert "exact version" in result.output
    assert not runtime.log.exists()


def test_network_failure_has_runtime_exit_code_and_preserves_local_install(
    runtime, monkeypatch
) -> None:
    def offline(*args):
        raise RuntimeError("npm registry is offline")

    monkeypatch.setattr(pi_update, "_fetch_release", offline)
    result = CliRunner().invoke(cli, ["pi", "update"])
    assert result.exit_code == 4
    assert "npm registry is offline" in result.output
    assert pi_install.active_pi_prefix() == runtime.prefix
    assert not runtime.log.exists()


def test_status_json_reports_corruption_with_runtime_exit_code(runtime) -> None:
    (runtime.prefix / "package.json").unlink()
    result = CliRunner().invoke(cli, ["pi", "status", "--json"])
    assert result.exit_code == 4
    assert json.loads(result.output)["error"]
    assert not runtime.log.exists()


def test_missing_rollback_has_runtime_exit_code(runtime) -> None:
    result = CliRunner().invoke(cli, ["pi", "rollback"])
    assert result.exit_code == 4
    assert "No previous Pi runtime" in result.output


def test_daemon_is_paused_only_after_probe_and_resumed_after_unlock(runtime, monkeypatch) -> None:
    from syke.cli_support import daemon_state
    from syke.daemon import daemon

    events = []
    monkeypatch.setattr(
        daemon_state, "daemon_payload", lambda: {"running": True, "registered": True}
    )
    monkeypatch.setattr(daemon, "stop_and_unload", lambda: events.append("stop"))
    monkeypatch.setattr(
        daemon_state,
        "wait_for_daemon_shutdown",
        lambda user: {"running": False, "registered": False},
    )
    monkeypatch.setattr(
        daemon_state, "wait_for_daemon_startup", lambda user: {"running": True, "ipc": {"ok": True}}
    )
    monkeypatch.setattr(pi_update, "_probe_pi_runtime", lambda *args: events.append("probe"))

    def start(user):
        with pi_install._runtime_install_lock(timeout=0):
            events.append("start")
        assert pi_install.ensure_pi_binary() == str(pi_install.PI_BIN)

    monkeypatch.setattr(daemon, "install_and_start", start)
    result = CliRunner().invoke(cli, ["pi", "update", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["version"] == "1.0.0"
    assert events == ["probe", "stop", "start"]


def test_foreground_daemon_requires_explicit_no_restart(runtime, monkeypatch) -> None:
    monkeypatch.setattr(
        "syke.cli_support.daemon_state.daemon_payload",
        lambda: {"running": True, "registered": False},
    )
    result = CliRunner().invoke(cli, ["pi", "update"])
    assert result.exit_code == 4
    assert "foreground daemon" in result.output
    assert "--no-restart" in result.output
    assert pi_install.active_pi_prefix() == runtime.prefix


def test_daemon_restart_failure_reports_recovery_commands(runtime, monkeypatch) -> None:
    from syke.cli_support import daemon_state
    from syke.daemon import daemon

    monkeypatch.setattr(
        daemon_state, "daemon_payload", lambda: {"running": True, "registered": True}
    )
    monkeypatch.setattr(daemon, "stop_and_unload", lambda: None)
    monkeypatch.setattr(
        daemon_state,
        "wait_for_daemon_shutdown",
        lambda user: {"running": False, "registered": False},
    )
    monkeypatch.setattr(daemon, "install_and_start", lambda user: None)
    monkeypatch.setattr(
        daemon_state,
        "wait_for_daemon_startup",
        lambda user: {"running": False, "ipc": {"ok": False}},
    )
    result = CliRunner().invoke(cli, ["pi", "update"])
    assert result.exit_code == 4
    assert "syke pi status" in result.output
    assert "syke pi rollback" in result.output
    selected = pi_install.active_pi_prefix()
    assert pi_install._installed_pi_version(selected) == "1.0.0"
    assert selected.exists()


def test_daemon_is_restored_if_activation_fails(runtime, monkeypatch) -> None:
    from syke.cli_support import daemon_state
    from syke.daemon import daemon

    events = []
    monkeypatch.setattr(
        daemon_state, "daemon_payload", lambda: {"running": True, "registered": True}
    )
    monkeypatch.setattr(daemon, "stop_and_unload", lambda: events.append("stop"))
    monkeypatch.setattr(
        daemon_state,
        "wait_for_daemon_shutdown",
        lambda user: {"running": False, "registered": False},
    )
    monkeypatch.setattr(daemon, "install_and_start", lambda user: events.append("restore"))
    monkeypatch.setattr(
        daemon_state, "wait_for_daemon_startup", lambda user: {"running": True, "ipc": {"ok": True}}
    )
    monkeypatch.setattr(
        pi_install,
        "_write_runtime_state",
        lambda *args: (_ for _ in ()).throw(OSError("state failure")),
    )
    result = CliRunner().invoke(cli, ["pi", "update"])
    assert result.exit_code == 4
    assert events == ["stop", "restore"]
    assert pi_install.active_pi_prefix() == runtime.prefix


def test_unclean_daemon_shutdown_does_not_activate(runtime, monkeypatch) -> None:
    from syke.cli_support import daemon_state
    from syke.daemon import daemon

    monkeypatch.setattr(
        daemon_state, "daemon_payload", lambda: {"running": True, "registered": True}
    )
    monkeypatch.setattr(daemon, "stop_and_unload", lambda: None)
    monkeypatch.setattr(
        daemon_state,
        "wait_for_daemon_shutdown",
        lambda user: {"running": True, "registered": False},
    )
    result = CliRunner().invoke(cli, ["pi", "update"])
    assert result.exit_code == 4
    assert "did not stop cleanly" in result.output
    assert pi_install.active_pi_prefix() == runtime.prefix
