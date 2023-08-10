import logging
from importlib.metadata import entry_points
from sys import version_info

from typer import Typer
from typer.models import DefaultPlaceholder

log = logging.getLogger(__name__)

PLUGIN_GROUP_NAME = "anaconda_cli.subcommand"


def load_registered_subcommands(app: Typer) -> None:
    """Load all subcommands from plugins."""
    # The API was changed in Python 3.10, see https://docs.python.org/3/library/importlib.metadata.html#entry-points
    if version_info.major == 3 and version_info.minor <= 9:
        subcommand_entry_points = entry_points().get(PLUGIN_GROUP_NAME, [])
    else:
        subcommand_entry_points = entry_points().select(group=PLUGIN_GROUP_NAME)  # type: ignore

    for subcommand_entry_point in subcommand_entry_points:
        # Load the entry point and register it as a subcommand with the base CLI app
        subcommand_app = subcommand_entry_point.load()  # type: ignore

        # Allow plugins to disable this if they explicitly want to, but otherwise make True the default
        if isinstance(subcommand_app.info.no_args_is_help, DefaultPlaceholder):
            subcommand_app.info.no_args_is_help = True

        app.add_typer(subcommand_app, name=subcommand_entry_point.name)  # type: ignore
        log.debug(
            "Loaded subcommand '%s' from '%s'",
            subcommand_entry_point.name,  # type: ignore
            subcommand_entry_point.value,  # type: ignore
        )
