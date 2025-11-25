import logging
import os
from typing import List

import click
import readchar
from readchar import key
from rich.console import Console
from rich.live import Live
from rich.logging import RichHandler
from rich.style import Style
from rich.table import Table

__all__ = ["console", "select_from_list"]

SELECTED = Style(color="green", bold=True)

console = Console(soft_wrap=True)


def init_logging() -> None:
    # TODO: We only enable logging in debug level for now
    #       This is because anaconda-client uses logging for normal
    #       program output and this really warrants a much larger
    #       redesign. In anaconda-cli-base, we currently only use
    #       the logger for debug printing, so we can just do that
    #       here.
    log_level = os.getenv("LOGLEVEL", "INFO").upper()
    if log_level == "DEBUG":
        logging.basicConfig(
            level=log_level,
            format="%(message)s",
            datefmt="[%X]",
            handlers=[RichHandler()],
        )


def _generate_table(header: str, rows: List[str], selected: int) -> Table:
    table = Table(box=None)

    table.add_column(header)

    for i, row in enumerate(rows):
        if i == selected:
            style = SELECTED
            value = f"* {row}"
        else:
            style = None
            value = f"  {row}"
        table.add_row(value, style=style)

    return table


def _read_key() -> str:
    """Read a key from the terminal. We use click if we are not on Windows, but must
    use `readchar.readkey()` on Windows since keys like UP/DOWN are multiple characters.

    We can probably just use readkey() for all OS's, but that is proving challenging to mock.
    """
    return readchar.readkey()
    try:
        import msvcrt  # noqa: F401
    except ImportError:
        return click.getchar()
    else:
        return readchar.readkey()


def select_from_list(prompt: str, choices: List[str]) -> str:
    """Dynamically select from a list of choices, by using the up/down keys."""
    # inspired by https://github.com/Textualize/rich/discussions/1785#discussioncomment-1883808
    items = choices.copy()

    selected = 0
    with Live(_generate_table(prompt, items, selected), auto_refresh=False) as live:
        while keypress := _read_key():
            if keypress == key.UP or keypress == "k":
                selected = max(0, selected - 1)
            if keypress == key.DOWN or keypress == "j":
                selected = min(len(items) - 1, selected + 1)
            if keypress in ["\n", "\r", key.ENTER]:
                live.stop()
                return items[selected]
            live.update(_generate_table(prompt, items, selected), refresh=True)

    raise ValueError("Unreachable")
