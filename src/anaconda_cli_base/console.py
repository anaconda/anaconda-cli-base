from typing import List

from readchar import key
from readchar import readkey
from rich.console import Console
from rich.live import Live
from rich.style import Style
from rich.table import Table

__all__ = ["console", "select_from_list"]

SELECTED = Style(color="green", bold=True)

console = Console(soft_wrap=True)


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


def select_from_list(prompt: str, choices: List[str]) -> str:
    """Dynamically select from a list of choices, by using the up/down keys."""
    # inspired by https://github.com/Textualize/rich/discussions/1785#discussioncomment-1883808
    items = choices.copy()

    selected = 0
    with Live(_generate_table(prompt, items, selected), auto_refresh=False) as live:
        while ch := readkey():
            if ch == key.UP or ch == "k":
                selected = max(0, selected - 1)
            if ch == key.DOWN or ch == "j":
                selected = min(len(items) - 1, selected + 1)
            if ch == key.ENTER:
                live.stop()
                return items[selected]
            live.update(_generate_table(prompt, items, selected), refresh=True)

    raise ValueError("Unreachable")
