"""Local Pi runtime management, independent of Syke releases."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import click

from syke.cli_support.exit_codes import EXIT_RUNTIME, SykeRuntimeException
from syke.llm import pi_install, pi_update


def _exact_version(_ctx: click.Context, _param: click.Parameter, value: str | None) -> str | None:
    if value is not None and pi_install._EXACT_VERSION.fullmatch(value) is None:
        raise click.BadParameter("use an exact version, for example 1.0.0 or 1.1.0-rc.1")
    return value


@contextmanager
def _daemon_transition(user_id: str) -> Iterator[None]:
    """Pause a managed daemon only for activation, after download and probing."""
    from syke.cli_support.daemon_state import (
        daemon_payload,
        wait_for_daemon_shutdown,
        wait_for_daemon_startup,
    )
    from syke.daemon.daemon import install_and_start, stop_and_unload

    snapshot = daemon_payload()
    if not snapshot.get("running"):
        yield
        return
    if not snapshot.get("registered"):
        raise RuntimeError(
            "A foreground daemon is running. Stop it first, or use --no-restart "
            "and restart it yourself after the runtime switch."
        )
    click.echo("Stopping the managed daemon for the runtime switch...", err=True)
    stop_and_unload()
    stopped = wait_for_daemon_shutdown(user_id)
    if stopped.get("running") or stopped.get("registered"):
        raise RuntimeError("Daemon did not stop cleanly; the Pi runtime was not switched")
    try:
        yield
    finally:
        # The manager releases the installation lock before this exit handler.
        click.echo("Restarting the managed daemon...", err=True)
        try:
            install_and_start(user_id)
            started = wait_for_daemon_startup(user_id)
            ipc = started.get("ipc") or {}
            if not started.get("running") or not isinstance(ipc, dict) or not ipc.get("ok"):
                raise RuntimeError("the daemon did not become ready")
        except Exception as exc:
            raise RuntimeError(
                f"Daemon restart failed after the Pi runtime operation: {exc}. "
                "Run `syke pi status`, then `syke daemon start` or `syke pi rollback`."
            ) from exc


def _show_status(payload: dict[str, Any]) -> None:
    click.echo(f"Pi version:       {payload['version'] or 'not installed'}")
    click.echo(f"Default tested:   {payload['defaultVersion']} (first install only)")
    click.echo(f"Runtime:          {payload['runtimePath']}")
    click.echo(f"Previous version: {payload['previousVersion'] or 'none'}")
    if payload.get("error"):
        click.echo(f"Problem:          {payload['error']}")
    elif payload["healthy"]:
        click.echo("Local pins:       valid")


@click.group()
def pi() -> None:
    """Manage your private Pi runtime and choose when to update it."""


@pi.command("status")
@click.option("--json", "as_json", is_flag=True, help="Emit local runtime metadata as JSON.")
@click.pass_context
def pi_status(ctx: click.Context, as_json: bool) -> None:
    """Show the local choice without installing or launching Pi."""
    payload = pi_update.get_pi_runtime_status()
    if as_json:
        click.echo(json.dumps(payload))
    else:
        _show_status(payload)
    if payload.get("error"):
        ctx.exit(EXIT_RUNTIME)


@pi.command("update")
@click.option(
    "--check", "check_only", is_flag=True, help="Check the registry without installing Pi."
)
@click.option(
    "--version",
    callback=_exact_version,
    help="Select an exact release, including an older release.",
)
@click.option("--no-restart", is_flag=True, help="Leave any running daemon on its current runtime.")
@click.option("--json", "as_json", is_flag=True, help="Emit the result as JSON.")
@click.pass_context
def pi_update_command(
    ctx: click.Context,
    check_only: bool,
    version: str | None,
    no_restart: bool,
    as_json: bool,
) -> None:
    """Update to latest stable, or explicitly choose a release with --version."""
    try:
        if check_only:
            payload = pi_update.check_pi_update(version)
        else:
            guard = None if no_restart else lambda: _daemon_transition(ctx.obj["user"])
            payload = pi_update.update_pi_runtime(version, activation_guard=guard)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        raise SykeRuntimeException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(payload))
    elif check_only:
        _show_status(payload)
        click.echo(f"Target version:   {payload['targetVersion']}")
        click.echo("Update available." if payload["updateAvailable"] else "No update needed.")
    elif payload["changed"]:
        click.echo(
            f"Pi updated: {payload['previousVersion'] or 'not installed'} -> {payload['version']}"
        )
    else:
        click.echo(
            f"Pi {payload['version']} is already selected "
            f"(latest target: {payload['targetVersion']})."
        )
    if check_only and payload.get("error"):
        ctx.exit(EXIT_RUNTIME)
    if not check_only and payload["changed"] and no_restart and not as_json:
        click.echo("Restart a running daemon to use the new runtime.")


@pi.command("rollback")
@click.option("--no-restart", is_flag=True, help="Leave any running daemon on its current runtime.")
@click.option("--json", "as_json", is_flag=True, help="Emit the result as JSON.")
@click.pass_context
def pi_rollback(ctx: click.Context, no_restart: bool, as_json: bool) -> None:
    """Switch to the previous locally installed release, after compatibility checks."""
    try:
        guard = None if no_restart else lambda: _daemon_transition(ctx.obj["user"])
        payload = pi_update.rollback_pi_runtime(activation_guard=guard)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        raise SykeRuntimeException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(payload))
    else:
        click.echo(f"Pi rolled back to {payload['version']}.")
        if no_restart:
            click.echo("Restart a running daemon to use the selected runtime.")
