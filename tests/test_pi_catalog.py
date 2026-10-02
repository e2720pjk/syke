from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from syke.llm import pi_catalog, pi_install


def test_load_pi_catalog_parses_provider_requirements(monkeypatch, tmp_path: Path) -> None:
    payload = json.dumps(
        [
            {
                "id": "azure-openai-responses",
                "models": ["gpt-5.4-mini"],
                "availableModels": ["gpt-5.4-mini"],
                "defaultModel": "gpt-5.4-mini",
                "oauth": False,
                "oauthName": None,
                "requiresBaseUrl": True,
            },
            {
                "id": "openai",
                "models": ["gpt-5.4"],
                "availableModels": [],
                "defaultModel": "gpt-5.4",
                "oauth": False,
                "oauthName": None,
                "requiresBaseUrl": False,
            },
        ]
    )
    node = tmp_path / "node"
    node.write_text(
        f"#!{sys.executable}\nprint({payload!r})\n",
        encoding="utf-8",
    )
    node.chmod(0o755)
    pi_install._package_path(tmp_path, pi_install.PI_PACKAGE).mkdir(parents=True)
    monkeypatch.setattr(pi_install, "PI_LOCAL_PREFIX", tmp_path)
    monkeypatch.setattr(pi_install, "ensure_node_binary", lambda: node)

    entries = pi_catalog._load_pi_catalog()

    assert entries[0].id == "azure-openai-responses"
    assert entries[0].available_models == ("gpt-5.4-mini",)
    assert entries[0].requires_base_url is True
    assert entries[1].id == "openai"
    assert entries[1].requires_base_url is False


@pytest.mark.parametrize("thinking", ["xhigh", "max"])
def test_match_pi_model_pattern_preserves_every_pi_thinking_suffix(thinking: str) -> None:
    resolved = pi_catalog._match_pi_model_pattern(
        "openai", f"gpt-5.6-luna:{thinking}", ("gpt-5.6-luna", "gpt-5.6-luna-pro")
    )
    assert resolved == f"gpt-5.6-luna:{thinking}"


def test_match_pi_model_pattern_keeps_unknown_suffix_in_model_id() -> None:
    resolved = pi_catalog._match_pi_model_pattern("openai", "gpt-5.6-luna:wild", ("gpt-5.6-luna",))
    assert resolved is None


def test_resolve_pi_model_uses_pi_provider_default_when_no_explicit_model(monkeypatch) -> None:
    monkeypatch.setattr(
        pi_catalog,
        "_get_active_provider_spec",
        lambda: SimpleNamespace(id="kimi-coding"),
    )
    monkeypatch.setattr(pi_catalog, "get_default_model", lambda: None)
    monkeypatch.setattr(
        pi_catalog,
        "_load_pi_provider_default_model",
        lambda provider_name: "kimi-k2-thinking",
    )

    assert pi_catalog.resolve_pi_model() == "kimi-k2-thinking"


def test_resolve_pi_model_allows_explicit_provider_model_not_yet_in_pi_catalog(
    monkeypatch,
) -> None:
    monkeypatch.setattr(pi_catalog, "_get_active_provider_spec", lambda: SimpleNamespace(id="zai"))
    monkeypatch.setattr(pi_catalog, "get_default_model", lambda: "glm-5.1")
    monkeypatch.setattr(
        pi_catalog,
        "_load_pi_provider_model_ids",
        lambda provider_name: ("glm-5", "glm-5-turbo"),
    )

    assert pi_catalog.resolve_pi_model() == "glm-5.1"


def test_probe_and_node_script_use_the_bounded_child_environment(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    monkeypatch.setenv("OPENAI_API_KEY", "host-openai")
    monkeypatch.setenv("UNSAFE_SECRET", "must-not-leak")
    monkeypatch.setattr(pi_install, "PI_LOCAL_PREFIX", tmp_path)
    monkeypatch.setattr(pi_install, "ensure_pi_binary", lambda: "/tmp/pi")
    monkeypatch.setattr(pi_install, "ensure_node_binary", lambda: tmp_path / "node")

    def run(cmd, **kwargs):
        if cmd == ["getconf", "DARWIN_USER_TEMP_DIR"]:
            return subprocess.CompletedProcess(cmd, 1, "", "unavailable")
        calls.append((cmd, kwargs["env"]))
        stdout = "ping" if cmd[0] == "/tmp/pi" else "{}"
        return subprocess.CompletedProcess(cmd, 0, stdout, "")

    monkeypatch.setattr(pi_catalog.subprocess, "run", run)

    ok, _ = pi_catalog.probe_pi_provider_connection("openai", "gpt-5.4")
    result = pi_catalog._run_pi_node_script("console.log('ok')")

    assert ok is True
    assert result.returncode == 0
    probe_env, script_env = calls[0][1], calls[1][1]
    assert probe_env["OPENAI_API_KEY"] == "host-openai"
    assert probe_env["PI_CODING_AGENT_DIR"] == script_env["PI_CODING_AGENT_DIR"]
    assert "UNSAFE_SECRET" not in probe_env
    assert "UNSAFE_SECRET" not in script_env


def test_oauth_login_uses_bounded_env(monkeypatch, tmp_path: Path) -> None:
    capture = tmp_path / "oauth.json"
    node = tmp_path / "node"
    node.write_text(
        f"""#!{sys.executable}
import json
import os
import sys
from pathlib import Path

Path(os.environ["CAPTURE_PATH"]).write_text(json.dumps({{
    "provider": os.environ.get("SYKE_PI_LOGIN_PROVIDER"),
    "method": os.environ.get("SYKE_PI_LOGIN_METHOD"),
    "openai_key": os.environ.get("OPENAI_API_KEY"),
    "unsafe_present": "UNSAFE_SECRET" in os.environ,
    "args": sys.argv[1:3],
    "opens_browser": "openBrowser(event.url)" in sys.argv[3],
    "selects_method": "normalizeMethod(option.id) === requested" in sys.argv[3],
}}), encoding="utf-8")
""",
        encoding="utf-8",
    )
    node.chmod(0o755)
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    monkeypatch.setenv("UNSAFE_SECRET", "should-not-leak")
    monkeypatch.setenv("OPENAI_API_KEY", "host-openai")
    monkeypatch.setenv("CAPTURE_PATH", str(capture))
    monkeypatch.setenv("SYKE_PI_PASSTHROUGH_ENV", "CAPTURE_PATH")
    monkeypatch.setattr(pi_install, "PI_LOCAL_PREFIX", tmp_path)
    monkeypatch.setattr(pi_install, "ensure_node_binary", lambda: node)

    pi_catalog.run_pi_oauth_login("openai", method="device-code")

    observed = json.loads(capture.read_text(encoding="utf-8"))
    assert observed == {
        "provider": "openai",
        "method": "device-code",
        "openai_key": "host-openai",
        "unsafe_present": False,
        "args": ["--input-type=module", "-e"],
        "opens_browser": True,
        "selects_method": True,
    }


def test_oauth_login_rejects_unknown_interaction_method() -> None:
    with pytest.raises(ValueError, match="Unsupported Pi login method"):
        pi_catalog.run_pi_oauth_login("openai", method="magic")


def test_prepare_host_oauth_uses_trusted_process_and_fails_closed(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    responses = iter(
        (
            subprocess.CompletedProcess([], 0, '{"status":"ready"}', ""),
            subprocess.CompletedProcess([], 2, "", "refresh unavailable"),
        )
    )
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    monkeypatch.setattr(pi_install, "PI_LOCAL_PREFIX", tmp_path)
    monkeypatch.setattr(
        pi_catalog,
        "load_pi_auth",
        lambda: {"openai-codex": {"type": "oauth", "access": "secret"}},
    )
    monkeypatch.setattr(pi_install, "ensure_pi_binary", lambda: "/test/pi")

    def run(cmd, **kwargs):
        if cmd == ["getconf", "DARWIN_USER_TEMP_DIR"]:
            return subprocess.CompletedProcess(cmd, 1, "", "unavailable")
        calls.append((cmd, kwargs))
        response = next(responses)
        return subprocess.CompletedProcess(
            cmd, response.returncode, response.stdout, response.stderr
        )

    monkeypatch.setattr(pi_catalog.subprocess, "run", run)

    pi_catalog._prepare_host_oauth_for_runtime("openai-codex")
    with pytest.raises(RuntimeError, match="refresh unavailable"):
        pi_catalog._prepare_host_oauth_for_runtime("openai-codex")

    assert calls[0][0] == [
        "/test/pi",
        "auth",
        "check",
        "--provider",
        "openai-codex",
        "--json",
    ]
    assert "secret" not in str(calls)
