from __future__ import annotations

import os
from functools import partial
from importlib import reload
from pathlib import Path
from typing import IO
from typing import Any
from typing import Mapping
from typing import Optional
from typing import Protocol
from typing import Sequence
from typing import Union
from typing import cast
from typing import Generator

import pytest
import typer
from pytest import MonkeyPatch
from typer.testing import CliRunner
from typer.testing import Result

# Force usage of new CLI
os.environ["ANACONDA_CLI_FORCE_NEW"] = "true"
os.environ["ANACONDA_CLI_DISABLE_PLUGINS"] = "true"

import anaconda_cli_base.cli


class CLIInvoker(Protocol):
    def __call__(
        self,
        # app: typer.Typer,
        args: Optional[Union[str, Sequence[str]]] = None,
        input: Optional[Union[bytes, str, IO[Any]]] = None,
        env: Optional[Mapping[str, str]] = None,
        catch_exceptions: bool = True,
        color: bool = False,
        **extra: Any,
    ) -> Result: ...


@pytest.fixture()
def tmp_cwd(monkeypatch: MonkeyPatch, tmp_path: Path) -> Path:
    """Create & return a temporary directory after setting current working directory to it."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def prepare_app() -> Generator[None, None, None]:
    reload(anaconda_cli_base.cli)

    @anaconda_cli_base.cli.app.command("some-test-subcommand")
    def some_test_subcommand() -> None:
        raise typer.Exit()

    yield


@pytest.fixture()
@pytest.mark.usefixtures("tmp_cwd")
def invoke_cli() -> CLIInvoker:
    """Returns a function, which can be used to call the CLI from within a temporary directory."""

    runner = CliRunner()

    return partial(runner.invoke, cast(typer.Typer, anaconda_cli_base.cli.app))
