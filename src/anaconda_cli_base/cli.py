from typing import Optional

import typer

from anaconda_cli_base import __version__
from anaconda_cli_base import console
from anaconda_cli_base.plugins import load_registered_subcommands

app = typer.Typer(add_completion=False, help="Welcome to the Anaconda CLI!")

load_registered_subcommands(app)


@app.callback(invoke_without_command=True, no_args_is_help=True)
def main(
    version: Optional[bool] = typer.Option(
        None, "--version", help="Show version and exit."
    )
) -> None:
    """Anaconda CLI."""
    if version:
        console.print(
            f"Anaconda CLI, version [cyan]{__version__}[/cyan]",
            style="bold green",
        )
        raise typer.Exit()
