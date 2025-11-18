import logging
import warnings
from importlib.metadata import EntryPoint
from importlib.metadata import entry_points
from sys import version_info
from typing import Any
from typing import Dict
from typing import List
from typing import Tuple
from typing import cast

import typer
from typer.models import DefaultPlaceholder

from anaconda_cli_base.console import console, select_from_list

log = logging.getLogger(__name__)

PLUGIN_GROUP_NAME = "anaconda_cli.subcommand"

PluginName = str
ModuleName = str


def _load_entry_points_for_group(
    group: str,
) -> List[Tuple[PluginName, ModuleName, typer.Typer]]:
    # The API was changed in Python 3.10, see https://docs.python.org/3/library/importlib.metadata.html#entry-points
    found_entry_points: tuple
    if version_info.major == 3 and version_info.minor <= 9:
        found_entry_points = cast(
            Tuple[EntryPoint, ...], entry_points().get(group, tuple())
        )
    else:
        found_entry_points = tuple(entry_points().select(group=group))  # type: ignore

    loaded = []
    for entry_point in found_entry_points:
        with warnings.catch_warnings():
            # Suppress anaconda-cloud-auth rename warnings just during entrypoint load
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            module: typer.Typer = entry_point.load()
        loaded.append((entry_point.name, entry_point.value, module))

    return loaded


AUTH_HANDLER_ALIASES = {
    "cloud": "anaconda.com",
    "org": "anaconda.org",
}


def _load_auth_handlers(
    app: typer.Typer,
    auth_handlers: Dict[str, typer.Typer],
    auth_handlers_dropdown: List[str],
) -> None:
    def validate_at(ctx: typer.Context, _: Any, choice: str) -> str:
        show_help = ctx.params.get("help", False) is True
        if show_help:
            help_str = ctx.get_help()
            console.print(help_str)
            raise typer.Exit()

        if choice is None:
            if len(auth_handlers_dropdown) > 1:
                choice = select_from_list("choose destination:", auth_handlers_dropdown)
            else:
                # If only one is available, we don't need a picker
                (choice,) = auth_handlers_dropdown

        elif choice not in auth_handlers:
            print(
                f"{choice} is not an allowed value for --at. Use one of {auth_handlers_dropdown}"
            )
            raise typer.Abort()
        return choice

    def _action(
        ctx: typer.Context,
        at: str = typer.Option(
            None, callback=validate_at, help=f"Choose from {auth_handlers_dropdown}"
        ),
        help: bool = typer.Option(False, "--help"),
    ) -> None:
        handler = auth_handlers[at]

        args = ("--help",) if help else ctx.args
        return handler(args=[ctx.command.name, *args], obj=ctx.obj)

    help_doc = {
        "login": "Sign into Anaconda services",
        "logout": "Sign out from Anaconda services",
        "whoami": "Display account information",
    }

    for action in "login", "logout", "whoami":
        decorator = app.command(
            action,
            context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
            rich_help_panel="Authentication",
            help=help_doc[action],
        )
        decorator(_action)


def load_registered_subcommands(app: typer.Typer) -> None:
    """Load all subcommands from plugins."""
    subcommand_entry_points = _load_entry_points_for_group(PLUGIN_GROUP_NAME)
    auth_handlers: Dict[str, typer.Typer] = {}
    auth_handler_selectors: List[str] = []
    for name, value, subcommand_app in subcommand_entry_points:
        # Allow plugins to disable this if they explicitly want to, but otherwise make True the default
        if isinstance(subcommand_app.info.no_args_is_help, DefaultPlaceholder):
            subcommand_app.info.no_args_is_help = True

        if "login" in [cmd.name for cmd in subcommand_app.registered_commands]:
            auth_handlers[name] = subcommand_app
            alias = AUTH_HANDLER_ALIASES.get(name)
            if alias:
                auth_handlers[alias] = subcommand_app
                auth_handler_selectors.append(alias)

        app.add_typer(subcommand_app, name=name, rich_help_panel="Plugins")

    if auth_handlers:
        auth_handlers_dropdown = sorted(auth_handler_selectors)

        _load_auth_handlers(
            app=app,
            auth_handlers=auth_handlers,
            auth_handlers_dropdown=auth_handlers_dropdown,
        )

        log.debug(
            "Loaded subcommand '%s' from '%s'",
            name,
            value,
        )
