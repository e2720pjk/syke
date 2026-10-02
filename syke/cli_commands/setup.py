"""Setup command for the Syke CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

import click
from rich.console import Console

from syke.cli_support.auth_flow import (
    ensure_setup_pi_runtime,
    run_interactive_provider_flow,
)
from syke.cli_support.auth_flow import (
    verify_provider_activation as verify_setup_provider_connection,
)
from syke.cli_support.exit_codes import EXIT_RUNTIME, SykeAuthException
from syke.cli_support.installers import run_managed_checkout_install
from syke.cli_support.providers import provider_payload
from syke.cli_support.render import render_section
from syke.cli_support.setup_support import (
    build_setup_inspect_payload,
    choose_setup_sources_interactive,
    render_setup_inspect_summary,
    run_setup_stage,
    setup_daemon_viability_payload,
)
from syke.config import _is_source_install
from syke.onboarding import write_onboarding_state
from syke.runtime.macos_filesystem_access import (
    macos_filesystem_access_status,
    run_macos_filesystem_access_check,
)
from syke.source_selection import set_selected_sources

console = Console()


def _launch_background_onboarding(
    *,
    user_id: str,
    selected_sources: list[str],
) -> Path:
    from syke.daemon.daemon import LOG_PATH
    from syke.runtime.locator import ensure_syke_launcher, resolve_background_syke_runtime

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    runtime = resolve_background_syke_runtime()
    launcher = ensure_syke_launcher(runtime)
    cmd = [str(launcher), "--user", user_id, "sync"]
    for source in selected_sources:
        cmd.extend(["--source", source])
    cmd.append("--start-daemon-after")

    with LOG_PATH.open("a", encoding="utf-8") as log_file:
        subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
            timeout=60,
            check=True,
            cwd=str(runtime.working_directory) if runtime.working_directory else os.getcwd(),
        )
    return LOG_PATH


def _begin_onboarding(
    *,
    user_id: str,
    selected_sources: list[str],
    total_files: int,
    estimated_minutes: int,
    estimate_method: str,
    persistence: dict[str, object],
) -> tuple[dict[str, object], Path | None, str | None]:
    from syke.daemon.daemon import LOG_PATH

    onboarding = write_onboarding_state(
        user_id,
        selected_sources=selected_sources,
        total_files=total_files,
        estimated_minutes=estimated_minutes,
        estimate_method=estimate_method,
        mode="daemon",
        monitor=str(LOG_PATH),
        persistence=persistence,
    )
    try:
        log_path = _launch_background_onboarding(
            user_id=user_id,
            selected_sources=selected_sources,
        )
    except Exception as exc:
        return onboarding, None, str(exc)
    return onboarding, log_path, None


def _select_agent_sources(
    inspect_info: dict[str, object],
    selected_sources_cli: tuple[str, ...],
) -> tuple[list[str], list[dict[str, object]], list[str]]:
    source_items = cast(list[dict[str, object]], inspect_info.get("sources") or [])
    detected_sources = [cast(str, s["source"]) for s in source_items if s.get("detected")]
    if not selected_sources_cli:
        return detected_sources, source_items, []

    requested = list(dict.fromkeys(selected_sources_cli))
    unknown = [source for source in requested if source not in detected_sources]
    if unknown:
        return [], source_items, unknown
    return requested, source_items, []


def _agent_setup_command(*, selected_sources_cli: tuple[str, ...]) -> str:
    parts = ["syke", "setup", "--agent"]
    for source in selected_sources_cli:
        parts.extend(("--source", source))
    return " ".join(parts)


def _render_macos_filesystem_access(result: dict[str, object]) -> None:
    folders = result.get("folders")
    folder_payload = folders if isinstance(folders, dict) else {}
    for name in ("Desktop", "Documents", "Downloads"):
        raw = folder_payload.get(name)
        item = raw if isinstance(raw, dict) else {}
        status = item.get("status")
        if status == "granted":
            console.print(f"  [green]✓[/green] {name}")
        elif status == "missing":
            console.print(f"  [dim]· {name}: folder does not exist[/dim]")
        else:
            console.print(f"  [yellow]·[/yellow] {name}: not available")
    if not result.get("ok"):
        detail = result.get("detail", "Protected-folder access failed")
        console.print(f"\n  [yellow]{detail}[/yellow]")


def _run_agent_setup(
    user_id: str,
    cli_provider: str | None,
    selected_sources_cli: tuple[str, ...] = (),
) -> dict[str, object]:
    """Non-interactive agent setup. Returns structured JSON result."""
    from syke.cli_support.exit_codes import EXIT_AUTH, SykeRuntimeException

    try:
        inspect_info = build_setup_inspect_payload(user_id=user_id, cli_provider=cli_provider)
    except Exception as exc:
        return {"status": "failed", "error": str(exc), "exit_code": 1}

    selected, _source_items, unknown_sources = _select_agent_sources(
        inspect_info,
        selected_sources_cli,
    )
    if unknown_sources:
        return {
            "status": "failed",
            "error": (
                f"Requested source(s) not detected during setup: {', '.join(unknown_sources)}"
            ),
            "exit_code": 2,
        }

    rerun_command = _agent_setup_command(selected_sources_cli=selected_sources_cli)

    try:
        from syke.llm.pi_client import ensure_pi_binary, get_pi_version

        ensure_pi_binary()
        get_pi_version(install=False)
    except (SykeRuntimeException, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        return {
            "status": "needs_runtime",
            "error": str(exc),
            "next_steps": [
                "Install Node.js 22.19 or newer",
                rerun_command,
            ],
            "exit_code": EXIT_RUNTIME,
        }
    except Exception as exc:
        return {
            "status": "failed",
            "error": f"Unexpected runtime preparation failure: {exc}",
            "exit_code": 1,
        }

    try:
        inspect_info = build_setup_inspect_payload(user_id=user_id, cli_provider=cli_provider)
    except Exception as exc:
        return {"status": "failed", "error": str(exc), "exit_code": 1}
    selected, source_items, unknown_sources = _select_agent_sources(
        inspect_info,
        selected_sources_cli,
    )
    if unknown_sources:
        return {
            "status": "failed",
            "error": (
                f"Requested source(s) not detected during setup: {', '.join(unknown_sources)}"
            ),
            "exit_code": 2,
        }

    # Check provider
    provider = cast(dict[str, object], inspect_info["provider"])
    if not provider.get("configured"):
        detected = [
            {
                "source": cast(str, s["source"]),
                "files": cast(int, s["files_found"]),
                "format": cast(str, s.get("format_cluster", "")),
            }
            for s in source_items
            if s.get("detected") and cast(str, s["source"]) in selected
        ]
        provider_choices = [
            {
                "id": item.get("id"),
                "label": item.get("label"),
                "oauth": bool(item.get("oauth")),
                "ready": bool(item.get("ready")),
                "default_model": item.get("default_model"),
            }
            for item in cast(
                list[dict[str, object]],
                inspect_info.get("provider_choices", []),
            )
        ]
        return {
            "status": "needs_provider",
            "user": user_id,
            "detected_sources": detected,
            "provider_choices": provider_choices,
            "instructions": (
                "Choose a provider with the user. Prefer OAuth when available; Pi will "
                "open a browser or show a device code. Never request or print a secret. "
                "After authentication, rerun the setup command."
            ),
            "auth_options": {
                "oauth": "syke auth login <provider> --use",
                "api_key": "syke auth set <provider> --api-key <KEY> --use",
                "inspect": "syke auth status --json",
            },
            "next_steps": [
                "Choose a provider from provider_choices and run the matching auth option",
                rerun_command,
            ],
            "exit_code": EXIT_AUTH,
        }

    provider_id = cast(str, provider.get("id"))
    model_id = cast(str, provider.get("model", ""))

    # Verify provider connection
    try:
        handshake = verify_setup_provider_connection(provider_id, model_id)
    except Exception as exc:
        return {
            "status": "failed",
            "error": f"Provider verification failed: {exc}",
            "next_steps": [
                "syke auth status --json",
                rerun_command,
            ],
            "exit_code": EXIT_RUNTIME,
        }

    # Handle managed install (macOS source checkout)
    daemon_info = cast(dict[str, object], inspect_info["daemon"])
    if (
        not daemon_info.get("installable")
        and daemon_info.get("platform") == "Darwin"
        and _is_source_install()
    ):
        try:
            run_managed_checkout_install(
                user_id=user_id, installer="auto", restart_daemon=False, prompt=False
            )
            daemon_info = setup_daemon_viability_payload()
        except (click.ClickException, OSError, RuntimeError) as exc:
            daemon_info = {
                **daemon_info,
                "detail": f"Managed background install failed: {exc}",
            }

    if not daemon_info.get("installable"):
        detail = daemon_info.get("detail") or "no supported background service is available"
        return {
            "status": "failed",
            "error": f"Background service is required for setup: {detail}",
            "next_steps": ["syke doctor", rerun_command],
            "exit_code": 1,
        }

    filesystem_access = macos_filesystem_access_status()
    if daemon_info.get("platform") == "Darwin":
        filesystem_access = run_macos_filesystem_access_check(user_id)

    if selected:
        set_selected_sources(user_id, selected)

    total_files = sum(
        cast(int, s.get("files_found", 0))
        for s in source_items
        if s.get("detected") and cast(str, s["source"]) in selected
    )

    estimate_method = "max(2, total_files // 1500 + 3)"
    est = max(2, total_files // 1500 + 3)
    onboarding, log_path, launch_error = _begin_onboarding(
        user_id=user_id,
        selected_sources=selected,
        total_files=total_files,
        estimated_minutes=est,
        estimate_method=estimate_method,
        persistence=cast(dict[str, object], daemon_info.get("persistence") or {}),
    )
    if launch_error:
        return {
            "status": "failed",
            "error": f"Background launch failed: {launch_error}",
            "onboarding": onboarding,
            "next_steps": ["syke doctor", rerun_command],
            "exit_code": 1,
        }
    instructions = (
        "Setup is complete. Background ingestion and synthesis are running now. "
        f"This takes about {est} minutes based on {total_files} detected files. "
        "The user can start using syke ask and add records with syke record immediately — "
        "ordinary synthesis considers those records as it updates memory. "
        "Do NOT run syke setup again. "
        "Check progress with: syke status --json"
    )
    next_steps = [
        'syke ask "what am I working on?"',
        "syke status --json",
    ]
    if filesystem_access.get("applicable") and not filesystem_access.get("ok"):
        instructions += f" Protected-folder access is incomplete: {filesystem_access['detail']}."
        next_steps.insert(0, "syke doctor")
    return {
        "status": "complete",
        "user": user_id,
        "provider": {"id": provider_id, "model": model_id},
        "handshake": handshake,
        "sources_ingesting": selected,
        "total_files": total_files,
        "estimated_minutes": est,
        "estimate_method": estimate_method,
        "daemon": "started",
        "daemon_persistence": daemon_info.get("persistence"),
        "daemon_detail": daemon_info.get("detail"),
        "filesystem_access": filesystem_access,
        "monitor": str(log_path) if log_path else None,
        "onboarding": onboarding,
        "instructions": instructions,
        "next_steps": next_steps,
        "exit_code": 0,
    }


@click.command(
    short_help="Review and apply local memory setup.",
    help=(
        "Inspect current setup state, then apply the approved local memory plan.\n\n"
        "Agents: use --agent for structured setup and follow its status and next_steps. "
        "Use --json to inspect without writing. A running background service is required "
        "for setup to complete."
    ),
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help=(
        "Auto-consent non-auth confirmations; requires an already configured "
        "provider unless --provider is set"
    ),
)
@click.option(
    "--json", "use_json", is_flag=True, help="Inspect setup state as JSON without side effects"
)
@click.option(
    "--agent",
    "agent_mode",
    is_flag=True,
    help="Non-interactive agent mode. Returns JSON, acts on current state.",
)
@click.option(
    "--source",
    "selected_sources_cli",
    multiple=True,
    help="Only connect selected detected source(s). Repeatable.",
)
@click.pass_context
def setup(
    ctx: click.Context,
    yes: bool,
    use_json: bool,
    agent_mode: bool,
    selected_sources_cli: tuple[str, ...],
) -> None:
    """Inspect current setup state, then apply the approved local memory plan."""
    from syke.llm.env import resolve_provider

    user_id = ctx.obj["user"]
    if agent_mode:
        result = _run_agent_setup(
            user_id,
            ctx.obj.get("provider"),
            selected_sources_cli,
        )
        click.echo(json.dumps(result, indent=2))
        ctx.exit(result.get("exit_code", 0))
        return

    if use_json:
        click.echo(
            json.dumps(
                build_setup_inspect_payload(
                    user_id=user_id,
                    cli_provider=ctx.obj.get("provider"),
                ),
                indent=2,
            )
        )
        return

    console.print(f"\n[bold]syke setup[/bold]  [dim]{user_id}[/dim]")

    cli_provider = ctx.obj.get("provider")
    inspect_info = run_setup_stage(
        "Preparing setup plan...",
        lambda: build_setup_inspect_payload(
            user_id=user_id,
            cli_provider=cli_provider,
        ),
    )
    render_setup_inspect_summary(inspect_info)
    if not yes and not click.confirm("\nApply this setup plan?"):
        console.print("\n  [dim]No changes made.[/dim]")
        return

    detected_sources = [
        cast(dict[str, object], item)["source"]
        for item in cast(list[dict[str, object]], inspect_info.get("sources") or [])
        if cast(dict[str, object], item).get("detected")
    ]
    selected_sources = detected_sources
    if selected_sources_cli:
        requested = list(dict.fromkeys(selected_sources_cli))
        unknown = [source for source in requested if source not in detected_sources]
        if unknown:
            raise click.UsageError(
                f"Requested source(s) not detected during setup: {', '.join(unknown)}"
            )
        selected_sources = requested
    elif not yes:
        source_items = cast(list[dict[str, object]], inspect_info.get("sources") or [])
        selected_sources = choose_setup_sources_interactive(source_items, user_id=user_id)
        inspect_info["sources"] = source_items
        detected_sources = [
            cast(str, item["source"]) for item in source_items if item.get("detected")
        ]
    persist_selected_sources = bool(detected_sources or selected_sources_cli)

    render_section("Sources")
    if selected_sources:
        skipped_sources = [source for source in detected_sources if source not in selected_sources]
        console.print(f"  [green]✓[/green] {', '.join(selected_sources)}")
        if skipped_sources:
            console.print(f"  [dim]· skipped: {', '.join(skipped_sources)}[/dim]")
    elif detected_sources:
        console.print(f"  [dim]· none selected (skipped: {', '.join(detected_sources)})[/dim]")
    else:
        console.print("  [dim]· none detected[/dim]")

    render_section("Runtime")
    run_setup_stage("Checking Pi runtime…", ensure_setup_pi_runtime)

    render_section("Provider")
    has_provider = False
    interactive_provider_selected = False

    if cli_provider:
        try:
            provider = resolve_provider(cli_provider=cli_provider)
            has_provider = True
            console.print(f"  [green]✓[/green]  Provider: [bold]{provider.id}[/bold]")
        except ValueError as exc:
            raise click.UsageError(str(exc)) from exc
        except RuntimeError as exc:
            raise SykeAuthException(str(exc)) from exc
    elif not yes and sys.stdin.isatty():
        flow = run_interactive_provider_flow()
        has_provider = flow.status == "selected"
        interactive_provider_selected = has_provider
    elif cast(dict[str, object], inspect_info["provider"]).get("configured"):
        has_provider = True
    else:
        raise SykeAuthException(
            "Setup requires a configured provider. Run `syke auth set <provider> ... --use`, "
            "`syke auth login <provider> --use`, or rerun setup interactively."
        )

    if not has_provider:
        raise SykeAuthException(
            "Setup requires a configured provider. Run `syke auth set <provider> ... --use`, "
            "`syke auth login <provider> --use`, or rerun setup interactively."
        )

    provider_info = provider_payload(ctx.obj.get("provider"))
    if provider_info.get("configured") and not interactive_provider_selected:
        pid = cast(str, provider_info.get("id", "unknown"))
        mid = cast(str, provider_info.get("model", ""))
        auth = cast(str, provider_info.get("auth_source", ""))
        console.print(f"  [green]✓[/green] {pid}  {mid}  [dim]{auth}[/dim]")

    provider_id = cast(str | None, provider_info.get("id"))
    model_id = cast(str | None, provider_info.get("model"))
    if not provider_id or not model_id:
        raise SykeAuthException("Setup requires a provider and model before ingest can begin.")
    if not interactive_provider_selected:
        handshake = run_setup_stage(
            f"Verifying {provider_id}/{model_id}…",
            lambda: verify_setup_provider_connection(provider_id, model_id),
        )
        console.print(f"  [green]✓[/green] {provider_id}/{model_id} connected")
        if handshake:
            console.print(f"    [dim]{handshake}[/dim]")

    daemon_info = cast(dict[str, object], inspect_info["daemon"])
    if (
        not daemon_info.get("installable")
        and daemon_info.get("platform") == "Darwin"
        and _is_source_install()
    ):
        try:
            run_setup_stage(
                "Installing managed background-service build...",
                lambda: run_managed_checkout_install(
                    user_id=user_id,
                    installer="auto",
                    restart_daemon=False,
                    prompt=False,
                ),
            )
            daemon_info = setup_daemon_viability_payload()
        except click.ClickException as exc:
            daemon_info = {
                **daemon_info,
                "detail": str(exc),
                "remediation": (
                    "Install a managed build with `syke install-current` or fix the "
                    "local installer tooling, then rerun setup."
                ),
            }

    if not daemon_info.get("installable"):
        detail = daemon_info.get("detail") or "no supported background service is available"
        raise click.ClickException(f"Background service is required for setup: {detail}")

    if daemon_info.get("platform") == "Darwin":
        render_section("Files")
        console.print("  Normal folders under your home directory already work.")
        console.print("  macOS may now ask about Desktop, Documents, and Downloads.")
        console.print("  Choose Allow for folders you want background Syke to read.\n")
        filesystem_access = run_setup_stage(
            "Checking protected folders...",
            lambda: run_macos_filesystem_access_check(user_id),
        )
        _render_macos_filesystem_access(filesystem_access)

    if persist_selected_sources:
        set_selected_sources(user_id, selected_sources)

    source_inventory = {
        cast(str, s["source"]): s
        for s in cast(list[dict[str, object]], inspect_info.get("sources") or [])
    }
    total_files = 0
    for src in selected_sources:
        inv = source_inventory.get(src, {})
        files = cast(int, inv.get("files_found", 0))
        total_files += files
    est_minutes = max(2, total_files // 1500 + 3)
    estimate_method = "max(2, total_files // 1500 + 3)"

    _, log_path, launch_error = _begin_onboarding(
        user_id=user_id,
        selected_sources=selected_sources,
        total_files=total_files,
        estimated_minutes=est_minutes,
        estimate_method=estimate_method,
        persistence=cast(dict[str, object], daemon_info.get("persistence") or {}),
    )
    if launch_error:
        raise click.ClickException(f"Background launch failed: {launch_error}")

    console.print("\n[bold green]✓ Setup complete[/bold green]\n")

    render_section("Ready now")
    console.print('  syke ask "what am I working on?"')
    console.print('  syke record "TODO: finish the API endpoint"')
    console.print('  syke ask "what are my open TODOs this week?"')

    render_section("Building in background")
    for src in selected_sources:
        inv = source_inventory.get(src, {})
        files = cast(int, inv.get("files_found", 0))
        fmt = cast(str, inv.get("format_cluster", ""))
        unit = "db" if fmt == "sqlite" else "files"
        console.print(f"  [dim]…[/dim] {src}  {files:,} {unit}")
    console.print("  [dim]…[/dim] installing skill files to detected harnesses")
    console.print("  [dim]…[/dim] first daemon cycle will synthesize your memex")
    console.print("  [dim]…[/dim] background service is running")
    persistence = cast(dict[str, object], daemon_info.get("persistence") or {})
    if persistence.get("keeps_daemon_alive"):
        console.print(f"  [dim]…[/dim] {persistence.get('manager')} keeps Syke running if it exits")
    console.print(f"\n  [dim]Estimated: ~{est_minutes} minutes[/dim]")

    render_section("What happens next")
    console.print("  Syke watches your agent sessions across harnesses and builds")
    console.print("  a living memex called MEMEX — a map of your current work.")
    console.print("  Every connected harness gets a skill file and a live memex.")
    console.print()
    console.print("  [dim]Your agents already know how to use Syke via the skill file.[/dim]")
    console.print("  [dim]You can also use it directly:[/dim]")
    console.print()
    console.print('    syke ask "…"       [dim]deep recall across all sessions[/dim]')
    console.print('    syke record "…"    [dim]send evidence to the next synthesis[/dim]')
    console.print("    syke memex         [dim]read the current memex[/dim]")
    console.print("    syke status        [dim]check what's connected[/dim]")

    render_section("Monitor")
    console.print(f"  tail -f {log_path}")
    console.print("  syke status")

    console.print()
    console.print("[dim]Try it now:[/dim]")
    console.print('  syke ask "what are my open threads?"')

    if sys.stdin.isatty() and not yes:
        click.pause("\nPress any key to close setup.")
