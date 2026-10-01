"""Setup flow support helpers for the Syke CLI."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import cast

import click

from syke.cli_support.daemon_state import daemon_payload
from syke.cli_support.providers import provider_payload
from syke.cli_support.render import SetupStatus, console
from syke.config import DEFAULT_USER, user_syke_db_path
from syke.observe.catalog import source_inventory


def run_setup_stage(label: str, fn):
    with SetupStatus(label):
        return fn()


def setup_source_inventory() -> list[dict[str, object]]:
    return source_inventory(DEFAULT_USER)


def setup_provider_choices() -> list[dict[str, object]]:
    from syke.llm.env import evaluate_provider_readiness
    from syke.llm.pi_client import get_pi_provider_catalog
    from syke.pi_state import get_default_provider

    active_provider = get_default_provider()
    choices: list[dict[str, object]] = []
    for entry in get_pi_provider_catalog():
        readiness = evaluate_provider_readiness(entry.id)
        label = entry.oauth_name or entry.id
        choices.append(
            {
                "id": entry.id,
                "label": label,
                "ready": readiness.ready,
                "detail": readiness.detail,
                "active": entry.id == active_provider,
                "oauth": entry.oauth,
                "default_model": entry.default_model,
                "models": list(entry.models),
            }
        )
    return choices


def setup_runtime_payload() -> dict[str, object]:
    from syke.llm.pi_client import PI_BIN, get_pi_version

    payload: dict[str, object] = {
        "launcher": str(PI_BIN),
        "installed": PI_BIN.exists(),
        "ready": False,
        "version": None,
        "detail": None,
    }

    try:
        payload["version"] = get_pi_version(install=False)
        payload["ready"] = True
        payload["detail"] = "Pi runtime available"
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        payload["detail"] = str(exc)

    return payload


def setup_target_payload(
    *,
    user_id: str,
    daemon: dict[str, object],
) -> list[dict[str, str]]:
    from syke.daemon.daemon import LOG_PATH, PLIST_PATH, SYSTEMD_UNIT_PATH
    from syke.distribution.context_files import capability_target_paths
    from syke.llm.pi_client import PI_BIN
    from syke.pi_state import (
        get_pi_agent_dir,
        get_pi_auth_path,
        get_pi_models_path,
        get_pi_settings_path,
    )
    from syke.runtime import workspace as workspace_module

    targets = [
        {"kind": "user_data", "path": str(workspace_module.SYKE_ROOT)},
        {"kind": "control", "path": str(workspace_module.CONTROL_ROOT)},
        {"kind": "native_sessions", "path": str(workspace_module.SESSIONS_DIR)},
        {"kind": "host_receipts", "path": str(workspace_module.RECEIPTS_DIR)},
        {"kind": "incoming_records", "path": str(workspace_module.RECORDS_DIR)},
        {"kind": "syke_db", "path": str(user_syke_db_path(user_id))},
        {
            "kind": "source_readers_dir",
            "path": str(workspace_module.WORKSPACE_ROOT / "adapters"),
        },
        {"kind": "workspace", "path": str(workspace_module.WORKSPACE_ROOT)},
        {"kind": "workspace_memex", "path": str(workspace_module.MEMEX_PATH)},
        {"kind": "pi_launcher", "path": str(PI_BIN)},
        {"kind": "pi_agent_dir", "path": str(get_pi_agent_dir())},
        {"kind": "pi_auth", "path": str(get_pi_auth_path())},
        {"kind": "pi_settings", "path": str(get_pi_settings_path())},
        {"kind": "pi_models", "path": str(get_pi_models_path())},
    ]
    targets.extend(
        {"kind": "capability_file", "path": str(path)} for path in capability_target_paths()
    )

    if daemon.get("installable") and not daemon.get("running"):
        targets.append({"kind": "daemon_log", "path": str(LOG_PATH)})
        if daemon.get("platform") == "Darwin":
            targets.append({"kind": "launch_agent", "path": str(PLIST_PATH)})
        elif daemon.get("platform") == "Linux":
            targets.append({"kind": "systemd_user_service", "path": str(SYSTEMD_UNIT_PATH)})
        else:
            targets.append({"kind": "manual_daemon", "path": "syke daemon run"})

    return targets


def setup_daemon_viability_payload() -> dict[str, object]:
    import platform

    from syke.cli_support.daemon_state import daemon_persistence_payload
    from syke.daemon.daemon import systemd_user_available
    from syke.runtime.locator import resolve_background_syke_runtime

    payload = daemon_payload()
    system = platform.system()
    detail = payload.get("detail")
    installable = True
    remediation: str | None = None
    persistence = daemon_persistence_payload(system)

    if system == "Darwin":
        try:
            runtime = resolve_background_syke_runtime()
            command = runtime.target_path or runtime.syke_command[0]
            detail = f"background-service-safe runtime: {command}"
        except RuntimeError as exc:
            installable = False
            detail = str(exc)
            remediation = (
                "Run `syke install-current` to create a managed background-service build, "
                "or move/install Syke outside protected folders. If the service is stale, "
                "run `syke daemon stop` first."
            )
    elif system == "Linux":
        available, systemd_detail = systemd_user_available()
        if not available:
            installable = False
            detail = f"background service unavailable: {systemd_detail}"
            remediation = (
                "Start a user systemd session, enable lingering for this user if needed, "
                "or run `syke daemon run` manually."
            )
        else:
            detail = "background service manager available"
    else:
        installable = False
        detail = "background service install is not supported on this platform"
        remediation = "Run `syke daemon run` manually."

    return {
        "platform": system,
        "running": payload.get("running", False),
        "registered": payload.get("registered", False),
        "installable": installable,
        "detail": detail,
        "remediation": remediation,
        "persistence": persistence,
    }


def build_setup_inspect_payload(*, user_id: str, cli_provider: str | None) -> dict[str, object]:
    from syke.daemon.ipc import daemon_runtime_status
    from syke.runtime.macos_filesystem_access import macos_filesystem_access_status
    from syke.runtime.sandbox import sandbox_enabled, sandbox_read_paths
    from syke.source_selection import get_selected_sources

    provider = provider_payload(cli_provider)
    providers = setup_provider_choices()
    sources = setup_source_inventory()
    selected_sources = get_selected_sources(user_id)
    runtime = setup_runtime_payload()
    daemon = setup_daemon_viability_payload()
    warm_runtime = daemon_runtime_status(user_id)
    setup_targets = setup_target_payload(
        user_id=user_id,
        daemon=daemon,
    )
    filesystem_sandboxed = sandbox_enabled()
    filesystem_read_roots = list(sandbox_read_paths()) if filesystem_sandboxed else None
    protected_folders = macos_filesystem_access_status()

    return {
        "ok": True,
        "schema_version": 1,
        "mode": "inspect",
        "user": user_id,
        "provider": provider,
        "provider_choices": providers,
        "sources": sources,
        "selected_sources": list(selected_sources) if selected_sources is not None else None,
        "filesystem_access": {
            "mode": "sandboxed" if filesystem_sandboxed else "process_permissions",
            "computer_read_roots": filesystem_read_roots,
            "macos_protected_folders": protected_folders,
            "persistent_write_roots": (
                ["syke_workspace", "syke_runtime"] if filesystem_sandboxed else None
            ),
        },
        "setup_targets": setup_targets,
        "runtime": runtime,
        "daemon": daemon,
        "daemon_runtime": warm_runtime,
    }


def render_setup_inspect_summary(info: dict[str, object]) -> None:
    console.print()

    # Provider status — one line
    provider = cast(dict[str, object], info["provider"])
    if provider.get("configured"):
        console.print(
            f"  [green]✓[/green] provider: {provider['id']}  "
            f"{provider.get('model', '')}  [dim]{provider.get('auth_source', '')}[/dim]"
        )
    else:
        console.print("  [yellow]✗[/yellow] provider: not configured")

    filesystem = cast(dict[str, object], info.get("filesystem_access") or {})
    if filesystem.get("mode") == "sandboxed":
        raw_roots = filesystem.get("computer_read_roots")
        roots = raw_roots if isinstance(raw_roots, list) else []
        home = str(Path.home().expanduser().resolve())
        read_scope = (
            "$HOME"
            if roots == [home]
            else ", ".join(str(root) for root in roots or []) or "no computer roots"
        )
        console.print(
            f"  [green]✓[/green] computer files: sandbox allows read-only {read_scope}; "
            "writes limited to workspace/runtime"
        )
    else:
        console.print(
            "  [yellow]✗[/yellow] computer files: sandbox unavailable or disabled; "
            "process permissions apply"
        )

    protected = filesystem.get("macos_protected_folders")
    if isinstance(protected, dict) and protected.get("applicable"):
        ok = bool(protected.get("ok"))
        icon = "[green]✓[/green]" if ok else "[yellow]·[/yellow]"
        detail = str(protected.get("detail") or "not checked")
        console.print(f"  {icon} macOS protected folders: {detail}")

    # Sources — files, last used, and span
    detected_sources = [
        cast(dict[str, object], item)
        for item in cast(list[dict[str, object]], info["sources"])
        if item.get("detected")
    ]
    if detected_sources:
        console.print()
        console.print("  [bold]Sources[/bold]")
        for item in detected_sources:
            name = cast(str, item["source"])
            files = cast(int, item["files_found"])
            fmt = cast(str, item.get("format_cluster", ""))
            unit = "db" if fmt == "sqlite" else "files"
            latest = cast(str | None, item.get("latest_seen"))
            latest_short = latest[:10] if latest else "?"
            console.print(
                f"    {name:<16} {files:>6,} {unit:<5}  [dim]last used:[/dim] {latest_short}"
            )
        total_files = sum(cast(int, s["files_found"]) for s in detected_sources)
        console.print(f"    [dim]{'total':<16} {total_files:>6,} files[/dim]")
    else:
        console.print("  [dim]· sources: none detected[/dim]")

    # Daemon — one line
    daemon = cast(dict[str, object], info["daemon"])
    if daemon.get("running"):
        console.print("  [green]✓[/green] background service: running")
    elif daemon.get("installable"):
        console.print("  [green]✓[/green] background service: ready")
    else:
        remediation = cast(str | None, daemon.get("remediation"))
        if remediation:
            console.print("  [yellow]✗[/yellow] background service: needs managed install")
            console.print(f"    [dim]{remediation}[/dim]")
        else:
            console.print("  [yellow]✗[/yellow] background service: blocked")

    # What setup will do — one paragraph
    console.print()
    console.print("  [bold]Setup will:[/bold]")
    if not provider.get("configured"):
        console.print("    · configure a provider")
    if detected_sources:
        console.print(f"    · ingest {len(detected_sources)} source(s) in background")
    console.print("    · synthesize your first memex")
    console.print("    · register capabilities to your agent harnesses")
    if daemon.get("installable") and not daemon.get("running"):
        console.print("    · start background service")

    # Writes — collapsed to one line with count
    setup_targets = cast(list[dict[str, str]], info.get("setup_targets") or [])
    console.print(f"\n  {len(setup_targets)} planned write targets")


def choose_setup_sources_interactive(
    sources: list[dict[str, object]], *, user_id: str = DEFAULT_USER
) -> list[str]:
    from syke.cli_support.auth_flow import term_menu_select_many
    from syke.source_selection import register_source_path

    detected = [item for item in sources if item.get("detected")]
    entries = []
    for item in detected:
        name = cast(str, item["source"])
        files = cast(int, item["files_found"])
        fmt = cast(str, item.get("format_cluster", ""))
        unit = "db" if fmt == "sqlite" else "files"
        latest = cast(str | None, item.get("latest_seen"))
        latest_short = latest[:10] if latest else "?"
        label = "inventory" if item.get("path_registration") else "last used"
        entries.append(f"{name:<16} {files:>6,} {unit:<5}  {label}: {latest_short}")
    entries.append("+ Add a local ChatGPTExporter archive path")
    selected = term_menu_select_many(
        entries,
        title="\n  Select sources to connect:\n",
        default_indices=list(range(len(detected))),
    )
    if selected is None:
        raise click.Abort()
    result = [cast(str, detected[idx]["source"]) for idx in selected if idx < len(detected)]
    if len(detected) in selected:
        path = click.prompt("Archive or archive collection path", type=click.Path(path_type=Path))
        try:
            info = register_source_path(user_id, "chatgpt-web", path)
        except (OSError, ValueError) as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(f"Registered chatgpt-web: {info['path']} ({info['state']})")
        for warning in info["warnings"]:
            click.echo(f"  Warning: {warning}")
        click.echo("Local snapshot only; runtime permissions are not yet verified.")
        if "chatgpt-web" not in result:
            result.append("chatgpt-web")
        sources[:] = source_inventory(user_id)
    return result
