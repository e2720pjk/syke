"""Pi and Node installation owned by Syke."""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger("syke.llm.pi_client")

PI_PACKAGE = "@earendil-works/pi-coding-agent"

# A tested bootstrap default, not a constraint on the user's installed version.
PI_PACKAGE_VERSION = "0.87.1"

PI_PACKAGE_SPEC = f"{PI_PACKAGE}@{PI_PACKAGE_VERSION}"

PI_SCHEMA_PACKAGE = "typebox"

PI_SCHEMA_VERSION = "1.3.27"

PI_SCHEMA_SPEC = f"{PI_SCHEMA_PACKAGE}@{PI_SCHEMA_VERSION}"

PI_LOCAL_PREFIX = Path.home() / ".syke" / "pi"

PI_BIN = Path.home() / ".syke" / "bin" / "pi"

PI_NODE_BIN = Path.home() / ".syke" / "bin" / "node"

PI_PACKAGE_ROOT = PI_LOCAL_PREFIX / "node_modules" / "@earendil-works" / "pi-coding-agent"

PI_CLI_JS = PI_PACKAGE_ROOT / "dist" / "cli.js"

PI_TOOL_EXTENSION = PI_LOCAL_PREFIX / "syke-tools.mjs"

PI_TOOL_EXTENSION_SOURCE = Path(__file__).resolve().parents[1] / "runtime" / "pi_tools.mjs"

_NODE_CANDIDATES = [
    Path("/opt/homebrew/bin/node"),
    Path("/usr/local/bin/node"),
    Path("/usr/bin/node"),
]

_NPM_CANDIDATES = [
    Path("/opt/homebrew/bin/npm"),
    Path("/usr/local/bin/npm"),
    Path("/usr/bin/npm"),
]

_MINIMUM_NODE_VERSION = (22, 19, 0)

_NODE_REQUIREMENT = "Node.js 22.19+ with Zstandard support"

_EXACT_VERSION = re.compile(
    r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)


def _runtime_state_path() -> Path:
    return PI_LOCAL_PREFIX.with_name("pi-runtime.json")


def _runtime_store_path() -> Path:
    return PI_LOCAL_PREFIX.with_name("pi-runtimes")


def _runtime_prefix_from_name(name: object) -> Path:
    if name == PI_LOCAL_PREFIX.name:
        return PI_LOCAL_PREFIX
    if isinstance(name, str):
        parts = name.split("/")
        if (
            len(parts) == 2
            and parts[0] == _runtime_store_path().name
            and re.fullmatch(r"[0-9A-Za-z.+-]+", parts[1])
            and parts[1] not in {".", ".."}
        ):
            return _runtime_store_path() / parts[1]
    raise RuntimeError(f"Invalid Pi runtime selection in {_runtime_state_path()}")


def _read_runtime_state() -> dict[str, Any] | None:
    path = _runtime_state_path()
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid Pi runtime selection at {path}: {exc}") from exc
    if (
        not isinstance(state, dict)
        or type(state.get("schemaVersion")) is not int
        or state["schemaVersion"] != 1
    ):
        raise RuntimeError(f"Unsupported Pi runtime selection at {path}")
    _runtime_prefix_from_name(state.get("active"))
    if state.get("previous") is not None:
        _runtime_prefix_from_name(state["previous"])
    return state


def active_pi_prefix() -> Path:
    """Resolve the local choice; without a choice, keep the legacy installation."""
    state = _read_runtime_state()
    return _runtime_prefix_from_name(state["active"]) if state else PI_LOCAL_PREFIX


def _atomic_write(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_runtime_state(active: Path, previous: Path | None) -> None:
    parent = PI_LOCAL_PREFIX.parent
    state = {
        "schemaVersion": 1,
        "active": active.relative_to(parent).as_posix(),
        "previous": previous.relative_to(parent).as_posix() if previous else None,
    }
    _runtime_prefix_from_name(state["active"])
    if state["previous"] is not None:
        _runtime_prefix_from_name(state["previous"])
    _atomic_write(_runtime_state_path(), (json.dumps(state, indent=2) + "\n").encode())


@contextmanager
def _runtime_install_lock(*, timeout: float = 10.0, update: bool = False) -> Iterator[None]:
    """Use a separate update lock so daemon restarts can acquire the launcher lock."""
    path = PI_LOCAL_PREFIX.with_name("pi-update.lock" if update else "pi-runtime.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()

            def acquire() -> None:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

            def release() -> None:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def acquire() -> None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            def release() -> None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

        deadline = time.monotonic() + timeout
        while True:
            try:
                acquire()
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "Another Pi runtime operation is in progress; retry later."
                    ) from exc
                time.sleep(0.1)
        try:
            yield
        finally:
            release()


def _find_executable(name: str, candidates: list[Path]) -> Path | None:
    resolved = shutil.which(name)
    if resolved:
        path = Path(resolved).expanduser().resolve()
        if path.exists() and os.access(path, os.X_OK):
            return path

    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    return None


def _ensure_symlink(link_path: Path, target_path: Path) -> Path:
    link_path.parent.mkdir(parents=True, exist_ok=True)

    if link_path.is_symlink():
        try:
            if link_path.resolve() == target_path.resolve() and os.access(link_path, os.X_OK):
                return link_path
        except OSError:
            pass
        link_path.unlink()
    elif link_path.exists():
        if link_path.resolve() == target_path.resolve() and os.access(link_path, os.X_OK):
            return link_path
        link_path.unlink()

    link_path.symlink_to(target_path)
    return link_path


def _node_version_text(node: Path) -> str:
    try:
        result = subprocess.run(
            [str(node), "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return "unknown version"
    version = (result.stdout or result.stderr).strip()
    return version or "unknown version"


def _node_supports_pi_runtime(node: Path) -> tuple[bool, str]:
    version_text = _node_version_text(node)
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", version_text)
    if match is None:
        return False, version_text
    version = tuple(int(part) for part in match.groups())
    if version < _MINIMUM_NODE_VERSION:
        return False, version_text

    try:
        result = subprocess.run(
            [
                str(node),
                "-e",
                "new RegExp('', 'v');"
                "const zlib = require('node:zlib');"
                "if (typeof zlib.createZstdDecompress !== 'function') {"
                "throw new Error('Node.js Zstandard support is unavailable');"
                "}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception as exc:
        return False, str(exc)
    if result.returncode == 0:
        return True, version_text
    detail = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
    return False, f"{version_text}: {detail[:300]}"


def ensure_node_binary() -> Path:
    """Return a stable absolute Node path Syke can use outside shell-managed PATH."""
    if PI_NODE_BIN.exists() and os.access(PI_NODE_BIN, os.X_OK):
        supported, detail = _node_supports_pi_runtime(PI_NODE_BIN)
        if supported:
            return PI_NODE_BIN
        if PI_NODE_BIN.is_symlink():
            PI_NODE_BIN.unlink()
        else:
            raise RuntimeError(f"Syke's Pi runtime requires {_NODE_REQUIREMENT}. Found {detail}")

    candidates: list[Path] = []
    resolved = shutil.which("node")
    if resolved:
        candidates.append(Path(resolved).expanduser().resolve())
    candidates.extend(candidate.resolve() for candidate in _NODE_CANDIDATES if candidate.exists())

    seen: set[Path] = set()
    unsupported: list[str] = []
    for node in candidates:
        if node in seen or not os.access(node, os.X_OK):
            continue
        seen.add(node)
        supported, detail = _node_supports_pi_runtime(node)
        if supported:
            return _ensure_symlink(PI_NODE_BIN, node)
        unsupported.append(detail)

    if unsupported:
        raise RuntimeError(
            f"Syke's Pi runtime requires {_NODE_REQUIREMENT}. Found {', '.join(unsupported)}"
        )
    raise RuntimeError(
        f"Syke's Pi runtime requires {_NODE_REQUIREMENT}. Install from https://nodejs.org"
    )


def _resolve_npm_binary() -> str:
    npm = _find_executable("npm", _NPM_CANDIDATES)
    if npm is None:
        raise RuntimeError(
            "Syke's Pi runtime requires npm to install Pi locally. Install Node.js from "
            "https://nodejs.org"
        )
    return str(npm)


def _write_pi_launcher(node_bin: Path, prefix: Path | None = None) -> Path:
    """Pin each launched process to its runtime, even after a later update."""
    prefix = prefix if prefix is not None else active_pi_prefix()
    cli = _package_path(prefix, PI_PACKAGE) / "dist" / "cli.js"
    if not cli.is_file():
        raise RuntimeError(f"Pi CLI entrypoint not found at {cli}")
    launcher = (
        f'#!/bin/sh\nexec {shlex.quote(str(node_bin))} {shlex.quote(str(cli.resolve()))} "$@"\n'
    )
    _atomic_write(PI_BIN, launcher.encode(), mode=0o755)
    return PI_BIN


def _package_path(prefix: Path, package: str) -> Path:
    return prefix / "node_modules" / Path(*package.split("/"))


def _read_package_manifest(package_root: Path) -> dict[str, Any]:
    manifest_path = package_root / "package.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid package metadata at {manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise RuntimeError(f"Invalid package metadata at {manifest_path}")
    return manifest


def _validate_pi_install(prefix: Path, *, expected_version: str | None = None) -> None:
    """Check the local exact pins, not equality with Syke's bootstrap default."""
    root_manifest = _read_package_manifest(prefix)
    dependencies = root_manifest.get("dependencies")
    if not isinstance(dependencies, dict):
        raise RuntimeError(f"Pi runtime dependencies are missing at {prefix / 'package.json'}")
    for package in (PI_PACKAGE, PI_SCHEMA_PACKAGE):
        pinned = dependencies.get(package)
        if not isinstance(pinned, str) or _EXACT_VERSION.fullmatch(pinned) is None:
            raise RuntimeError(
                f"Pi runtime must pin {package} at an exact version; found {pinned!r}"
            )
        if package == PI_PACKAGE and expected_version is not None and pinned != expected_version:
            raise RuntimeError(
                f"Pi runtime must pin {package} at {expected_version}; found {pinned!r}"
            )
        manifest = _read_package_manifest(_package_path(prefix, package))
        if manifest.get("name") != package or manifest.get("version") != pinned:
            raise RuntimeError(
                f"Pi runtime package {package} must be {pinned}; "
                f"found {manifest.get('name')!r} {manifest.get('version')!r}"
            )

    cli = _package_path(prefix, PI_PACKAGE) / "dist" / "cli.js"
    if not cli.is_file():
        raise RuntimeError(f"Pi CLI entrypoint not found at {cli}")


def _installed_pi_version(prefix: Path) -> str | None:
    try:
        manifest = _read_package_manifest(_package_path(prefix, PI_PACKAGE))
    except RuntimeError:
        return None
    version = manifest.get("version")
    return version if manifest.get("name") == PI_PACKAGE and isinstance(version, str) else None


def _version_tuple(version: str) -> tuple[int, int, int] | None:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:$|[-+])", version)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _remove_install_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)


def _install_pi_runtime(
    node_bin: Path,
    *,
    prefix: Path | None = None,
    version: str | None = None,
    schema_spec: str | None = None,
) -> None:
    """Stage exact local pins before replacing a destination, never the selection."""
    prefix = prefix if prefix is not None else PI_LOCAL_PREFIX
    version = version if version is not None else PI_PACKAGE_VERSION
    schema_spec = schema_spec if schema_spec is not None else PI_SCHEMA_VERSION
    if _EXACT_VERSION.fullmatch(version) is None:
        raise RuntimeError(f"Expected an exact Pi version, found {version!r}")
    npm = _resolve_npm_binary()
    prefix.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{prefix.name}.staging-", dir=prefix.parent))
    backup = prefix.with_name(f".{prefix.name}.backup-{os.getpid()}-{time.time_ns()}")
    install_env = dict(os.environ)
    install_env["PATH"] = os.pathsep.join(
        part for part in (str(node_bin.parent), install_env.get("PATH", "")) if part
    )

    try:
        result = subprocess.run(
            [
                npm,
                "install",
                "--prefix",
                str(staging),
                "--save-exact",
                "--package-lock=true",
                "--engine-strict",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
                f"{PI_PACKAGE}@{version}",
                f"{PI_SCHEMA_PACKAGE}@{schema_spec}",
            ],
            capture_output=True,
            text=True,
            timeout=180,
            env=install_env,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to install Pi runtime: {result.stderr.strip()[:500]}")
        _validate_pi_install(staging, expected_version=version)

        if prefix.exists() or prefix.is_symlink():
            prefix.rename(backup)
        try:
            staging.rename(prefix)
        except Exception:
            if backup.exists() or backup.is_symlink():
                backup.rename(prefix)
            raise
        _remove_install_path(backup)
    finally:
        _remove_install_path(staging)


def ensure_pi_binary() -> str:
    """Bootstrap only a missing installation; respect every existing local choice."""
    with _runtime_install_lock():
        prefix = active_pi_prefix()
        if not prefix.exists() and not prefix.is_symlink() and _read_runtime_state() is None:
            node_bin = ensure_node_binary()
            logger.info("Installing default Pi %s to %s", PI_PACKAGE_VERSION, prefix)
            _install_pi_runtime(node_bin)
        else:
            node_bin = None
        try:
            _validate_pi_install(prefix)
        except RuntimeError as exc:
            raise RuntimeError(
                f"{exc}. Run `syke pi update` to repair it; the local choice was not replaced."
            ) from exc
        node_bin = node_bin or ensure_node_binary()
        _write_pi_launcher(node_bin, prefix)
        return str(PI_BIN)


def _install_pi_tool_extension(prefix: Path | None = None) -> Path:
    """Install Syke's trusted tool broker beside the selected Pi package."""
    if not PI_TOOL_EXTENSION_SOURCE.is_file():
        raise RuntimeError(f"Syke Pi tool extension not found at {PI_TOOL_EXTENSION_SOURCE}")
    prefix = prefix if prefix is not None else active_pi_prefix()
    extension = prefix / PI_TOOL_EXTENSION.name
    source = PI_TOOL_EXTENSION_SOURCE.read_bytes()
    if not extension.exists() or extension.read_bytes() != source:
        _atomic_write(extension, source, mode=0o644)
    return extension


def resolve_pi_binary() -> str:
    """Find or install the Pi binary at ~/.syke/bin/pi."""
    return ensure_pi_binary()


def get_pi_version(*, install: bool = False, minimal_env: bool = False) -> str:
    """Return Pi version through Syke's stable launcher.

    When ``minimal_env`` is true, simulate a launchd-style cold environment with
    a stripped PATH to catch shell-dependent runtime failures.
    """
    if install:
        ensure_pi_binary()
    launcher = PI_BIN
    _validate_pi_install(active_pi_prefix())
    if not launcher.exists():
        raise FileNotFoundError(f"Pi launcher not found at {launcher}")

    env: dict[str, str] | None = None
    if minimal_env:
        env = {
            "HOME": str(Path.home()),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        }

    result = subprocess.run(
        [str(launcher), "--version"],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise RuntimeError(detail[:500])
    return result.stdout.strip() or result.stderr.strip() or "unknown"
