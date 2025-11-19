import logging
import sys
import warnings
from importlib.metadata import EntryPoint
from importlib.metadata import entry_points
from sys import version_info
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import cast

import typer
from typer.models import DefaultPlaceholder

from anaconda_cli_base.console import console, select_from_list

log = logging.getLogger(__name__)

PLUGIN_GROUP_NAME = "anaconda_cli.subcommand"

# Plugins which are available but hidden from help text
HIDDEN_PLUGINS = ["cloud"]

PluginName = str
ModuleName = str


def _load_entry_points_for_group(
    group: str,
) -> List[Tuple[PluginName, ModuleName, typer.Typer]]:
    # The API was changed in Python 3.10, see https://docs.python.org/3/library/importlib.metadata.html#entry-points
    found_entry_points: tuple
    if version_info.major == 3 and version_info.minor <= 9:
        found_entry_points = cast(
            Tuple[EntryPoint, ...],
            entry_points().get(group, tuple()),  # type:ignore
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


def _select_auth_handler_and_args(
    *,
    ctx: typer.Context,
    at: Optional[str],
    hostname: Optional[str],
    username: Optional[str],
    password: Optional[str],
    help: bool,
    auth_handlers: Dict[str, typer.Typer],
    auth_handlers_dropdown: List[str],
) -> Tuple[Callable, list[str]]:
    """Select the appropriate auth handler, and construct its arguments, depending on
    user input. Isolated to enable better testing to support legacy anaconda.org login
    flows.
    """
    # If we use one of the legacy anaconda-client parameters, we implicitly select
    # anaconda.org for the user.
    if hostname or username or password:
        at = "anaconda.org"

    # Present a picker if the user doesn't use the --at site option
    if at is None:
        if len(auth_handlers_dropdown) > 1:
            at = select_from_list("choose destination:", auth_handlers_dropdown)
        else:
            # If only one is available, we don't need a picker
            (at,) = auth_handlers_dropdown

    if at not in auth_handlers:
        print(
            f"{at} is not an allowed value for --at. Use one of {auth_handlers_dropdown}"
        )
        raise typer.Abort()

    handler = auth_handlers[at]

    # Consolidate the arguments to pass into the handler function
    if help:
        args = ["--help"]
    elif at == "anaconda.org":
        # In order to support legacy anaconda-client login arguments, we need to do some
        # manual argument parsing to pass into the handler function

        # Reconstruct the valid options
        legacy_client_args = []
        if hostname:
            legacy_client_args.extend(["--hostname", hostname])
        if username:
            legacy_client_args.extend(["--username", username])
        if password:
            legacy_client_args.extend(["--password", password])

        # We reconstruct sys.argv, dropping everything after the
        # "login/logout/whoami" subcommand, and replacing with any passed options
        def _find_subcommand_index(subcommands: list[str]) -> int:
            subcommands_str = "/".join(subcommands)
            for s in subcommands:
                try:
                    return sys.argv.index(s)
                except ValueError:
                    pass

            raise ValueError(f"Must use a valid subcommand '{subcommands_str}'")

        # Extend sys.argv so we grab everything including the entrypoint and
        # the subcommand, but drop any options
        subcommand_index = _find_subcommand_index(["login", "logout", "whoami"])
        sys.argv = sys.argv[: subcommand_index + 1] + legacy_client_args

        args = legacy_client_args
    else:
        args = ctx.args
    return handler, args


def _load_auth_handlers(
    app: typer.Typer,
    auth_handlers: Dict[str, typer.Typer],
    auth_handlers_dropdown: List[str],
) -> None:
    # this ensures that we can reach the help message
    # for the handler chosen by the --at flag if it appears
    # before --help
    def handler_help(ctx: typer.Context, _: Any, at: Optional[str]) -> Optional[str]:
        show_help = ctx.params.get("help", False) is True
        if show_help:
            help_str = ctx.get_help()
            console.print(help_str)
            raise typer.Exit()

        return at

    def _action(
        ctx: typer.Context,
        at: Optional[str] = typer.Option(
            None, help=f"Choose from {auth_handlers_dropdown}", callback=handler_help
        ),
        # Legacy options from anaconda-client login subcommand
        hostname: Optional[str] = typer.Option(None, hidden=True),
        username: Optional[str] = typer.Option(None, hidden=True),
        password: Optional[str] = typer.Option(None, hidden=True),
        help: bool = typer.Option(False, "--help"),
    ) -> None:
        handler, args = _select_auth_handler_and_args(
            ctx=ctx,
            at=at,
            hostname=hostname,
            username=username,
            password=password,
            help=help,
            auth_handlers=auth_handlers,
            auth_handlers_dropdown=auth_handlers_dropdown,
        )
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
            # this means anaconda-auth is available
            if name == "auth":
                try:
                    from anaconda_auth.config import AnacondaAuthSitesConfig

                    site_config = AnacondaAuthSitesConfig()
                    for site_name, site in site_config.sites.root.items():
                        if site_name == site.domain:
                            entry = site_name
                        else:
                            # entry = f"{site_name} ({site.domain})"
                            entry = (site_name, f"({site.domain})")  # type: ignore

                        auth_handlers[entry] = subcommand_app

                        if site_name == site_config.default_site:
                            # entry = f"{entry} (default)"
                            entry = (*entry, "(default)")  # type: ignore
                            auth_handler_selectors.insert(0, entry)  # type: ignore
                        else:
                            auth_handler_selectors.append(entry)  # type: ignore
                except ImportError as e:
                    raise e
            elif name == "cloud":
                pass
            elif alias:
                auth_handlers[alias] = subcommand_app
                auth_handler_selectors.append(alias)

        app.add_typer(
            subcommand_app,
            name=name,
            hidden=name in HIDDEN_PLUGINS,
            rich_help_panel="Plugins",
        )

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
