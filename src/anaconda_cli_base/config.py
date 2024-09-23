import os

from functools import cached_property
from pathlib import Path
from typing import Any
from typing import Optional
from typing import Tuple
from typing import Type

from pydantic_settings import BaseSettings
from pydantic_settings import PydanticBaseSettingsSource
from pydantic_settings import PyprojectTomlConfigSettingsSource
from pydantic_settings import SettingsConfigDict


def anaconda_config_path() -> Path:
    return Path(
        os.path.expandvars(
            os.path.expanduser(
                os.getenv("ANACONDA_CONFIG_TOML", "~/.anaconda/config.toml")
            )
        )
    )


class AnacondaBaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        pyproject_toml_table_header=(),
        env_prefix="ANACONDA_",
        env_nested_delimiter="__",
        extra="ignore",
        ignored_types=(cached_property,),
    )

    def __init_subclass__(
        cls, plugin_name: Optional[str] = None, **kwargs: Any
    ) -> None:
        cls.model_config["pyproject_toml_table_header"] = (
            ()
            if plugin_name is None
            else (
                "plugin",
                plugin_name,
            )
        )
        base_env_prefix = cls.model_config.get("env_prefix", "")
        env_prefix = base_env_prefix + (
            "" if plugin_name is None else f"{plugin_name.upper()}_"
        )
        cls.model_config["env_prefix"] = env_prefix
        return super().__init_subclass__(**kwargs)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
            PyprojectTomlConfigSettingsSource(settings_cls, anaconda_config_path()),
        )
