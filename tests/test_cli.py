from typing import Tuple

import pytest

from anaconda_cli_base import __version__

from .conftest import CLIInvoker


@pytest.mark.parametrize(
    "args",
    [
        pytest.param((), id="no-args"),
        pytest.param(("--help",), id="--help"),
    ],
)
def test_cli_help(invoke_cli: CLIInvoker, args: Tuple[str]) -> None:
    result = invoke_cli(*args)
    assert result.exit_code == 0
    assert "Welcome to the Anaconda CLI!" in result.stdout


def test_cli_version(invoke_cli: CLIInvoker) -> None:
    result = invoke_cli("--version")
    assert result.exit_code == 0
    assert f"Anaconda CLI, version {__version__}" in result.stdout
