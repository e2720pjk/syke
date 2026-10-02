"""Public CLI entrypoint for Syke."""

from __future__ import annotations

import os

import click

from syke import __version__
from syke.cli_commands.ask import ask
from syke.cli_commands.auth import auth
from syke.cli_commands.config import config
from syke.cli_commands.daemon import daemon, self_update
from syke.cli_commands.internal import macos_filesystem_probe
from syke.cli_commands.maintenance import cost, install_current, sync
from syke.cli_commands.pi import pi
from syke.cli_commands.record import record
from syke.cli_commands.setup import setup
from syke.cli_commands.source import source
from syke.cli_commands.status import connect, doctor, memex, observe, status
from syke.cli_commands.web import web
from syke.cli_support.dashboard import show_dashboard
from syke.config import DEFAULT_USER

PRIMARY_COMMANDS = (
    "setup",
    "ask",
    "memex",
    "record",
    "status",
    "sync",
    "source",
    "auth",
    "doctor",
    "web",
)

ADVANCED_COMMANDS = (
    "daemon",
    "pi",
    "config",
    "connect",
    "cost",
    "observe",
    "self-update",
    "install-current",
)


class SykeGroup(click.Group):
    """Top-level CLI group with product-oriented help sections."""

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        commands: dict[str, click.Command] = {}
        for subcommand in self.list_commands(ctx):
            cmd = self.get_command(ctx, subcommand)
            if cmd is None or cmd.hidden:
                continue
            commands[subcommand] = cmd

        if not commands:
            return

        def _rows(names: tuple[str, ...]) -> list[tuple[str, str]]:
            rows: list[tuple[str, str]] = []
            for name in names:
                cmd = commands.get(name)
                if cmd is None:
                    continue
                rows.append((name, cmd.get_short_help_str(formatter.width) or ""))
            return rows

        primary_rows = _rows(PRIMARY_COMMANDS)
        advanced_rows = _rows(ADVANCED_COMMANDS)
        for title, rows in (
            ("Primary Commands", primary_rows),
            ("Advanced Commands", advanced_rows),
        ):
            if not rows:
                continue
            with formatter.section(title):
                formatter.write_dl(rows)


@click.group(
    cls=SykeGroup,
    invoke_without_command=True,
    epilog='\b\nExamples:\n  syke setup\n  syke ask "What changed this week?"\n  syke memex',
)
@click.option(
    "--user",
    "-u",
    default=DEFAULT_USER,
    help="Person ID for this installation (one shared store)",
)
@click.option("--verbose", "-v", is_flag=True, help="Verbose logging")
@click.option("--provider", "-p", default=None, help="Override LLM provider for this invocation")
@click.version_option(__version__)
@click.pass_context
def cli(ctx: click.Context, user: str, verbose: bool, provider: str | None) -> None:
    """Syke — Local memory for your AI tools."""
    ctx.ensure_object(dict)
    ctx.obj["user"] = user
    ctx.obj["provider"] = provider

    if provider:
        os.environ["SYKE_PROVIDER"] = provider

    from syke.metrics import setup_logging

    setup_logging(
        user,
        verbose=verbose,
        file_logging=ctx.invoked_subcommand != "setup",
    )

    if ctx.invoked_subcommand is None:
        show_dashboard(user)


cli.add_command(setup)
cli.add_command(ask)
cli.add_command(record)
cli.add_command(status)
cli.add_command(sync)
cli.add_command(source)
cli.add_command(auth)
cli.add_command(memex)
cli.add_command(observe)
cli.add_command(doctor)
cli.add_command(connect)
cli.add_command(config)
cli.add_command(daemon)
cli.add_command(pi)
cli.add_command(self_update)
cli.add_command(cost)
cli.add_command(install_current)
cli.add_command(web)
cli.add_command(macos_filesystem_probe)
