"""Register local evidence paths without running setup or synthesis."""

from __future__ import annotations

import json
from pathlib import Path

import click

from syke.observe.catalog import get_source, source_inventory
from syke.source_selection import register_source_path, remove_source_path


def _run(action, use_json: bool) -> dict:
    try:
        info = action()
    except (OSError, ValueError) as exc:
        if use_json:
            click.echo(json.dumps({"ok": False, "error": str(exc)}))
            raise click.exceptions.Exit(1) from exc
        raise click.ClickException(str(exc)) from exc
    if use_json:
        click.echo(json.dumps({"ok": True, **info}, indent=2))
    return info


def render_sources(rows: list[dict]) -> None:
    for row in rows:
        click.echo(
            f"{row['source']}: enabled={'yes' if row['enabled'] else 'no'} "
            f"supported=yes recognized={'yes' if row['recognized'] else 'no'} "
            f"readable={'yes' if row['readable'] else 'no'} state={row['state']}"
        )
        for root in row.get("roots", [row.get("path")]):
            if root:
                click.echo(f"  path: {root}")
        for archive in row["archives"]:
            retained = archive["retained_conversations"]
            click.echo(
                f"  archive: {archive['path']} · "
                f"{archive['saved_conversations']} saved conversations"
                f" · retained: {retained if retained is not None else 'unknown'}"
                f" · inventory: {archive['inventory_generated_at'] or 'unknown'}"
            )
        for warning in row.get("warnings", []):
            click.echo(f"  Warning: {warning}")
        if row.get("configuration_error"):
            click.echo(f"  Error: {row['configuration_error']}")
    click.echo(
        "Runtime permissions are unverified here. "
        "Archives are local snapshots, not live account state."
    )


@click.group(short_help="Add, inspect, or remove local evidence source paths.")
def source() -> None:
    pass


@source.command("add", short_help="Validate a local archive path and enable its source.")
@click.argument("source_id")
@click.argument("path", type=Path)
@click.option("--json", "use_json", is_flag=True, help="Output as JSON")
@click.pass_context
def add(ctx: click.Context, source_id: str, path: Path, use_json: bool) -> None:
    info = _run(lambda: register_source_path(ctx.obj["user"], source_id, path), use_json)
    if not use_json:
        click.echo("Added and enabled." if info["changed"] else "Already registered and enabled.")
        render_sources([info])
        click.echo(
            'Next: syke ask "your question" (after setup); '
            "syke sync only if you want synthesis now."
        )


@source.command("list", short_help="Show recognition, activation, paths, and archive boundaries.")
@click.argument("source_id", required=False)
@click.option("--json", "use_json", is_flag=True, help="Output as JSON")
@click.pass_context
def list_sources(ctx: click.Context, source_id: str | None, use_json: bool) -> None:
    def inspect() -> dict:
        if source_id and get_source(source_id) is None:
            raise ValueError(f"Unknown source: {source_id}")
        rows = source_inventory(ctx.obj["user"])
        return {"sources": [row for row in rows if not source_id or row["source"] == source_id]}

    info = _run(inspect, use_json)
    if not use_json:
        render_sources(info["sources"])


@source.command("remove", short_help="Unregister a path without deleting archive or memory.")
@click.argument("source_id")
@click.argument("path", type=Path)
@click.option("--json", "use_json", is_flag=True, help="Output as JSON")
@click.pass_context
def remove(ctx: click.Context, source_id: str, path: Path, use_json: bool) -> None:
    info = _run(lambda: remove_source_path(ctx.obj["user"], source_id, path), use_json)
    if not use_json:
        click.echo(f"Unregistered: {info['removed']}")
        click.echo(f"Source enabled: {'yes' if info['enabled'] else 'no'}")
        click.echo("Archive files, graph, and MEMEX were not deleted or rewritten.")
