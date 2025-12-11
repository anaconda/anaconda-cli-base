import os
import sys
from collections import deque

from copy import deepcopy
from functools import cached_property, reduce
from pathlib import Path
from shutil import copy
from tomlkit.toml_document import TOMLDocument
from typing import Any
from typing import ClassVar
from typing import Dict
from typing import Optional
from typing import Tuple
from typing import Type
from typing import Union

import tomlkit
from pydantic import ValidationError
from pydantic.main import IncEx
from pydantic_settings import BaseSettings
from pydantic_settings import PydanticBaseSettingsSource
from pydantic_settings import PyprojectTomlConfigSettingsSource
from pydantic_settings import SettingsConfigDict

from .exceptions import AnacondaConfigTomlSyntaxError, AnacondaConfigValidationError

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


def anaconda_secrets_dir() -> Optional[Path]:
    path = Path(
        os.path.expandvars(
            os.path.expanduser(os.getenv("ANACONDA_SECRETS_DIR", "/run/secrets"))
        )
    )
    return path if path.is_dir() else None


def anaconda_config_path() -> Path:
    return Path(
        os.path.expandvars(
            os.path.expanduser(
                os.getenv("ANACONDA_CONFIG_TOML", "~/.anaconda/config.toml")
            )
        )
    )


class AnacondaConfigTomlSettingsSource(PyprojectTomlConfigSettingsSource):
    _cache: ClassVar[Dict[Path, Dict[str, Any]]] = {}

    def _read_file(self, file_path: Path) -> Dict[str, Any]:
        try:
            result = self._cache.get(file_path)
            if result is None:
                result = super()._read_file(file_path)
                self._cache[file_path] = result
            return result
        except tomllib.TOMLDecodeError as e:
            arg = f"{anaconda_config_path()}: {e.args[0]}"
            raise AnacondaConfigTomlSyntaxError(arg)


class AnacondaBaseSettings(BaseSettings):
    def __init_subclass__(
        cls, plugin_name: Optional[Union[str, tuple]] = None, **kwargs: Any
    ) -> None:
        base_env_prefix: str = "ANACONDA_"
        pyproject_toml_table_header: Tuple[str, ...]

        if plugin_name is None:
            pyproject_toml_table_header = ()
            env_prefix = base_env_prefix
        elif isinstance(plugin_name, tuple):
            if not all(isinstance(entry, str) for entry in plugin_name):
                raise ValueError(
                    f"plugin_name={plugin_name} error: All values must be strings."
                )
            pyproject_toml_table_header = ("plugin", *plugin_name)
            env_prefix = base_env_prefix + "_".join(plugin_name).upper() + "_"
        elif isinstance(plugin_name, str):
            pyproject_toml_table_header = ("plugin", plugin_name)
            env_prefix = base_env_prefix + f"{plugin_name.upper()}_"
        else:
            raise ValueError(
                f"plugin_name={plugin_name} is not supported. It must be either a str or tuple."
            )

        cls.model_config = SettingsConfigDict(
            env_file=".env",
            pyproject_toml_table_header=pyproject_toml_table_header,
            env_prefix=env_prefix,
            env_nested_delimiter="__",
            extra="ignore",
            ignored_types=(cached_property,),
            secrets_dir=anaconda_secrets_dir(),
        )

        return super().__init_subclass__(**kwargs)

    def __init__(self, **kwargs: Any) -> None:
        try:
            super().__init__(**kwargs)
        except ValidationError as e:
            errors = []
            for error in e.errors():
                input_value = error["input"]
                msg = error["msg"]

                env_prefix = self.model_config.get("env_prefix", "")
                delimiter = self.model_config.get("env_nested_delimiter", "") or ""
                env_var = env_prefix + delimiter.join(
                    str(loc).upper() for loc in error["loc"]
                )

                kwarg = error["loc"][0]
                if kwarg in kwargs:
                    value = kwargs[str(kwarg)]
                    msg = f"- Error in init kwarg {e.title}({error['loc'][0]}={value})\n    {msg}"
                elif env_var in os.environ:
                    msg = f"- Error in environment variable {env_var}={input_value}\n    {msg}"
                else:
                    table_header = ".".join(
                        self.model_config.get("pyproject_toml_table_header", [])
                    )
                    key = ".".join(str(loc) for loc in error["loc"])
                    msg = f"- Error in {anaconda_config_path()} in [{table_header}] for {key} = {input_value}\n    {msg}"

                errors.append(msg)

            message = "\n" + "\n".join(errors)

            raise AnacondaConfigValidationError(message)

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
            file_secret_settings,
            dotenv_settings,
            AnacondaConfigTomlSettingsSource(settings_cls, anaconda_config_path()),
        )

    def write_config(
        self,
        dry_run: bool = False,
        include: Optional[IncEx] = None,
        exclude: Optional[IncEx] = None,
        exclude_unset: bool = True,
        exclude_defaults: bool = True,
        exclude_none: bool = True,
    ) -> None:
        values = self.model_dump(
            include=include,
            exclude=exclude,
            exclude_unset=exclude_unset,
            exclude_defaults=exclude_defaults,
            exclude_none=exclude_none,
            exclude_computed_fields=True,
        )

        # Validation of the dump ensures that attributes
        # set manually on the instance are correct before
        # writing to config.toml
        self.model_validate(values)

        # save a backup of the config.toml just to be safe
        config_toml = anaconda_config_path()
        if config_toml.exists():
            copy(config_toml, config_toml.with_suffix(".backup.toml"))

            with open(config_toml, "rt") as f:
                config = tomlkit.load(f)
        else:
            config_toml.parent.mkdir(parents=True, exist_ok=True)
            config = tomlkit.TOMLDocument()

        to_update = deepcopy(config)

        table_header = self.model_config.get("pyproject_toml_table_header", tuple())

        if table_header:

            def nestitem(a: tomlkit.TOMLDocument, b: Any) -> Any:
                if b not in a:
                    a.add(b, tomlkit.table())
                return a[b]

            parent = reduce(nestitem, table_header, to_update)
        else:
            parent = to_update

        def deepmerge(
            orig: tomlkit.TOMLDocument,
            new: tomlkit.TOMLDocument,
            allow_removal: bool = True,
        ) -> None:
            stack = deque[Tuple[TOMLDocument, TOMLDocument]]([(orig, new)])
            while stack:
                current_original, current_update = stack.popleft()

                if allow_removal and current_original:
                    removed_keys = (
                        current_original.keys() - current_update.keys() - {"plugin"}
                    )
                    for k in removed_keys:
                        del current_original[k]

                for k, v in current_update.items():
                    if isinstance(v, dict):
                        if k not in current_original:
                            current_original.add(k, tomlkit.table())
                        to_append = (current_original.get(k), v)
                        stack.append(to_append)  # type: ignore
                    else:
                        current_original[k] = v

        deepmerge(parent, values)

        if dry_run:
            import difflib
            from rich.syntax import Syntax
            from .console import console

            original = config.as_string()
            updated = to_update.as_string()

            diffs = difflib.unified_diff(
                original.splitlines(True),
                updated.splitlines(True),
                fromfile=str(anaconda_config_path()),
                lineterm="",
            )
            diff = "".join(diffs)
            if not diff:
                console.print(
                    f"[bold green]No change to {anaconda_config_path()}[/bold green]"
                )
                return

            syntax = Syntax(code=diff, lexer="diff", line_numbers=True, word_wrap=True)
            console.print(syntax)
            return

        with open(config_toml, "wt") as f:
            tomlkit.dump(to_update, f)
