from functools import partial
from typing import Tuple
from typing import cast
from typing import Optional, Sequence, Callable, Generator

import pytest
import typer
from unittest.mock import MagicMock
from pytest import MonkeyPatch
from pytest_mock import MockerFixture

from anaconda_cli_base import __version__
import anaconda_cli_base.cli
from anaconda_cli_base.cli import _select_main_entrypoint_app
from anaconda_cli_base.plugins import load_registered_subcommands

from .conftest import CLIInvoker

ENTRY_POINT_TUPLE = Tuple[str, str, typer.Typer]


@pytest.mark.parametrize(
    "args",
    [
        pytest.param((), id="no-args"),
        pytest.param(("--help",), id="--help"),
        pytest.param(("-h",), id="-h"),
    ],
)
def test_cli_help(invoke_cli: CLIInvoker, args: Tuple[str]) -> None:
    result = invoke_cli(args)
    assert result.exit_code == 0
    assert "Welcome to the Anaconda CLI!" in result.stdout


def test_cli_version(invoke_cli: CLIInvoker) -> None:
    result = invoke_cli(["--version"])
    assert result.exit_code == 0
    assert f"Anaconda CLI, version {__version__}" in result.stdout


@pytest.mark.parametrize(
    "args",
    [
        pytest.param((), id="no-args"),
        pytest.param(("-t", "TOKEN"), id="-t"),
        pytest.param(("--token", "TOKEN"), id="--token"),
        pytest.param(("-s", "SITE"), id="-s"),
        pytest.param(("--site", "SITE"), id="--site"),
        pytest.param(("--disable-ssl-warnings",), id="--disable-ssl-warnings"),
        pytest.param(("--show-traceback",), id="--show-traceback"),
        pytest.param(("-v",), id="-v"),
        pytest.param(("--verbose",), id="--verbose"),
        pytest.param(("-q",), id="-q"),
        pytest.param(("--quiet",), id="--quiet"),
    ],
)
def test_cli_root_options_passthrough(invoke_cli: CLIInvoker, args: Tuple[str]) -> None:
    """Here, we make sure that the root options from anaconda-client are allowed to be passed in.

    These will get forwarded through to anaconda-client, but if not defined in typer app could
    raise unwanted exceptions.

    """
    result = invoke_cli([*args, "some-test-subcommand"])
    assert result.exit_code == 0


@pytest.fixture
def plugin() -> ENTRY_POINT_TUPLE:
    plugin = typer.Typer(name="plugin", add_completion=False, no_args_is_help=True)

    @plugin.command("action")
    def action() -> None:
        print("done")

    return ("plugin", "plugin:app", plugin)


def test_load_plugin(
    invoke_cli: CLIInvoker, plugin: ENTRY_POINT_TUPLE, mocker: MockerFixture
) -> None:
    plugins = [plugin]

    mocker.patch(
        "anaconda_cli_base.plugins._load_entry_points_for_group", return_value=plugins
    )
    load_registered_subcommands(cast(typer.Typer, anaconda_cli_base.cli.app))

    group = next(
        (
            group
            for group in anaconda_cli_base.cli.app.registered_groups
            if group.name == "plugin"
        ),
        None,
    )
    assert group is not None
    assert group.typer_instance == plugin[-1]

    result = invoke_cli(["plugin", "action"])
    assert result.exit_code == 0
    assert result.stdout == "done\n"


@pytest.fixture
def cloud_plugin() -> ENTRY_POINT_TUPLE:
    plugin = typer.Typer(name="cloud", add_completion=False, no_args_is_help=True)

    @plugin.command("action")
    def action() -> None:
        print("cloud: done")

    @plugin.command("login")
    def login(force: bool = typer.Option(False, "--force")) -> None:
        print("cloud: You're in")

    @plugin.command("logout")
    def logout() -> None:
        print("cloud: You're out")

    @plugin.command("whoami")
    def whoami() -> None:
        print("cloud: Who are you?")

    return ("cloud", "auth-plugin:app", plugin)


def test_load_cloud_plugin(
    invoke_cli: CLIInvoker, cloud_plugin: ENTRY_POINT_TUPLE, mocker: MockerFixture
) -> None:
    assert "login" not in [
        cmd.name for cmd in anaconda_cli_base.cli.app.registered_commands
    ]

    plugins = [cloud_plugin]
    mocker.patch(
        "anaconda_cli_base.plugins._load_entry_points_for_group", return_value=plugins
    )
    load_registered_subcommands(cast(typer.Typer, anaconda_cli_base.cli.app))

    group = next(
        (
            group
            for group in anaconda_cli_base.cli.app.registered_groups
            if group.name == "cloud"
        ),
        None,
    )
    assert group is not None
    assert group.typer_instance == cloud_plugin[-1]

    for action in "login", "logout", "whoami":
        cmd = next(
            (
                cmd
                for cmd in anaconda_cli_base.cli.app.registered_commands
                if cmd.name == action
            ),
            None,
        )
        assert cmd is not None
        assert cmd.callback.__annotations__["at"].__metadata__[
            0
        ].click_type.choices == ["cloud"]

    result = invoke_cli(["cloud", "action"])
    assert result.exit_code == 0
    assert result.stdout == "cloud: done\n"

    result = invoke_cli(["login"], input="cloud")
    assert result.exit_code == 0
    assert result.stdout.strip().splitlines()[-1] == "cloud: You're in"

    result = invoke_cli(["login", "--at", "cloud"])
    assert result.exit_code == 0
    assert result.stdout == "cloud: You're in\n"

    result = invoke_cli(["login", "--at", "cloud", "--help"])
    assert result.exit_code == 0
    assert "--force" in result.stdout


@pytest.fixture
def org_plugin() -> ENTRY_POINT_TUPLE:
    plugin = typer.Typer(name="org", add_completion=False, no_args_is_help=True)

    @plugin.command("action")
    def action() -> None:
        print("org: done")

    @plugin.command("login")
    def login(force: bool = typer.Option(False, "--force")) -> None:
        print("org: You're in")

    @plugin.command("logout")
    def logout() -> None:
        print("org: You're out")

    @plugin.command("whoami")
    def whoami() -> None:
        print("org: Who are you?")

    return ("org", "org-plugin:app", plugin)


@pytest.fixture
def legacy_main(mocker: MockerFixture) -> Generator[Callable, None, None]:
    def main(
        args: Optional[Sequence[str]] = None,
        *,
        exit_: bool = True,
        allow_plugin_main: bool = True,
    ) -> None:
        pass

    cli = MagicMock()
    cli.main = main
    modules = {
        "binstar_client": MagicMock(),
        "binstar_client.scripts": MagicMock(),
        "binstar_client.scripts.cli": cli,
    }
    mocker.patch.dict("sys.modules", modules)

    yield main


def test_org_legacy(
    org_plugin: ENTRY_POINT_TUPLE,
    legacy_main: Callable,
    mocker: MockerFixture,
    monkeypatch: MonkeyPatch,
) -> None:
    """Mock the scenario where only anaconda-client was installed"""

    # these env vars should not be set in a normal env for this test
    monkeypatch.delenv("ANACONDA_CLI_FORCE_NEW", raising=False)
    monkeypatch.delenv("ANACONDA_CLIENT_FORCE_STANDALONE", raising=False)

    plugins = [org_plugin]
    mocker.patch(
        "anaconda_cli_base.plugins._load_entry_points_for_group", return_value=plugins
    )
    load_registered_subcommands(cast(typer.Typer, anaconda_cli_base.cli.app))

    assert [g.name for g in anaconda_cli_base.cli.app.registered_groups] == ["org"]

    final_app = _select_main_entrypoint_app(anaconda_cli_base.cli.app)

    assert isinstance(final_app, partial)
    assert final_app.func is legacy_main
    assert final_app.keywords["allow_plugin_main"] is False


def test_fail_org_legacy(
    org_plugin: ENTRY_POINT_TUPLE,
    legacy_main: Callable,
    mocker: MockerFixture,
    monkeypatch: MonkeyPatch,
) -> None:
    """Mock the scenario where only anaconda-client was installed"""

    # these env vars should not be set in a normal env for this test
    monkeypatch.setenv("ANACONDA_CLI_FORCE_NEW", "True")
    monkeypatch.setenv("ANACONDA_CLIENT_FORCE_STANDALONE", "True")

    plugins = [org_plugin]
    mocker.patch(
        "anaconda_cli_base.plugins._load_entry_points_for_group", return_value=plugins
    )
    load_registered_subcommands(cast(typer.Typer, anaconda_cli_base.cli.app))

    with pytest.raises(ValueError):
        _ = _select_main_entrypoint_app(anaconda_cli_base.cli.app)


def test_force_org_legacy(
    org_plugin: ENTRY_POINT_TUPLE,
    legacy_main: Callable,
    cloud_plugin: ENTRY_POINT_TUPLE,
    mocker: MockerFixture,
    monkeypatch: MonkeyPatch,
) -> None:
    """Multiple plugins installed but anaconda-client is the desired cli"""

    # these env vars should not be set in a normal env for this test
    monkeypatch.delenv("ANACONDA_CLI_FORCE_NEW", raising=False)
    monkeypatch.setenv("ANACONDA_CLIENT_FORCE_STANDALONE", "True")

    plugins = [org_plugin, cloud_plugin]
    mocker.patch(
        "anaconda_cli_base.plugins._load_entry_points_for_group", return_value=plugins
    )
    load_registered_subcommands(cast(typer.Typer, anaconda_cli_base.cli.app))
    groups = [g.name for g in anaconda_cli_base.cli.app.registered_groups]
    assert "org" in groups
    assert "cloud" in groups

    final_app = _select_main_entrypoint_app(anaconda_cli_base.cli.app)

    assert isinstance(final_app, partial)
    assert final_app.func is legacy_main
    assert final_app.keywords["allow_plugin_main"] is False


def test_org_subcommand(
    invoke_cli: CLIInvoker,
    org_plugin: ENTRY_POINT_TUPLE,
    cloud_plugin: ENTRY_POINT_TUPLE,
    mocker: MockerFixture,
    monkeypatch: MonkeyPatch,
) -> None:
    """Multiple plugins installed, default behavior"""

    # these env vars should not be set in a normal env for this test
    monkeypatch.delenv("ANACONDA_CLI_FORCE_NEW", raising=False)
    monkeypatch.delenv("ANACONDA_CLIENT_FORCE_STANDALONE", raising=False)

    plugins = [org_plugin, cloud_plugin]
    mocker.patch(
        "anaconda_cli_base.plugins._load_entry_points_for_group", return_value=plugins
    )
    load_registered_subcommands(cast(typer.Typer, anaconda_cli_base.cli.app))
    groups = [g.name for g in anaconda_cli_base.cli.app.registered_groups]
    assert "org" in groups
    assert "cloud" in groups

    final_app = _select_main_entrypoint_app(anaconda_cli_base.cli.app)

    assert final_app is anaconda_cli_base.cli.app

    for action in "login", "logout", "whoami":
        cmd = next(
            (
                cmd
                for cmd in anaconda_cli_base.cli.app.registered_commands
                if cmd.name == action
            ),
            None,
        )
        assert cmd is not None
        choices = cmd.callback.__annotations__["at"].__metadata__[0].click_type.choices
        assert sorted(choices) == ["cloud", "org"]

    result = invoke_cli(["org", "action"])
    assert result.exit_code == 0
    assert "org: done\n" == result.stdout
