from __future__ import annotations

import io
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from syke.llm import pi_install as install
from syke.llm import pi_update as update
from syke.llm.pi_update import _probe_pi_runtime
from tests.test_pi_install import (
    _patch_pi_install_paths,
    _write_fake_pi_install_binaries,
    _write_test_pi_install,
)


@pytest.fixture(name="runtime")
def _runtime(tmp_path: Path, monkeypatch):
    prefix, _, _ = _patch_pi_install_paths(monkeypatch, tmp_path / "syke-home")
    _write_test_pi_install(prefix, pi_version=install.PI_PACKAGE_VERSION)
    node, npm = _write_fake_pi_install_binaries(tmp_path)
    npm_log = tmp_path / "npm.log"
    monkeypatch.setenv("PATH", f"{npm.parent}:/usr/bin:/bin")
    monkeypatch.setenv("FAKE_NPM_LOG", str(npm_log))
    monkeypatch.delenv("FAKE_PI_VERSION", raising=False)
    monkeypatch.setattr(install, "_NODE_CANDIDATES", (node,))
    monkeypatch.setattr(install, "_NPM_CANDIDATES", (npm,))
    monkeypatch.setattr(
        update,
        "_fetch_release",
        lambda version=None: {
            "version": version or "1.0.0",
            "schemaSpec": "^1.3.27",
        },
    )
    probes = []
    monkeypatch.setattr(update, "_probe_pi_runtime", lambda node, prefix: probes.append(prefix))
    install._write_pi_launcher(node, prefix)
    return SimpleNamespace(prefix=prefix, node=node, log=npm_log, probes=probes)


def test_status_and_check_do_not_install_launch_or_create_a_selection(runtime, monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("read-only inspection must not install or launch Pi")

    monkeypatch.setattr(install, "ensure_node_binary", forbidden)
    monkeypatch.setattr(install, "_install_pi_runtime", forbidden)
    monkeypatch.setattr(update, "_probe_pi_runtime", forbidden)
    status = update.get_pi_runtime_status()
    checked = update.check_pi_update()
    assert status["version"] == "0.87.1"
    assert status["healthy"] is True
    assert checked["targetVersion"] == "1.0.0"
    assert checked["updateAvailable"] is True
    assert not install._runtime_state_path().exists()
    assert not runtime.log.exists()


def test_fresh_update_selects_latest_without_installing_the_bootstrap_default(runtime) -> None:
    install._remove_install_path(runtime.prefix)
    install.PI_BIN.unlink()
    result = update.update_pi_runtime()
    assert result["version"] == "1.0.0"
    assert result["previousVersion"] is None
    assert len(runtime.log.read_text().splitlines()) == 1
    assert not runtime.prefix.exists()
    assert install.ensure_pi_binary() == str(install.PI_BIN)
    assert len(runtime.log.read_text().splitlines()) == 1


def test_broken_legacy_symlink_is_not_automatically_replaced(runtime) -> None:
    install._remove_install_path(runtime.prefix)
    target = runtime.prefix.parent / "missing-user-runtime"
    runtime.prefix.symlink_to(target)
    with pytest.raises(RuntimeError, match="local choice was not replaced"):
        install.ensure_pi_binary()
    assert runtime.prefix.is_symlink()
    assert runtime.prefix.resolve() == target
    assert not runtime.log.exists()


def test_status_of_missing_runtime_does_not_bootstrap(tmp_path, monkeypatch) -> None:
    prefix, _, _ = _patch_pi_install_paths(monkeypatch, tmp_path / "syke-home")
    status = update.get_pi_runtime_status()
    assert status["version"] is None
    assert status["healthy"] is False
    assert status["error"] is None
    assert not prefix.parent.exists()


def test_update_installs_exact_local_pins_and_preserves_auth_and_old_runtime(runtime) -> None:
    agent = runtime.prefix.parent / "pi-agent"
    agent.mkdir()
    auth = agent / "auth.json"
    settings = agent / "settings.json"
    auth.write_text('{"private":"unchanged"}')
    settings.write_text('{"model":"custom"}')
    result = update.update_pi_runtime()
    selected = install.active_pi_prefix()
    assert result["changed"] is True
    assert result["version"] == "1.0.0"
    assert result["previousVersion"] == "0.87.1"
    assert selected.parent == install._runtime_store_path()
    assert runtime.prefix.exists()
    assert runtime.probes == [selected]
    assert auth.read_text() == '{"private":"unchanged"}'
    assert settings.read_text() == '{"model":"custom"}'
    install._validate_pi_install(selected)
    dependencies = json.loads((selected / "package.json").read_text())["dependencies"]
    assert dependencies[install.PI_PACKAGE] == "1.0.0"
    args = json.loads(runtime.log.read_text().splitlines()[0])
    assert f"{install.PI_PACKAGE}@1.0.0" in args
    assert "typebox@^1.3.27" in args
    assert "--save-exact" in args and "--ignore-scripts" in args
    assert "--package-lock=true" in args and "--engine-strict" in args
    assert (
        str(selected / "node_modules" / install.PI_PACKAGE / "dist" / "cli.js")
        in install.PI_BIN.read_text()
    )


def test_boot_and_syke_default_changes_do_not_replace_local_choice(runtime, monkeypatch) -> None:
    update.update_pi_runtime()
    selected = install.active_pi_prefix()
    calls = runtime.log.read_text()
    monkeypatch.setattr(install, "PI_PACKAGE_VERSION", "2.0.0")
    monkeypatch.setattr(install, "PI_SCHEMA_VERSION", "9.0.0")
    assert install.ensure_pi_binary() == str(install.PI_BIN)
    assert install.active_pi_prefix() == selected
    assert install._installed_pi_version(selected) == "1.0.0"
    assert install.get_pi_version() == "shim-pi-version"
    assert runtime.log.read_text() == calls


def test_invalid_existing_install_is_not_automatically_repaired(runtime) -> None:
    (runtime.prefix / "package.json").unlink()
    with pytest.raises(RuntimeError, match="local choice was not replaced"):
        install.ensure_pi_binary()
    assert not runtime.log.exists()
    assert not (runtime.prefix / "package.json").exists()


@pytest.mark.parametrize("current", ["1.0.0", "1.0.0+local", "2.0.0", "2.0.0-rc.1"])
def test_latest_update_is_idempotent_and_never_implicitly_downgrades(runtime, current) -> None:
    _write_test_pi_install(runtime.prefix, pi_version=current)
    result = update.update_pi_runtime()
    assert result["changed"] is False
    assert result["version"] == current
    assert not runtime.log.exists()
    assert not runtime.probes
    assert update.check_pi_update()["updateAvailable"] is False


def test_explicit_older_release_is_allowed(runtime) -> None:
    result = update.update_pi_runtime("0.86.0")
    assert result["changed"] is True
    assert result["version"] == "0.86.0"
    assert result["previousVersion"] == "0.87.1"


def test_final_release_replaces_prerelease_of_same_version(runtime) -> None:
    _write_test_pi_install(runtime.prefix, pi_version="1.0.0-rc.1")
    assert update.check_pi_update()["updateAvailable"] is True
    assert update.update_pi_runtime()["version"] == "1.0.0"


@pytest.mark.parametrize("phase", ["install", "probe", "guard", "state"])
def test_failure_preserves_selection_launcher_old_runtime_and_auth(
    runtime, monkeypatch, phase
) -> None:
    original_launcher = install.PI_BIN.read_bytes()
    auth = runtime.prefix.parent / "pi-agent" / "auth.json"
    auth.parent.mkdir()
    auth.write_text("secret")
    entered = []

    def fail(*args, **kwargs):
        raise RuntimeError(f"failed {phase}")

    @contextmanager
    def guard():
        entered.append(True)
        if phase == "guard":
            fail()
        yield

    if phase == "install":
        monkeypatch.setattr(install, "_install_pi_runtime", fail)
    elif phase == "probe":
        monkeypatch.setattr(update, "_probe_pi_runtime", fail)
    elif phase == "state":
        original_write = install._write_runtime_state

        def broken_write(*args):
            original_write(*args)
            fail()

        monkeypatch.setattr(install, "_write_runtime_state", broken_write)
    with pytest.raises(RuntimeError, match=f"failed {phase}"):
        update.update_pi_runtime(activation_guard=guard)
    assert install.active_pi_prefix() == runtime.prefix
    assert not install._runtime_state_path().exists()
    assert install.PI_BIN.read_bytes() == original_launcher
    assert auth.read_text() == "secret"
    assert install._installed_pi_version(runtime.prefix) == "0.87.1"
    assert not install._runtime_store_path().exists() or not list(
        install._runtime_store_path().iterdir()
    )
    if phase in {"install", "probe"}:
        assert not entered


def test_activation_restores_existing_selection_after_write_failure(runtime, monkeypatch) -> None:
    update.update_pi_runtime()
    state = install._runtime_state_path().read_bytes()
    launcher = install.PI_BIN.read_bytes()
    current = install.active_pi_prefix()
    original_write = install._write_runtime_state

    def broken_write(*args):
        original_write(*args)
        raise OSError("cannot finish state write")

    monkeypatch.setattr(install, "_write_runtime_state", broken_write)
    with pytest.raises(OSError, match="state write"):
        update.update_pi_runtime("1.1.0")
    assert install._runtime_state_path().read_bytes() == state
    assert install.PI_BIN.read_bytes() == launcher
    assert install.active_pi_prefix() == current


def test_failed_activation_restores_a_symlink_launcher(runtime, monkeypatch, tmp_path) -> None:
    original = tmp_path / "previous-pi"
    original.write_text("previous launcher")
    install.PI_BIN.unlink()
    install.PI_BIN.symlink_to(original)
    monkeypatch.setattr(
        install,
        "_write_runtime_state",
        lambda *args: (_ for _ in ()).throw(OSError("state failure")),
    )
    with pytest.raises(OSError, match="state failure"):
        update.update_pi_runtime()
    assert install.PI_BIN.is_symlink()
    assert install.PI_BIN.resolve() == original


def test_rollback_is_offline_and_can_toggle_the_previous_selection(runtime, monkeypatch) -> None:
    update.update_pi_runtime()
    newer = install.active_pi_prefix()
    calls = runtime.log.read_text()
    monkeypatch.setattr(
        update, "_fetch_release", lambda *args: pytest.fail("rollback must be offline")
    )
    result = update.rollback_pi_runtime()
    assert result["version"] == "0.87.1"
    assert result["previousVersion"] == "1.0.0"
    assert install.active_pi_prefix() == runtime.prefix
    assert update.rollback_pi_runtime()["version"] == "1.0.0"
    assert install.active_pi_prefix() == newer
    assert runtime.log.read_text() == calls


def test_rollback_probe_failure_keeps_current_choice(runtime, monkeypatch) -> None:
    update.update_pi_runtime()
    state = install._runtime_state_path().read_bytes()
    launcher = install.PI_BIN.read_bytes()
    monkeypatch.setattr(
        update,
        "_probe_pi_runtime",
        lambda *args: (_ for _ in ()).throw(RuntimeError("incompatible")),
    )
    with pytest.raises(RuntimeError, match="incompatible"):
        update.rollback_pi_runtime()
    assert install._runtime_state_path().read_bytes() == state
    assert install.PI_BIN.read_bytes() == launcher


def test_rollback_without_previous_version_fails_without_installing(runtime) -> None:
    with pytest.raises(RuntimeError, match="No previous"):
        update.rollback_pi_runtime()
    assert not runtime.log.exists()


def test_explicit_update_repairs_missing_selection_and_keeps_known_good_previous(runtime) -> None:
    update.update_pi_runtime()
    missing = install.active_pi_prefix()
    install._remove_install_path(missing)
    assert update.get_pi_runtime_status()["error"]
    result = update.update_pi_runtime()
    assert result["version"] == "1.0.0"
    assert result["previousVersion"] == "0.87.1"
    assert install.active_pi_prefix() != missing


def test_daemon_resume_runs_after_releasing_install_lock(runtime) -> None:
    events = []

    @contextmanager
    def guard():
        events.append("stop")
        yield
        with install._runtime_install_lock(timeout=0):
            events.append("restart")
        with pytest.raises(RuntimeError, match="operation is in progress"):
            with install._runtime_install_lock(timeout=0, update=True):
                pytest.fail("another update must not overlap the daemon restart")
        assert install.ensure_pi_binary() == str(install.PI_BIN)

    update.update_pi_runtime(activation_guard=guard)
    update.rollback_pi_runtime(activation_guard=guard)
    assert events == ["stop", "restart", "stop", "restart"]


def test_daemon_restart_failure_does_not_delete_the_committed_runtime(runtime) -> None:
    @contextmanager
    def guard():
        yield
        raise RuntimeError("daemon restart failed")

    with pytest.raises(RuntimeError, match="daemon restart failed"):
        update.update_pi_runtime(activation_guard=guard)
    selected = install.active_pi_prefix()
    assert selected.exists()
    assert install._installed_pi_version(selected) == "1.0.0"
    assert install.ensure_pi_binary() == str(install.PI_BIN)


def test_runtime_lock_prevents_concurrent_switches_and_is_released(runtime) -> None:
    with install._runtime_install_lock():
        with pytest.raises(RuntimeError, match="operation is in progress"):
            with install._runtime_install_lock(timeout=0):
                pytest.fail("second operation entered")
    with install._runtime_install_lock(timeout=0):
        pass


@pytest.mark.parametrize(
    "state",
    [
        "not json",
        '{"schemaVersion":2,"active":"pi"}',
        '{"schemaVersion":true,"active":"pi"}',
        '{"schemaVersion":1,"active":"../elsewhere"}',
        '{"schemaVersion":1,"active":"pi-runtimes/.."}',
    ],
)
def test_invalid_state_is_reported_and_never_overwritten(runtime, state) -> None:
    install._runtime_state_path().write_text(state)
    assert update.get_pi_runtime_status()["error"]
    with pytest.raises(RuntimeError):
        update.update_pi_runtime()
    assert install._runtime_state_path().read_text() == state
    assert not runtime.log.exists()


@pytest.mark.parametrize(
    "version,valid", [("latest", False), ("^1.0.0", False), ("1.0.0", True), ("1.0.0-rc.1", True)]
)
def test_registry_requires_exact_requested_releases(monkeypatch, version, valid) -> None:
    metadata = {
        "name": install.PI_PACKAGE,
        "version": version,
        "dependencies": {"typebox": "^1.3.27"},
    }
    calls = []

    def fetch(url, timeout):
        calls.append(url)
        assert timeout == 20
        return io.BytesIO(json.dumps(metadata).encode())

    monkeypatch.setattr(update, "urlopen", fetch)
    if valid:
        assert update._fetch_release(version)["version"] == version
        assert "%40earendil-works%2Fpi-coding-agent" in calls[0]
    else:
        with pytest.raises(RuntimeError, match="exact version"):
            update._fetch_release(version)
        assert not calls


@pytest.mark.parametrize(
    "metadata",
    [
        {"name": "other", "version": "1.0.0"},
        {"name": install.PI_PACKAGE, "version": "latest"},
        {
            "name": install.PI_PACKAGE,
            "version": "1.0.0-rc.1",
            "dependencies": {"typebox": "1.3.27"},
        },
        {"name": install.PI_PACKAGE, "version": "1.0.0", "dependencies": {}},
        {
            "name": install.PI_PACKAGE,
            "version": "1.0.0",
            "dependencies": {"typebox": "https://example.com/package.tgz"},
        },
    ],
)
def test_registry_rejects_unexpected_or_incompatible_metadata(monkeypatch, metadata) -> None:
    monkeypatch.setattr(
        update, "urlopen", lambda *args, **kwargs: io.BytesIO(json.dumps(metadata).encode())
    )
    with pytest.raises(RuntimeError):
        update._fetch_release()


def test_registry_network_failure_is_actionable(monkeypatch) -> None:
    from urllib.error import URLError

    def offline(*args, **kwargs):
        raise URLError("offline")

    monkeypatch.setattr(update, "urlopen", offline)
    with pytest.raises(RuntimeError, match="Could not check Pi latest"):
        update._fetch_release()


def test_probe_uses_real_syke_flags_and_never_inherits_credentials(runtime, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-used")
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", "/private/user/credentials")
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if "--input-type=module" in cmd:
            return SimpleNamespace(
                returncode=0, stdout='{"model":"gpt-5","provider":"openai"}', stderr=""
            )
        replies = [
            {"id": name, "type": "response", "success": True, "data": {}}
            for name in ("get_state", "get_available_models")
        ]
        return SimpleNamespace(
            returncode=0, stdout="\n".join(json.dumps(r) for r in replies), stderr=""
        )

    monkeypatch.setattr(update.subprocess, "run", run)
    # The imported function bypasses the update-flow fixture's probe stub.
    _probe_pi_runtime(runtime.node, runtime.prefix)
    assert len(calls) == 2
    for _, kwargs in calls:
        env = kwargs["env"]
        assert "OPENAI_API_KEY" not in env
        assert "SYKE_PI_AGENT_DIR" not in env
        assert env["PI_CODING_AGENT_DIR"] != "/private/user/credentials"
    args = calls[1][0]
    assert "--offline" in args
    assert "--no-builtin-tools" in args
    assert "--no-context-files" in args
    assert "--no-extensions" in args
    assert "--skill" in args
    assert "get_state" in calls[1][1]["input"]
    assert "get_available_models" in calls[1][1]["input"]


@pytest.mark.parametrize(
    "left,right",
    [
        ("1.0.0-rc.2", "1.0.0-rc.10"),
        ("1.0.0-rc.10", "1.0.0"),
        ("1.0.0", "1.1.0-alpha"),
        ("1.9.9", "1.10.0"),
    ],
)
def test_semver_precedence(left, right) -> None:
    assert update._version_key(left) < update._version_key(right)
    assert update._version_key(left + "+local") == update._version_key(left)
