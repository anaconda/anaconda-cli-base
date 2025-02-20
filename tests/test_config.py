from pathlib import Path
from textwrap import dedent
from typing import Optional

import pytest
from pydantic import BaseModel
from pydantic import ValidationError
from pytest import MonkeyPatch
from pytest_mock import MockerFixture

from anaconda_cli_base.config import AnacondaBaseSettings


class Nested(BaseModel):
    field: str = "default"


class DerivedSettings(AnacondaBaseSettings, plugin_name="derived"):
    foo: str = "default"
    nested: Nested = Nested()
    optional: Optional[int] = None


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

    table_header = TupleName.model_config.get(
        "pyproject_toml_table_header", tuple()
    )
    assert table_header == (
        "plugin",
        "nested",
        "settings"
    )


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


def test_settings_validation_failed(
    mocker: MockerFixture, monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    config_file = tmp_path / "config.toml"
    monkeypatch.setenv("ANACONDA_CONFIG_TOML", str(config_file))

    dotenv = tmp_path / ".env"
    mocker.patch.dict(DerivedSettings.model_config, {"env_file": dotenv})

    config_file.write_text(
        dedent("""\
        [plugin.derived]
        foo = 0
        [plugin.derived.nested]
        field = "toml"
    """)
    )
    with pytest.raises(ValidationError):
        _ = DerivedSettings()


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
