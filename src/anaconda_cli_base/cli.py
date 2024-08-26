import functools
import logging
import os
from typing import Callable
from typing import Dict
from typing import Optional
from typing import Union

import click
import typer
from typing_extensions import Annotated

from anaconda_cli_base import __version__
from anaconda_cli_base import console
from anaconda_cli_base.plugins import load_registered_subcommands

app = typer.Typer(add_completion=False, help="Welcome to the Anaconda CLI!")

log = logging.getLogger(__name__)


@app.callback(invoke_without_command=True, no_args_is_help=True)
def main(
    ctx: typer.Context,
    token: Optional[str] = typer.Option(
        None,
        "-t",
        "--token",
        help="Authentication token to use. A token string or path to a file containing a token",
        hidden=True,
    ),
    site: Optional[str] = typer.Option(
        None,
        "-s",
        "--site",
        help="select the anaconda-client site to use",
        hidden=True,
    ),
    disable_ssl_warnings: Optional[bool] = typer.Option(
        False,
        help="Disable SSL warnings",
        hidden=True,
    ),
    show_traceback: Optional[bool] = typer.Option(
        False,
        help="Show the full traceback for chalmers user errors",
        hidden=True,
    ),
    verbose: Optional[bool] = typer.Option(
        False,
        "-v",
        "--verbose",
        help="print debug information on the console",
        hidden=True,
    ),
    quiet: Optional[bool] = typer.Option(
        False,
        "-q",
        "--quiet",
        help="Only show warnings or errors on the console",
        hidden=True,
    ),
    show_help: Optional[bool] = typer.Option(
        False,
        "-h",
        "--help",
        help="Show this message and exit.",
    ),
    version: Optional[bool] = typer.Option(
        None, "-V", "--version", help="Show version and exit."
    ),
) -> None:
    """Anaconda CLI."""
    if show_help:
        console.print(ctx.get_help())
        raise typer.Exit()

    if version:
        console.print(
            f"Anaconda CLI, version [cyan]{__version__}[/cyan]",
            style="bold green",
        )
        raise typer.Exit()


def _load_auth_handlers(auth_handlers: Dict[str, typer.Typer]) -> None:
    at_choices = click.Choice(list(auth_handlers))

    def _action(
        ctx: typer.Context,
        at: Annotated[
            str, typer.Option(click_type=at_choices, prompt="at", show_choices=True)
        ],
        help: bool = typer.Option(False, "--help"),
    ) -> None:
        handler = auth_handlers[at]

        args = ("--help",) if help else ctx.args
        return handler(args=[ctx.command.name, *args])

    for action in "login", "logout", "whoami":
        decorator = app.command(
            action,
            context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
        )
        decorator(_action)


app._load_auth_handlers = _load_auth_handlers  # type: ignore

disable_plugins = bool(os.getenv("ANACONDA_CLI_DISABLE_PLUGINS"))
if not disable_plugins:
    load_registered_subcommands(app)


def _select_main_entrypoint_app(app_: typer.Typer) -> Union[typer.Typer, Callable]:
    """Select the main application to handle the `anaconda` entrypoint at the command line.

    This function, and its execution below at the bottom of this module, can be removed once
    we are fully confident that the `binstar_client.scripts.cli` CLI application (defined
    inside `anaconda-client`) can be replaced with the modern `click`/`typer`-based application.

    If there are no additional plugins registered besides `anaconda-client`, then we fall back
    to the legacy CLI. If any additional plugins are installed, we use the new CLI.

    One can force usage of the legacy CLI by setting the environment variable
    `ANACONDA_CLIENT_FORCE_STANDALONE` to any value (e.g. `1`).

    Users are encouraged to only use the fallback in cases where the new CLI breaks existing usage.
    Please register a bug in that case.

    """
    subcommands = [g.name for g in app_.registered_groups]

    anaconda_client_is_only_plugin = subcommands == ["org"]
    force_new_cli_entrypoint = bool(os.getenv("ANACONDA_CLI_FORCE_NEW"))
    force_legacy_cli_entrypoint = bool(os.getenv("ANACONDA_CLIENT_FORCE_STANDALONE"))
    if force_legacy_cli_entrypoint and force_new_cli_entrypoint:
        raise ValueError(
            "Cannot set both ANACONDA_CLI_FORCE_NEW and ANACONDA_CLIENT_FORCE_STANDALONE at the same time"
        )

    use_legacy_cli_entrypoint = force_legacy_cli_entrypoint or (
        anaconda_client_is_only_plugin and not force_new_cli_entrypoint
    )
    if use_legacy_cli_entrypoint:
        # TODO: We may want to do the conditional import first, and load the subcommand name from anaconda-client
        try:
            from binstar_client.scripts.cli import main
        except ImportError:
            pass
        else:
            return functools.partial(main, allow_plugin_main=False)

    return app_


# Here, we re-assign the global `app` variable based on the selection logic.
# This should be removed once we are confident that we can completely replace the
# `binstar_client` CLI (that inside `anaconda-client`) with the modern
# `click`/`typer`-based application.
app = _select_main_entrypoint_app(app)  # type: ignore
