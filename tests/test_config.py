from pathlib import Path
from textwrap import dedent
from typing import Optional, Tuple, cast, Dict

import pytest
import typer
from pydantic import BaseModel
from pytest import MonkeyPatch
from pytest_mock import MockerFixture

import anaconda_cli_base.cli
from anaconda_cli_base.config import AnacondaBaseSettings
from anaconda_cli_base.exceptions import AnacondaConfigTomlSyntaxError
from anaconda_cli_base.exceptions import AnacondaConfigValidationError
from anaconda_cli_base.plugins import load_registered_subcommands

from .conftest import CLIInvoker

ENTRY_POINT_TUPLE = Tuple[str, str, typer.Typer]


class Nested(BaseModel):
    field: str = "default"


class DerivedSettings(AnacondaBaseSettings, plugin_name="derived"):
    foo: str = "default"
    nested: Nested = Nested()
    optional: Optional[int] = None
    not_required: Optional[str] = None


def test_settings_plugin_name_str() -> None:
    env_prefix = DerivedSettings.model_config.get("env_prefix", "")
    assert env_prefix == "ANACONDA_DERIVED_"

    table_header = DerivedSettings.model_config.get(
        "pyproject_toml_table_header", tuple()
    )
    assert table_header == (
        "plugin",
        "derived",
    )


def test_settings_plugin_name_tuple() -> None:
    class TupleName(DerivedSettings, plugin_name=("nested", "settings")): ...

    env_prefix = TupleName.model_config.get("env_prefix", "")
    assert env_prefix == "ANACONDA_NESTED_SETTINGS_"

    table_header = TupleName.model_config.get("pyproject_toml_table_header", tuple())
    assert table_header == ("plugin", "nested", "settings")


def test_settings_plugin_name_error() -> None:
    with pytest.raises(ValueError):

        class FailList(DerivedSettings, plugin_name=["nested", "settings"]): ...

    with pytest.raises(ValueError):

        class FailType(DerivedSettings, plugin_name=["nested", 0]): ...


def test_settings_priority(
    mocker: MockerFixture, monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    config_file = tmp_path / "config.toml"
    monkeypatch.setenv("ANACONDA_CONFIG_TOML", str(config_file))

    dotenv = tmp_path / ".env"
    mocker.patch.dict(DerivedSettings.model_config, {"env_file": dotenv})

    config = DerivedSettings()
    assert config.foo == "default"
    assert config.nested.field == "default"

    config_file.write_text(
        dedent("""\
        [plugin.derived]
        foo = "toml"
        [plugin.derived.nested]
        field = "toml"
    """)
    )
    config = DerivedSettings()
    assert config.foo == "toml"
    assert config.nested.field == "toml"

    config_file.write_text(
        dedent("""\
        [plugin.derived]
        foo = "toml"
        nested = { field = "toml_inline" }
    """)
    )
    config = DerivedSettings()
    assert config.foo == "toml"
    assert config.nested.field == "toml_inline"

    config_file.write_text(
        dedent("""\
        [plugin.derived]
        foo = "toml"
        nested.field = "toml_dot"
    """)
    )
    config = DerivedSettings()
    assert config.foo == "toml"
    assert config.nested.field == "toml_dot"

    dotenv.write_text(
        dedent("""\
        ANACONDA_DERIVED_FOO=dotenv
        ANACONDA_DERIVED_NESTED__FIELD=dotenv
    """)
    )
    config = DerivedSettings()
    assert config.foo == "dotenv"
    assert config.nested.field == "dotenv"

    monkeypatch.setenv("ANACONDA_DERIVED_FOO", "env")
    monkeypatch.setenv("ANACONDA_DERIVED_NESTED__FIELD", "env")
    config = DerivedSettings()
    assert config.foo == "env"
    assert config.nested.field == "env"

    config = DerivedSettings(foo="init", nested=Nested(field="init"))
    assert config.foo == "init"
    assert config.nested.field == "init"


def test_subclass(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    monkeypatch.setenv("ANACONDA_CONFIG_TOML", str(config_file))

    class Subclass(DerivedSettings, plugin_name="subclass"): ...

    assert Subclass.model_config.get("env_prefix", "") == "ANACONDA_SUBCLASS_"

    config_file.write_text(
        dedent("""\
        [plugin.subclass]
        foo = "subclass-config"
    """)
    )

    config = Subclass()
    assert config.foo == "subclass-config"

    monkeypatch.setenv("ANACONDA_SUBCLASS_FOO", "subclass-env")
    config = Subclass()
    assert config.foo == "subclass-env"


def test_nested_plugins(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    monkeypatch.setenv("ANACONDA_CONFIG_TOML", str(config_file))

    class ExtrasConfig(AnacondaBaseSettings, plugin_name=("derived", "extras")):
        value: str = "default"

    config_file.write_text(
        dedent("""\
        [plugin.derived]
        foo = "toml"
        [plugin.derived.nested]
        field = "toml"
        [plugin.derived.extras]
        value = "toml"
    """)
    )

    config = ExtrasConfig()
    assert config.value == "toml"

    monkeypatch.setenv("ANACONDA_DERIVED_EXTRAS_VALUE", "env")
    config = ExtrasConfig()
    assert config.value == "env"


def test_settings_toml_error(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    monkeypatch.setenv("ANACONDA_CONFIG_TOML", str(config_file))

    config_file.write_text(
        dedent("""\
        [plugin.derived]
        foo = ["a"
        [plugin.derived.nested]
        field = "toml"
    """)
    )

    with pytest.raises(AnacondaConfigTomlSyntaxError) as excinfo:
        _ = DerivedSettings()

    assert "/config.toml: Unclosed array (at line 3, column 1)" in excinfo.value.args[0]


def test_settings_validation_error(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    monkeypatch.setenv("ANACONDA_CONFIG_TOML", str(config_file))
    monkeypatch.setenv("ANACONDA_DERIVED_OPTIONAL", "not-an-integer")

    config_file.write_text(
        dedent("""\
        [plugin.derived]
        foo = 1
        [plugin.derived.nested]
        field = [0, 1, 2]
    """)
    )

    with pytest.raises(AnacondaConfigValidationError) as excinfo:
        _ = DerivedSettings(not_required=3)  # type: ignore

    message = excinfo.value.args[0]
    assert "/config.toml in [plugin.derived] for foo = 1" in message
    assert "/config.toml in [plugin.derived] for nested.field = [0, 1, 2]" in message
    assert (
        "Error in environment variable ANACONDA_DERIVED_OPTIONAL=not-an-integer"
        in message
    )
    assert "Error in init kwarg DerivedSettings(not_required=3)" in message


@pytest.fixture
def config_error_plugin(tmp_path: Path, monkeypatch: MonkeyPatch) -> ENTRY_POINT_TUPLE:
    config_file = tmp_path / "config.toml"
    monkeypatch.setenv("ANACONDA_CONFIG_TOML", str(config_file))

    plugin = typer.Typer(name="error", add_completion=False, no_args_is_help=True)

    @plugin.command("syntax-error")
    def syntax_error() -> None:
        config_file.write_text(
            dedent("""\
            [plugin.derived]
            foo = ["a"
            [plugin.derived.nested]
            field = "toml"
        """)
        )
        _ = DerivedSettings()

    @plugin.command("validation-error")
    def validation_error() -> None:
        monkeypatch.setenv("ANACONDA_DERIVED_OPTIONAL", "not-an-integer")

        config_file.write_text(
            dedent("""\
            [plugin.derived]
            foo = 1
            [plugin.derived.nested]
            field = [0, 1, 2]
        """)
        )
        _ = DerivedSettings(not_required=3)  # type: ignore

    return ("config-error", "config-error-plugin:app", plugin)


def test_error_handled(
    invoke_cli: CLIInvoker,
    config_error_plugin: ENTRY_POINT_TUPLE,
    mocker: MockerFixture,
) -> None:
    plugins = [config_error_plugin]
    mocker.patch(
        "anaconda_cli_base.plugins._load_entry_points_for_group", return_value=plugins
    )
    load_registered_subcommands(cast(typer.Typer, anaconda_cli_base.cli.app))

    result = invoke_cli(["config-error", "syntax-error"])
    assert result.exit_code == 1
    assert "/config.toml: Unclosed array (at line 3, column 1)" in result.stdout

    result = invoke_cli(["config-error", "validation-error"])
    assert result.exit_code == 1
    assert "/config.toml in [plugin.derived] for foo = 1" in result.stdout
    assert (
        "/config.toml in [plugin.derived] for nested.field = [0, 1, 2]" in result.stdout
    )
    assert (
        "Error in environment variable ANACONDA_DERIVED_OPTIONAL=not-an-integer"
        in result.stdout
    )
    assert "Error in init kwarg DerivedSettings(not_required=3)" in result.stdout


def test_root_level_table(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    monkeypatch.setenv("ANACONDA_CONFIG_TOML", str(config_file))

    class RootLevelTable(AnacondaBaseSettings, plugin_name=None):
        foo: str = "bar"
        table: Optional[Dict[str, str]] = None

    config_file.write_text(
        dedent("""\
        foo = "baz"
        [table]
        key = "value"
    """)
    )

    config = RootLevelTable()
    assert config.table == {"key": "value"}
    assert config.foo == "baz"
