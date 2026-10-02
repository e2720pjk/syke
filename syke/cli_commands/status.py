"""Status-style command family for the Syke CLI."""

from __future__ import annotations

import json
import sys
from typing import cast

import click

from syke.cli_commands.source import render_sources
from syke.cli_support.context import get_db
from syke.cli_support.daemon_state import daemon_payload
from syke.cli_support.doctor import build_doctor_payload, render_doctor_payload
from syke.cli_support.providers import provider_payload
from syke.cli_support.render import (
    console,
    render_daemon_runtime_summary,
    render_section,
    render_setup_line,
)
from syke.config import user_control_dir
from syke.control import receipt_rollup
from syke.observe.catalog import source_inventory
from syke.onboarding import read_onboarding_state
from syke.source_selection import get_selected_sources


def build_status_payload(db, *, user_id: str, cli_provider: str | None) -> dict[str, object]:
    from syke.daemon.ipc import daemon_ipc_status, daemon_runtime_status
    from syke.metrics import runtime_metrics_status

    memex = db.get_memex(user_id)
    memory_count = int(db.get_graph_stats(user_id)["memories"])
    cycle_count = receipt_rollup(user_control_dir(user_id))["total"]
    selected_sources = get_selected_sources(user_id)
    return {
        "ok": True,
        "user": user_id,
        "initialized": memory_count > 0 or cycle_count > 0,
        "selected_sources": list(selected_sources) if selected_sources is not None else None,
        "selection_mode": "all" if selected_sources is None else "explicit",
        "sources": source_inventory(user_id),
        "onboarding": read_onboarding_state(user_id),
        "provider": provider_payload(cli_provider),
        "daemon": daemon_payload(),
        "daemon_runtime": daemon_runtime_status(user_id),
        "cycle_count": cycle_count,
        "memex": {
            "present": bool(memex),
            "created_at": memex.get("created_at") if memex else None,
            "updated_at": (memex.get("updated_at") or memex.get("created_at")) if memex else None,
            "memory_count": memory_count,
        },
        "runtime_signals": {
            "daemon_ipc": daemon_ipc_status(user_id),
            **runtime_metrics_status(user_id),
        },
    }


@click.command(short_help="Show provider, daemon, source, and memex status.")
@click.option("--json", "use_json", is_flag=True, help="Output as JSON")
@click.pass_context
def status(ctx: click.Context, use_json: bool) -> None:
    user_id = ctx.obj["user"]
    db = get_db(user_id)
    try:
        info = build_status_payload(db, user_id=user_id, cli_provider=ctx.obj.get("provider"))
        if use_json:
            click.echo(json.dumps(info, indent=2))
            return

        console.print(f"\n[bold]syke status[/bold]  [dim]{user_id}[/dim]")
        prov = cast(dict[str, object], info["provider"])
        if prov.get("configured"):
            console.print(
                f"  provider: {prov['id']}  {prov.get('model', '')}  "
                f"[dim]{prov.get('auth_source', '')} · {prov.get('source', '')}[/dim]"
            )
        else:
            error = prov.get("error") or "not configured"
            console.print(f"  [yellow]provider: {error}[/yellow]")
        daemon = cast(dict[str, object], info.get("daemon") or {})
        daemon_runtime = cast(dict[str, object], info.get("daemon_runtime") or {})
        if daemon.get("running") or daemon_runtime.get("reachable"):
            render_daemon_runtime_summary(
                daemon_runtime,
                indent="  ",
                configured_provider=cast(dict[str, object], info["provider"]),
                show_unavailable=True,
            )
        runtime_signals = cast(dict[str, object], info.get("runtime_signals") or {})
        session_history = cast(dict[str, object], runtime_signals.get("session_history") or {})
        daemon_ipc = cast(dict[str, object], runtime_signals.get("daemon_ipc") or {})

        signals: list[tuple[str, str]] = []
        if session_history and not session_history.get("ok", True):
            signals.append(("native session history", str(session_history.get("detail", ""))))
        if daemon_ipc and not daemon_ipc.get("ok", True):
            signals.append(("daemon IPC", str(daemon_ipc.get("detail", ""))))

        if signals:
            render_section("Runtime")
            for name, detail in signals:
                suffix = f"  [dim]{detail}[/dim]" if detail else ""
                console.print(f"  [red]✗[/red] {name}{suffix}")

        render_section("Sources")
        selected_sources = info.get("selected_sources")
        if isinstance(selected_sources, list):
            if selected_sources:
                render_setup_line("selection", ", ".join(str(s) for s in selected_sources))
            else:
                render_setup_line("selection", "none selected")
        else:
            render_setup_line("selection", "all detected sources")
        configured = [row for row in info["sources"] if row["configured_paths"] is not None]
        if configured:
            render_sources(configured)
        onboarding = info.get("onboarding")
        if isinstance(onboarding, dict):
            status_text = str(onboarding.get("status") or "unknown")
            est = onboarding.get("estimated_minutes")
            detail = f"~{est} minutes" if isinstance(est, int) else None
            render_setup_line("onboarding", status_text, detail=detail)

        render_section("Data")
        if not info["initialized"]:
            render_setup_line("data", "none yet", detail="run syke setup")
            return

        console.print(f"  {info['cycle_count']} cycles")

        render_section("Memex")
        memex = cast(dict[str, object], info["memex"])
        if memex["present"]:
            mem_count = memex["memory_count"]
            updated = memex.get("updated_at") or memex.get("created_at") or "unknown"
            console.print(f"  [green]✓[/green] memex  {mem_count} memories  [dim]{updated}[/dim]")
        else:
            console.print("  [dim]✗ memex  not yet built — run syke setup or syke sync[/dim]")
    finally:
        db.close()


@click.command(short_help="Print the current MEMEX.md projection.")
@click.option("--json", "use_json", is_flag=True, help="Output as JSON")
@click.pass_context
def memex(ctx: click.Context, use_json: bool) -> None:
    from syke.memory.memex import get_memex_for_injection

    user_id = ctx.obj["user"]
    db = get_db(user_id)
    try:
        content = get_memex_for_injection(db, user_id)
        if not content:
            if use_json:
                click.echo(json.dumps({"memex": None, "user": user_id}))
            else:
                console.print("[dim]No memex yet. Run: syke setup[/dim]")
            return
        if use_json:
            click.echo(json.dumps({"memex": content, "user": user_id}))
        else:
            click.echo(content)
    finally:
        db.close()


@click.command(short_help="Inspect self-observation and memory trends.")
@click.option("--json", "use_json", is_flag=True, help="Output as JSON")
@click.option("--watch", is_flag=True, help="Live refresh every 30 seconds")
@click.option("--days", "-d", default=7, help="Trend window in days (default: 7)")
@click.pass_context
def observe(ctx: click.Context, use_json: bool, watch: bool, days: int) -> None:
    from syke.health import format_observe, full_observe

    if use_json and watch:
        raise click.UsageError("--json and --watch are mutually exclusive.")

    user_id = ctx.obj["user"]
    db = get_db(user_id)
    try:
        if watch:
            import time

            try:
                while True:
                    click.clear()
                    data = full_observe(db, user_id, days=days)
                    output = format_observe(data)
                    console.print(output)
                    console.print("\n[dim]Refreshing every 30s — Ctrl+C to stop[/dim]")
                    time.sleep(30)
            except KeyboardInterrupt:
                console.print("\n[dim]Stopped.[/dim]")
        else:
            data = full_observe(db, user_id, days=days)
            if use_json:
                click.echo(json.dumps(data, indent=2, default=str))
            else:
                output = format_observe(data)
                console.print(output)
    finally:
        db.close()


@click.command(short_help="Verify auth, runtime, DB, daemon, and memex health.")
@click.option("--json", "use_json", is_flag=True, help="Output as JSON")
@click.pass_context
def doctor(ctx: click.Context, use_json: bool) -> None:
    payload = build_doctor_payload(
        ctx,
        verify_filesystem=not use_json and sys.stdin.isatty(),
    )
    if use_json:
        click.echo(json.dumps(payload, indent=2))
    else:
        render_doctor_payload(payload)
    if not payload.get("ok", False):
        ctx.exit(1)


@click.command(short_help="Install adapter markdowns for all known harnesses.")
@click.pass_context
def connect(ctx: click.Context) -> None:
    from syke.observe.bootstrap import ensure_adapters
    from syke.runtime.workspace import WORKSPACE_ROOT

    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    results = ensure_adapters(WORKSPACE_ROOT)
    for r in results:
        if r.status == "installed":
            console.print(f"[green]\u2713[/green] {r.source}: installed ({r.detail})")
        elif r.status == "existing":
            console.print(f"[dim]\u2713 {r.source}: already present[/dim]")
        else:
            console.print(f"[yellow]- {r.source}: {r.status} ({r.detail})[/yellow]")
