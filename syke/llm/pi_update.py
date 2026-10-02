"""User-controlled Pi releases, isolated probes, and local runtime selection."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import urlopen

from syke.llm import pi_install as install

ActivationGuard = Callable[[], AbstractContextManager[None]]


def _version_key(version: str) -> tuple[Any, ...]:
    """Semver precedence, including prereleases; build metadata is ignored."""
    core, _, pre = version.split("+", 1)[0].partition("-")
    identifiers = (
        tuple((0, int(p)) if p.isdigit() else (1, p) for p in pre.split(".")) if pre else ()
    )
    return (*map(int, core.split(".")), not bool(pre), identifiers)


def _fetch_release(version: str | None = None) -> dict[str, str]:
    if version is not None and install._EXACT_VERSION.fullmatch(version) is None:
        raise RuntimeError("--version must be an exact version, for example 1.0.0 or 1.1.0-rc.1")
    target = version or "latest"
    url = (
        f"https://registry.npmjs.org/{quote(install.PI_PACKAGE, safe='')}/{quote(target, safe='')}"
    )
    try:
        with urlopen(url, timeout=20) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise RuntimeError("Pi registry metadata exceeds the size limit")
        metadata = json.loads(raw)
    except (URLError, OSError, ValueError) as exc:
        raise RuntimeError(f"Could not check Pi {target} in the npm registry: {exc}") from exc
    if not isinstance(metadata, dict) or metadata.get("name") != install.PI_PACKAGE:
        raise RuntimeError("The npm registry returned unexpected Pi package metadata")
    release = metadata.get("version")
    if not isinstance(release, str) or install._EXACT_VERSION.fullmatch(release) is None:
        raise RuntimeError("The npm registry did not return an exact Pi release version")
    if version is not None and release != version:
        raise RuntimeError(f"Requested Pi {version}, but the registry returned {release}")
    if version is None and "-" in release.split("+", 1)[0]:
        raise RuntimeError("The latest Pi tag is a prerelease; select it explicitly with --version")
    dependencies = metadata.get("dependencies")
    schema_spec = (
        dependencies.get(install.PI_SCHEMA_PACKAGE) if isinstance(dependencies, dict) else None
    )
    if (
        not isinstance(schema_spec, str)
        or re.fullmatch(r"[0-9A-Za-z.*+^~|<>= -]+", schema_spec) is None
        or not any(char.isdigit() for char in schema_spec)
    ):
        raise RuntimeError("This Pi release has no supported typebox dependency for Syke's tools")
    return {"version": release, "schemaSpec": schema_spec}


def get_pi_runtime_status() -> dict[str, Any]:
    """Inspect local metadata only: no install, Node launch, or credential access."""
    error = None
    previous = None
    prefix = install.PI_LOCAL_PREFIX
    selected = False
    try:
        state = install._read_runtime_state()
        prefix = install._runtime_prefix_from_name(state["active"]) if state else prefix
        selected = True
        if state and state.get("previous") is not None:
            previous = install._runtime_prefix_from_name(state["previous"])
        if prefix.exists():
            install._validate_pi_install(prefix)
            healthy = True
        else:
            healthy = False
            if state:
                error = f"Selected Pi runtime is missing at {prefix}"
    except RuntimeError as exc:
        healthy = False
        error = str(exc)
    return {
        "version": install._installed_pi_version(prefix) if selected else None,
        "defaultVersion": install.PI_PACKAGE_VERSION,
        "runtimePath": str(prefix),
        "selectionPath": str(install._runtime_state_path()),
        "previousVersion": install._installed_pi_version(previous) if previous else None,
        "previousPath": str(previous) if previous else None,
        "healthy": healthy,
        "error": error,
    }


def check_pi_update(version: str | None = None) -> dict[str, Any]:
    status = get_pi_runtime_status()
    release = _fetch_release(version)
    current = status["version"]
    available = not status["healthy"] or current is None
    if current is not None and install._EXACT_VERSION.fullmatch(current):
        available = available or (
            current != release["version"]
            if version
            else _version_key(current) < _version_key(release["version"])
        )
    return {**status, "targetVersion": release["version"], "updateAvailable": available}


def _probe_pi_runtime(node_bin: Path, prefix: Path) -> None:
    """Check the real catalog, tool imports, and Syke RPC flags without user auth."""
    from syke.config import SYNC_THINKING_LEVEL
    from syke.llm.pi_client import _build_rpc_launch_command

    extension = install._install_pi_tool_extension(prefix)
    script = r"""
import { ModelRuntime } from "@earendil-works/pi-coding-agent";
import { defaultModelPerProvider } from
  "./node_modules/@earendil-works/pi-coding-agent/dist/core/model-resolver.js";
import register from "./syke-tools.mjs";
import { readFile } from "node:fs/promises";
const registered = {};
register({ registerTool: tool => { registered[tool.name] = tool; } });
const tools = Object.keys(registered);
const runtime = await ModelRuntime.create({ allowModelNetwork: false, refreshOnCreate: false });
await runtime.getAvailable();
const models = runtime.getModels();
if (!models.length || !runtime.getProviders().length || typeof defaultModelPerProvider !== "object"
    || typeof runtime.getAuth !== "function" || typeof runtime.login !== "function"
    || tools.sort().join(",") !== "bash,edit,read,write") {
  throw new Error("Pi catalog, authentication, or tool API is incompatible with Syke");
}
if (process.env.SYKE_PROBE_SANDBOX === "1") {
  const text = result => (result.content ?? []).map(part => part.text ?? "").join("\n");
  const execute = (name, args) => registered[name].execute("syke-probe", args);
  const file = process.env.SYKE_PROBE_FILE;
  await execute("write", { path: file, content: "SYKE_PROBE_WRITE" });
  if (!text(await execute("read", { path: file })).includes("SYKE_PROBE_WRITE")) {
    throw new Error("Pi read/write operations are incompatible");
  }
  await execute("edit", {
    path: file, edits: [{ oldText: "SYKE_PROBE_WRITE", newText: "SYKE_PROBE_EDIT" }]
  });
  if ((await readFile(file, "utf8")) !== "SYKE_PROBE_EDIT"
      || !text(await execute("bash", { command: "printf SYKE_PROBE_BASH" }))
          .includes("SYKE_PROBE_BASH")) {
    throw new Error("Pi edit/bash operations are incompatible");
  }
  const denied = async (name, args) => {
    try { return (await execute(name, args)).isError === true; }
    catch { return true; }
  };
  if (!(await denied("read", { path: process.env.SYKE_PROBE_SECRET }))
      || !(await denied("write", { path: process.env.SYKE_PROBE_BLOCKED, content: "changed" }))
      || !(await denied("edit", {
        path: process.env.SYKE_PROBE_BLOCKED, edits: [{ oldText: "LOCKED", newText: "changed" }]
      }))
      || (await readFile(process.env.SYKE_PROBE_BLOCKED, "utf8")) !== "LOCKED") {
    throw new Error("Pi file tools bypassed Syke's sandbox operations");
  }
  const secret = process.env.SYKE_PROBE_SECRET.replace(/'/g, "'\\''");
  let output = "";
  try { output = text(await execute("bash", { command: `cat '${secret}'` })); }
  catch { /* The sandbox denial may also be surfaced as an exception. */ }
  if (output.includes("SYKE_PROBE_SECRET")) {
    throw new Error("Pi bash bypassed Syke's sandbox operations");
  }
}
console.log(JSON.stringify({ model: models[0].id, provider: models[0].provider }));
"""
    with tempfile.TemporaryDirectory(prefix="syke-pi-probe-") as temporary:
        root = Path(temporary).resolve()
        for name in ("home", "agent", "tmp", "sessions"):
            (root / name).mkdir()
        env = {
            "HOME": str(root / "home"),
            "USERPROFILE": str(root / "home"),
            "PI_CODING_AGENT_DIR": str(root / "agent"),
            "TMPDIR": str(root / "tmp"),
            "TEMP": str(root / "tmp"),
            "TMP": str(root / "tmp"),
            "PATH": os.pathsep.join(
                (str(node_bin.parent), "/usr/bin", "/bin", "/usr/sbin", "/sbin")
            ),
            "NO_COLOR": "1",
            "SYKE_TOOL_SANDBOX_PROFILE": str(root / "unused-profile.sb"),
        }
        if os.environ.get("SYSTEMROOT"):
            env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
        if sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file():
            secret = root / "secret.txt"
            blocked = root / "blocked.txt"
            secret.write_text("SYKE_PROBE_SECRET")
            blocked.write_text("LOCKED")
            profile = root / "tool-probe.sb"
            profile.write_text(
                "(version 1)\n(allow default)\n"
                f"(deny file-read* (literal {json.dumps(str(secret))}))\n"
                f"(deny file-write* (literal {json.dumps(str(blocked))}))\n"
            )
            env.update(
                {
                    "SYKE_TOOL_SANDBOX_PROFILE": str(profile),
                    "SYKE_PROBE_SANDBOX": "1",
                    "SYKE_PROBE_FILE": str(root / "workspace.txt"),
                    "SYKE_PROBE_SECRET": str(secret),
                    "SYKE_PROBE_BLOCKED": str(blocked),
                }
            )
        try:
            catalog = subprocess.run(
                [str(node_bin), "--input-type=module", "-e", script],
                cwd=prefix,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if catalog.returncode:
                raise RuntimeError(
                    f"Pi API compatibility check failed: {catalog.stderr.strip()[:1000]}"
                )
            binding = json.loads(catalog.stdout)
            skill = root / "agent" / "skills" / "self-learn" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(
                "---\nname: self-learn\ndescription: Compatibility probe.\n---\nProbe only.\n"
            )
            cmd, extra_env = _build_rpc_launch_command(
                provider=binding["provider"],
                model=binding["model"],
                thinking_level=SYNC_THINKING_LEVEL,
                session_dir=root / "sessions",
                self_learn_skill_path=skill,
                tool_sandbox_profile=Path(env["SYKE_TOOL_SANDBOX_PROFILE"]),
                executable=[
                    str(node_bin),
                    str(install._package_path(prefix, install.PI_PACKAGE) / "dist" / "cli.js"),
                ],
                tool_extension=extension,
            )
            cmd.append("--offline")
            requests = [
                {"id": command, "type": command}
                for command in ("get_state", "get_available_models")
            ]
            rpc = subprocess.run(
                cmd,
                cwd=root / "home",
                env={**env, **extra_env},
                input="".join(json.dumps(request) + "\n" for request in requests),
                capture_output=True,
                text=True,
                timeout=30,
            )
            replies = {}
            for line in rpc.stdout.splitlines():
                try:
                    reply = json.loads(line)
                except ValueError:
                    continue
                if isinstance(reply, dict) and reply.get("type") == "response":
                    replies[reply.get("id")] = reply
            if rpc.returncode or any(
                replies.get(r["id"], {}).get("success") is not True for r in requests
            ):
                detail = rpc.stderr.strip() or json.dumps(replies)
                raise RuntimeError(f"Pi RPC compatibility check failed: {detail[:1000]}")
        except (subprocess.TimeoutExpired, OSError, ValueError, KeyError) as exc:
            raise RuntimeError(f"Pi compatibility check failed: {exc}") from exc


def _activate(node_bin: Path, prefix: Path, previous: Path | None) -> None:
    """Commit selection and launcher together, restoring both on write failure."""
    state_path = install._runtime_state_path()
    old_state = state_path.read_bytes() if state_path.exists() else None
    old_link = os.readlink(install.PI_BIN) if install.PI_BIN.is_symlink() else None
    old_launcher = (
        install.PI_BIN.read_bytes() if install.PI_BIN.is_file() and old_link is None else None
    )
    old_mode = stat.S_IMODE(install.PI_BIN.stat().st_mode) if old_launcher is not None else 0o755
    try:
        install._write_runtime_state(prefix, previous)
        install._write_pi_launcher(node_bin, prefix)
    except Exception:
        try:
            current_state = state_path.read_bytes() if state_path.exists() else None
            if current_state != old_state:
                if old_state is None:
                    state_path.unlink(missing_ok=True)
                else:
                    install._atomic_write(state_path, old_state)
        finally:
            if old_link is not None:
                temporary = install.PI_BIN.with_name(f".pi.restore-{time.time_ns()}")
                try:
                    temporary.symlink_to(old_link)
                    os.replace(temporary, install.PI_BIN)
                finally:
                    temporary.unlink(missing_ok=True)
            elif old_launcher is not None:
                if not install.PI_BIN.is_file() or install.PI_BIN.read_bytes() != old_launcher:
                    install._atomic_write(install.PI_BIN, old_launcher, mode=old_mode)
            else:
                install.PI_BIN.unlink(missing_ok=True)
        raise


def _usable_previous(status: dict[str, Any]) -> Path | None:
    for candidate in (status["runtimePath"], status["previousPath"]):
        if candidate:
            prefix = Path(candidate)
            try:
                install._validate_pi_install(prefix)
                return prefix
            except RuntimeError:
                pass
    return None


def update_pi_runtime(
    version: str | None = None,
    *,
    activation_guard: ActivationGuard | None = None,
) -> dict[str, Any]:
    release = _fetch_release(version)
    with (
        install._runtime_install_lock(update=True),
        ExitStack() as transitions,
        install._runtime_install_lock(),
    ):
        install._read_runtime_state()  # Do not overwrite a corrupt or unknown selection format.
        status = get_pi_runtime_status()
        current = status["version"]
        target = release["version"]
        if status["healthy"] and current is not None:
            if current == target or (
                version is None and _version_key(current) >= _version_key(target)
            ):
                return {**status, "changed": False, "targetVersion": target}
        node_bin = install.ensure_node_binary()
        prefix = install._runtime_store_path() / f"{target}-{time.time_ns()}"
        activated = False
        try:
            install._install_pi_runtime(
                node_bin, prefix=prefix, version=target, schema_spec=release["schemaSpec"]
            )
            _probe_pi_runtime(node_bin, prefix)
            if activation_guard:
                transitions.enter_context(activation_guard())
            _activate(node_bin, prefix, _usable_previous(status))
            activated = True
        finally:
            if not activated:
                # If restoration itself failed, do not delete a runtime still selected.
                try:
                    selected = install.active_pi_prefix()
                except RuntimeError:
                    selected = None
                if selected != prefix:
                    install._remove_install_path(prefix)
        return {**get_pi_runtime_status(), "changed": True, "targetVersion": target}


def rollback_pi_runtime(*, activation_guard: ActivationGuard | None = None) -> dict[str, Any]:
    with (
        install._runtime_install_lock(update=True),
        ExitStack() as transitions,
        install._runtime_install_lock(),
    ):
        state = install._read_runtime_state()
        if not state or state.get("previous") is None:
            raise RuntimeError("No previous Pi runtime is available to roll back to")
        previous = install._runtime_prefix_from_name(state["previous"])
        install._validate_pi_install(previous)
        current = install.active_pi_prefix()
        node_bin = install.ensure_node_binary()
        _probe_pi_runtime(node_bin, previous)
        if activation_guard:
            transitions.enter_context(activation_guard())
        _activate(node_bin, previous, current)
        return {**get_pi_runtime_status(), "changed": True}
