# pyright: reportMissingImports=false

"""Setup flow support helpers for the Syke CLI."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import click  # pyright: ignore[reportMissingImports]

from syke.cli_support.context import observe_registry
from syke.cli_support.daemon_state import daemon_payload
from syke.cli_support.providers import provider_payload
from syke.cli_support.render import (
    SetupStatus,
    console,
    render_setup_line,
)
from syke.config import user_syke_db_path


def run_setup_stage(label: str, fn):
    with SetupStatus(label):
        return fn()


def render_setup_source_result(source: str, status: str, detail: str | None = None) -> None:
    render_setup_line(source, status, detail=detail)


def trust_payload(
    user_id: str,
    *,
    selected_sources: tuple[str, ...] | list[str] | None = None,
) -> dict[str, list[dict[str, str]]]:
    import platform

    from syke.config import CODEX_GLOBAL_AGENTS, SKILLS_DIRS, user_data_dir
    from syke.daemon.daemon import LOG_PATH, PLIST_PATH, SYSTEMD_UNIT_PATH
    from syke.observe.catalog import is_source_selected
    from syke.pi_state import (
        get_pi_agent_dir,
        get_pi_auth_path,
        get_pi_models_path,
        get_pi_settings_path,
    )
    from syke.runtime.workspace import WORKSPACE_ROOT

    sources: list[dict[str, str]] = []
    registry = observe_registry(user_id)
    for desc in registry.active_harnesses():
        if not is_source_selected(desc, selected_sources):
            continue
        if desc.source == "chatgpt-web":
            sources.append(
                {
                    "source": desc.source,
                    "path": str(WORKSPACE_ROOT / "sources" / "chatgpt-web" / "projection.jsonl"),
                }
            )
            continue
        if desc.discover is None:
            continue
        for root in desc.discover.roots:
            sources.append({"source": desc.source, "path": str(Path(root.path).expanduser())})

    targets: list[dict[str, str]] = [
        {"kind": "user_data", "path": str(user_data_dir(user_id))},
        {"kind": "workspace", "path": str(Path.home() / ".syke")},
        {"kind": "pi_agent_dir", "path": str(get_pi_agent_dir())},
        {"kind": "pi_auth", "path": str(get_pi_auth_path())},
        {"kind": "pi_settings", "path": str(get_pi_settings_path())},
        {"kind": "pi_models", "path": str(get_pi_models_path())},
        {"kind": "launcher", "path": str(Path.home() / ".syke" / "bin" / "syke")},
        {"kind": "daemon_log", "path": str(LOG_PATH)},
        {"kind": "memex_export", "path": str(user_data_dir(user_id) / "MEMEX.md")},
        {"kind": "memex_include", "path": str(Path.home() / ".claude" / "CLAUDE.md")},
        {"kind": "codex_agents", "path": str(CODEX_GLOBAL_AGENTS)},
    ]
    targets.extend({"kind": "skill_dir", "path": str(path)} for path in SKILLS_DIRS)

    if platform.system() == "Darwin":
        targets.append({"kind": "launch_agent", "path": str(PLIST_PATH)})
    elif platform.system() == "Linux":
        targets.append({"kind": "systemd_user_service", "path": str(SYSTEMD_UNIT_PATH)})
    else:
        targets.append({"kind": "manual_daemon", "path": "syke daemon run"})

    return {"sources": sources, "targets": targets}


def _latest_mtime_sort_value(item: dict[str, object]) -> float:
    value = item.get("latest_mtime")
    if not isinstance(value, (int, float)):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def setup_source_inventory(user_id: str) -> list[dict[str, object]]:
    from datetime import UTC, datetime

    from syke.observe.catalog import iter_discovered_files

    sources: list[dict[str, object]] = []
    registry = observe_registry(user_id)

    for desc in registry.active_harnesses():
        files_found = 0
        detected_paths: list[str] = []
        roots: list[str] = []
        latest_mtime: float | None = None
        if desc.source == "chatgpt-web":
            from syke.config import chatgpt_web_excluded_project_ids
            from syke.observe.chatgpt_web import inspect_chatgpt_web_archive

            root = (
                Path(desc.discover.roots[0].path) if desc.discover and desc.discover.roots else None
            )
            diagnostics = (
                inspect_chatgpt_web_archive(root, chatgpt_web_excluded_project_ids())
                if root is not None
                else {"archive_validated": False, "error": {"reason": "root_missing"}}
            )
            roots = [str(root)] if root is not None else []
            valid = bool(diagnostics.get("archive_validated"))
            files_found = int(diagnostics.get("conversations_discovered", 0)) if valid else 0
            if root is not None:
                detected_paths = [
                    str(root / name) for name in ("archive.json", "indexes/conversations.jsonl")
                ]
                for path in (root / "archive.json", root / "indexes" / "conversations.jsonl"):
                    try:
                        mtime = path.stat().st_mtime
                    except OSError:
                        continue
                    latest_mtime = max(latest_mtime or mtime, mtime)
            sources.append(
                {
                    "source": desc.source,
                    "format_cluster": desc.format_cluster,
                    "explicit_only": bool(getattr(desc, "explicit_only", False)),
                    "roots": roots,
                    "files_found": files_found,
                    "detected": valid,
                    "selectable": valid,
                    "sample_paths": detected_paths,
                    "latest_mtime": latest_mtime,
                    "latest_seen": datetime.fromtimestamp(latest_mtime, UTC).isoformat()
                    if latest_mtime is not None
                    else None,
                    "archive": diagnostics,
                }
            )
            continue
        if desc.discover is not None:
            for root in desc.discover.roots:
                roots.append(str(Path(root.path).expanduser()))
            try:
                discovered_files = iter_discovered_files(desc)
            except OSError:
                discovered_files = []
            for match in discovered_files:
                files_found += 1
                try:
                    mtime = match.stat().st_mtime
                except OSError:
                    mtime = None
                if mtime is not None and (latest_mtime is None or mtime > latest_mtime):
                    latest_mtime = mtime
                if len(detected_paths) < 3:
                    detected_paths.append(str(match))

        sources.append(
            {
                "source": desc.source,
                "format_cluster": desc.format_cluster,
                "explicit_only": bool(getattr(desc, "explicit_only", False)),
                "roots": roots,
                "files_found": files_found,
                "detected": files_found > 0,
                "sample_paths": detected_paths,
                "latest_mtime": latest_mtime,
                "latest_seen": datetime.fromtimestamp(latest_mtime, UTC).isoformat()
                if latest_mtime is not None
                else None,
            }
        )

    sources.sort(
        key=lambda item: (
            not bool(item["detected"]),
            -_latest_mtime_sort_value(item),
            cast(str, item["source"]),
        )
    )
    return sources


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
    except (RuntimeError, FileNotFoundError) as exc:
        payload["detail"] = str(exc)

    return payload


def setup_target_payload(
    *,
    user_id: str,
    daemon: dict[str, object],
) -> list[dict[str, str]]:
    from syke.config import user_data_dir
    from syke.daemon.daemon import LOG_PATH, PLIST_PATH, SYSTEMD_UNIT_PATH
    from syke.llm.pi_client import PI_BIN
    from syke.pi_state import (
        get_pi_agent_dir,
        get_pi_auth_path,
        get_pi_models_path,
        get_pi_settings_path,
    )
    from syke.runtime.workspace import MEMEX_PATH, SYKE_DB, WORKSPACE_ROOT

    targets = [
        {"kind": "user_data", "path": str(user_data_dir(user_id))},
        {"kind": "syke_db", "path": str(user_syke_db_path(user_id))},
        {"kind": "source_readers_dir", "path": str(WORKSPACE_ROOT / "adapters")},
        {"kind": "workspace", "path": str(WORKSPACE_ROOT)},
        {"kind": "workspace_syke_db", "path": str(SYKE_DB)},
        {"kind": "workspace_memex", "path": str(MEMEX_PATH)},
        {"kind": "pi_launcher", "path": str(PI_BIN)},
        {"kind": "pi_agent_dir", "path": str(get_pi_agent_dir())},
        {"kind": "pi_auth", "path": str(get_pi_auth_path())},
        {"kind": "pi_settings", "path": str(get_pi_settings_path())},
        {"kind": "pi_models", "path": str(get_pi_models_path())},
    ]

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


def _build_next_steps(provider: dict[str, object], daemon: dict[str, object]) -> list[str]:
    """Actionable commands an agent should run to complete setup non-interactively."""
    steps: list[str] = []
    if not provider.get("configured"):
        steps.append("syke auth set <provider> <API_KEY> --use")
    steps.append("syke setup --yes")
    return steps


def build_setup_inspect_payload(*, user_id: str, cli_provider: str | None) -> dict[str, object]:
    from syke.daemon.ipc import daemon_runtime_status
    from syke.observe.bootstrap import customized_adapter_hints
    from syke.runtime.workspace import WORKSPACE_ROOT
    from syke.source_selection import get_selected_sources

    provider = provider_payload(cli_provider)
    providers = setup_provider_choices()
    sources = setup_source_inventory(user_id)
    selected_sources = get_selected_sources(user_id)
    trust = trust_payload(user_id, selected_sources=selected_sources)
    runtime = setup_runtime_payload()
    daemon = setup_daemon_viability_payload()
    warm_runtime = daemon_runtime_status(user_id)
    setup_targets = setup_target_payload(
        user_id=user_id,
        daemon=daemon,
    )
    adapter_repairs = [
        {"source": result.source, "detail": result.detail}
        for result in customized_adapter_hints(
            WORKSPACE_ROOT,
            selected_sources=selected_sources,
        )
    ]

    detected_sources = [item["source"] for item in sources if item["detected"]]
    previously_selected = set(selected_sources or ())
    default_sources = [
        item["source"]
        for item in sources
        if item["detected"]
        and (not item.get("explicit_only", False) or item["source"] in previously_selected)
    ]
    explicit_only_sources = [
        item["source"] for item in sources if item["detected"] and item.get("explicit_only", False)
    ]
    proposed_actions: list[dict[str, object]] = [
        {
            "id": "bootstrap_source_readers",
            "description": "Bootstrap or repair detected source readers before ingest when needed.",
        }
    ]
    consent_points: list[dict[str, object]] = []

    if detected_sources:
        proposed_actions.append(
            {
                "id": "connect_sources",
                "description": "Connect selected detected sources for synthesis and ask context.",
                "sources": default_sources,
                "available_sources": detected_sources,
                "explicit_only_sources": explicit_only_sources,
            }
        )

    proposed_actions.append(
        {
            "id": "initial_synthesis",
            "description": (
                "Run initial synthesis immediately when a provider is ready "
                "and setup creates or changes state."
            ),
        }
    )

    if not provider.get("configured"):
        consent_points.append(
            {
                "id": "provider",
                "question": "Choose a provider before synthesis can run.",
                "options": [item["id"] for item in providers],
                "default": None,
            }
        )
    if detected_sources:
        consent_points.append(
            {
                "id": "sources",
                "question": "Choose which detected sources to connect during setup.",
                "options": detected_sources,
                "default": default_sources,
                "explicit_only": explicit_only_sources,
            }
        )
    if daemon.get("installable") and not daemon.get("running"):
        proposed_actions.append(
            {
                "id": "background_service",
                "description": (
                    "Install the background service for sync, warm ask, and timeline UI."
                ),
            }
        )
        consent_points.append(
            {
                "id": "daemon",
                "question": "Enable the background service after setup?",
                "options": ["yes", "no"],
                "default": "yes",
            }
        )

    return {
        "ok": True,
        "schema_version": 1,
        "mode": "inspect",
        "user": user_id,
        "provider": provider,
        "provider_choices": providers,
        "sources": sources,
        "selected_sources": list(selected_sources) if selected_sources is not None else None,
        "trust": trust,
        "setup_targets": setup_targets,
        "adapter_repairs": adapter_repairs,
        "runtime": runtime,
        "daemon": daemon,
        "daemon_runtime": warm_runtime,
        "proposed_actions": proposed_actions,
        "consent_points": consent_points,
        "next_steps": _build_next_steps(provider, daemon),
        "next_commands": [
            "syke auth status",
            "syke status --json",
            "syke doctor",
        ],
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

    # Sources — files, last used, and span
    detected_sources = [
        cast(dict[str, object], item)
        for item in cast(list[dict[str, object]], info["sources"])
        if item.get("detected")
    ]
    previously_selected = info.get("selected_sources")
    previously_selected = (
        previously_selected if isinstance(previously_selected, (list, tuple)) else ()
    )
    planned_sources = [
        item
        for item in detected_sources
        if not item.get("explicit_only", False) or cast(str, item["source"]) in previously_selected
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
            opt_in = " [explicit opt-in]" if item.get("explicit_only") else ""
            console.print(
                f"    {name:<16} {files:>6,} {unit:<5}  [dim]last used:[/dim] "
                f"{latest_short}{opt_in}"
            )
        total_files = sum(cast(int, s["files_found"]) for s in planned_sources)
        console.print(f"    [dim]{'planned total':<16} {total_files:>6,} files[/dim]")
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

    adapter_repairs = cast(list[dict[str, object]], info.get("adapter_repairs") or [])
    if adapter_repairs:
        console.print()
        console.print("  [yellow]! adapter guides need manual repair:[/yellow]")
        for repair in adapter_repairs:
            console.print(f"    {repair['source']}: {repair['detail']}")

    # What setup will do — one paragraph
    console.print()
    console.print("  [bold]Setup will:[/bold]")
    if not provider.get("configured"):
        console.print("    · configure a provider")
    if planned_sources:
        console.print(f"    · ingest {len(planned_sources)} source(s) in background")
    explicit_only_sources = [
        item["source"]
        for item in detected_sources
        if item.get("explicit_only", False) and cast(str, item["source"]) not in previously_selected
    ]
    if explicit_only_sources:
        console.print(
            "    · leave explicit-only source(s) disabled unless explicitly selected: "
            + ", ".join(cast(str, source) for source in explicit_only_sources)
        )
    console.print("    · synthesize your first memex")
    console.print("    · register capabilities to your agent harnesses")
    if daemon.get("installable") and not daemon.get("running"):
        console.print("    · start background service")

    # Writes — collapsed to one line with count
    setup_targets = cast(
        list[dict[str, str]],
        info.get("setup_targets")
        or cast(dict[str, object], info.get("trust") or {}).get("targets", []),
    )
    console.print(f"\n  {len(setup_targets)} files will be created under ~/.syke")


def choose_setup_sources_interactive(
    sources: list[dict[str, object]],
    *,
    previously_selected: tuple[str, ...] | list[str] = (),
) -> list[str]:
    from syke.cli_support.auth_flow import term_menu_select_many

    detected = [item for item in sources if item.get("detected")]
    if not detected:
        return []

    entries = []
    for item in detected:
        name = cast(str, item["source"])
        files = cast(int, item["files_found"])
        fmt = cast(str, item.get("format_cluster", ""))
        unit = "db" if fmt == "sqlite" else "files"
        latest = cast(str | None, item.get("latest_seen"))
        latest_short = latest[:10] if latest else "?"
        entries.append(f"{name:<16} {files:>6,} {unit:<5}  last used: {latest_short}")

    selected = term_menu_select_many(
        entries,
        title="\n  Select sources to connect (newest first):\n",
        default_indices=[
            index
            for index, item in enumerate(detected)
            if not item.get("explicit_only", False)
            or cast(str, item["source"]) in previously_selected
        ],
    )
    if selected is None:
        raise click.Abort()
    return [cast(str, detected[idx]["source"]) for idx in selected]
