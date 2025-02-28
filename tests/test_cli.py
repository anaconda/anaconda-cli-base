from functools import partial
from typing import Tuple
from typing import Type
from typing import cast
from typing import Optional, Sequence, Callable, Generator

import pytest
import typer
from unittest.mock import MagicMock
from pytest import MonkeyPatch
from pytest_mock import MockerFixture

import anaconda_cli_base.cli
from anaconda_cli_base import __version__
from anaconda_cli_base import console
from anaconda_cli_base.cli import _select_main_entrypoint_app
from anaconda_cli_base.exceptions import register_error_handler
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
@pytest.mark.parametrize(
    "expected_text",
    [
        "Welcome to the Anaconda CLI!",
        "Print debug information to the console.",
        "Show this message and exit.",
        "Show version and exit.",
    ],
)
def test_cli_help(invoke_cli: CLIInvoker, args: Tuple[str], expected_text: str) -> None:
    result = invoke_cli(args)
    assert result.exit_code == 0
    assert expected_text in result.stdout


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
        console.print("cloud: done")

    @plugin.command("login")
    def login(force: bool = typer.Option(False, "--force")) -> None:
        console.print("cloud: You're in")

    @plugin.command("logout")
    def logout() -> None:
        console.print("cloud: You're out")

    @plugin.command("whoami")
    def whoami() -> None:
        console.print("cloud: Who are you?")

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

    result = invoke_cli(["cloud", "action"])
    assert result.exit_code == 0
    assert result.stdout == "cloud: done\n"

    result = invoke_cli(["login"], input="\n")
    assert result.exit_code == 0
    assert result.stdout.strip().splitlines()[-1].endswith("cloud: You're in")

    result = invoke_cli(["login", "--at", "cloud"])
    assert result.exit_code == 0
    assert result.stdout == "cloud: You're in\n"

    result = invoke_cli(["login", "--at", "anaconda.com"])
    assert result.exit_code == 0
    assert result.stdout == "cloud: You're in\n"

    result = invoke_cli(["login", "--at", "cloud", "--help"])
    assert result.exit_code == 0
    assert "--force" in result.stdout

    result = invoke_cli(["login", "--at", "anaconda.com", "--help"])
    assert result.exit_code == 0
    assert "--force" in result.stdout


@pytest.fixture
def org_plugin(monkeypatch: MonkeyPatch) -> ENTRY_POINT_TUPLE:
    plugin = typer.Typer(name="org", add_completion=False, no_args_is_help=True)

    @plugin.command("action")
    def action() -> None:
        console.print("org: done")

    @plugin.command("token")
    def token(ctx: typer.Context) -> None:
        print(f"token: {ctx.obj.params.get('token')}")

    @plugin.command("login")
    def login(force: bool = typer.Option(False, "--force")) -> None:
        console.print("org: You're in")

    @plugin.command("logout")
    def logout() -> None:
        console.print("org: You're out")

    @plugin.command("whoami")
    def whoami() -> None:
        console.print("org: Who are you?")

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

    result = invoke_cli(["org", "action"])
    assert result.exit_code == 0
    assert "org: done\n" == result.stdout

    result = invoke_cli(["login", "--at", "org"])
    assert result.exit_code == 0
    assert result.stdout == "org: You're in\n"

    result = invoke_cli(["login", "--at", "anaconda.org"])
    assert result.exit_code == 0
    assert result.stdout == "org: You're in\n"


def test_login_select_multiple_plugins(
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

    result = invoke_cli(["login"])
    assert result.stdout.strip().startswith(
        "choose destination: \n * anaconda.com      \n   anaconda.org"
    )

    result = invoke_cli(["login"], input="\n")
    assert result.exit_code == 0
    assert result.stdout.strip().splitlines()[-1].endswith("cloud: You're in")

    result = invoke_cli(["login"], input="j\n")
    assert result.exit_code == 0
    assert result.stdout.strip().splitlines()[-1].endswith("org: You're in")

    result = invoke_cli(["login"], input="jk\n")
    assert result.exit_code == 0
    assert result.stdout.strip().splitlines()[-1].endswith("cloud: You're in")

    # These cannot be tested because key.UP and key.DOWN send multiple characters
    # through to click.getchar, but that does not happen interactively.

    # result = invoke_cli(["login"], input=key.ENTER)
    # assert result.exit_code == 0
    # assert result.stdout.strip().splitlines()[-1].endswith("org: You're in")

    # result = invoke_cli(["login"], input=key.DOWN+key.ENTER)
    # assert result.exit_code == 0
    # assert result.stdout.strip().splitlines()[-1].endswith("org: You're in")

    # result = invoke_cli(["login"], input=key.UP+key.ENTER)
    # assert result.exit_code == 0
    # assert result.stdout.strip().splitlines()[-1].endswith("cloud: You're in")


def test_login_select_hidden_org(
    invoke_cli: CLIInvoker,
    org_plugin: ENTRY_POINT_TUPLE,
    mocker: MockerFixture,
    monkeypatch: MonkeyPatch,
) -> None:
    """Single plugin installed, selector hidden"""

    # these env vars should not be set in a normal env for this test
    monkeypatch.delenv("ANACONDA_CLI_FORCE_NEW", raising=False)
    monkeypatch.delenv("ANACONDA_CLIENT_FORCE_STANDALONE", raising=False)

    plugins = [org_plugin]
    mocker.patch(
        "anaconda_cli_base.plugins._load_entry_points_for_group", return_value=plugins
    )
    load_registered_subcommands(cast(typer.Typer, anaconda_cli_base.cli.app))

    result = invoke_cli(["login"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "org: You're in"


def test_login_select_hidden_cloud(
    invoke_cli: CLIInvoker,
    cloud_plugin: ENTRY_POINT_TUPLE,
    mocker: MockerFixture,
) -> None:
    """Single plugins installed, selector hidden"""

    plugins = [cloud_plugin]
    mocker.patch(
        "anaconda_cli_base.plugins._load_entry_points_for_group", return_value=plugins
    )
    load_registered_subcommands(cast(typer.Typer, anaconda_cli_base.cli.app))

    result = invoke_cli(["login"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "cloud: You're in"


def test_capture_top_level_params(
    invoke_cli: CLIInvoker,
    org_plugin: ENTRY_POINT_TUPLE,
    mocker: MockerFixture,
    monkeypatch: MonkeyPatch,
) -> None:
    # Test that subcommand captures the top-level CLI params via the typer.Context.obj.params attribute.

    # these env vars should not be set in a normal env for this test
    monkeypatch.delenv("ANACONDA_CLI_FORCE_NEW", raising=False)
    monkeypatch.delenv("ANACONDA_CLIENT_FORCE_STANDALONE", raising=False)

    plugins = [org_plugin]
    mocker.patch(
        "anaconda_cli_base.plugins._load_entry_points_for_group", return_value=plugins
    )
    load_registered_subcommands(cast(typer.Typer, anaconda_cli_base.cli.app))
    groups = [g.name for g in anaconda_cli_base.cli.app.registered_groups]
    assert "org" in groups

    final_app = _select_main_entrypoint_app(anaconda_cli_base.cli.app)

    assert final_app is anaconda_cli_base.cli.app

    result = invoke_cli(["--token", "TOKEN", "org", "token"])
    assert result.exit_code == 0
    assert "token: TOKEN\n" == result.stdout


@pytest.fixture
def error_plugin() -> ENTRY_POINT_TUPLE:
    plugin = typer.Typer(name="error", add_completion=False, no_args_is_help=True)

    class MyException(Exception):
        pass

    @register_error_handler(MyException)
    def handle_exception(e: Type[Exception]) -> int:
        print(f"Custom error handler: {e.__class__.__name__}")
        return 42

    class AnotherException(Exception):
        pass

    @register_error_handler(AnotherException)
    def handle_and_continue(e: Type[Exception]) -> int:
        print(f"I've corrected the problem: {e.__class__.__name__}")
        return -1

    @plugin.command("auto-catch")
    def auth_catch() -> None:
        _ = 1 / 0

    @plugin.command("custom-catch")
    def custom_catch() -> None:
        raise MyException("something bad happened")

    class Counter:
        def __init__(self) -> None:
            self.count = 0

        def __call__(self) -> None:
            print("calling counter")
            self.count += 1
            if self.count < 2:
                raise AnotherException("Call me again")

    counter1 = Counter()
    counter2 = Counter()

    @plugin.command("continue-task")
    def continue_task() -> None:
        counter1()

    @plugin.command("continue-task-but-fail")
    def continue_task_but_fail() -> None:
        counter2()
        raise RuntimeError("something went wrong")

    return ("error", "error-plugin:app", plugin)


def test_error_handled(
    invoke_cli: CLIInvoker,
    error_plugin: ENTRY_POINT_TUPLE,
    mocker: MockerFixture,
    monkeypatch: MonkeyPatch,
) -> None:
    # Test that subcommand captures the top-level CLI params via the typer.Context.obj.params attribute.

    # these env vars should not be set in a normal env for this test
    monkeypatch.delenv("ANACONDA_CLI_FORCE_NEW", raising=False)
    monkeypatch.delenv("ANACONDA_CLIENT_FORCE_STANDALONE", raising=False)

    plugins = [error_plugin]
    mocker.patch(
        "anaconda_cli_base.plugins._load_entry_points_for_group", return_value=plugins
    )
    load_registered_subcommands(cast(typer.Typer, anaconda_cli_base.cli.app))

    result = invoke_cli(["error", "auto-catch"])
    assert result.exit_code == 1
    assert result.stdout.splitlines()[0] == "ZeroDivisionError: division by zero"

    result = invoke_cli(["--verbose", "error", "auto-catch"])
    assert result.exit_code == 1
    assert isinstance(result.exception, ZeroDivisionError)

    result = invoke_cli(["error", "custom-catch"])
    assert result.exit_code == 42
    assert result.stdout.splitlines()[0] == "Custom error handler: MyException"

    result = invoke_cli(["error", "continue-task"])
    assert result.exit_code == 0
    output = result.stdout.splitlines()
    assert output[:3] == [
        "calling counter",
        "I've corrected the problem: AnotherException",
        "calling counter",
    ]

    result = invoke_cli(["error", "continue-task-but-fail"])
    assert result.exit_code == 1
    output = result.stdout.splitlines()
    assert output[:4] == [
        "calling counter",
        "I've corrected the problem: AnotherException",
        "calling counter",
        "RuntimeError: something went wrong",
    ]
