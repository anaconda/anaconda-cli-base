from __future__ import annotations

import sys
from collections import defaultdict
from typing import Callable

from anaconda_cli_base.console import console

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

ErrorHandlingCallback = Callable[[Exception], int]


class AnacondaConfigTomlSyntaxError(tomllib.TOMLDecodeError): ...


class AnacondaConfigValidationError(ValueError): ...


def catch_all(e: Exception) -> int:
    console.print(f"[bold][red]{e.__class__.__name__}:[/bold][/red] ", end="")
    console.print(e, markup=False)
    return 1


ERROR_HANDLERS: dict[type[Exception], ErrorHandlingCallback] = defaultdict(
    lambda: catch_all
)


def register_error_handler(exc: type[Exception]) -> Callable:
    def decorator(f: ErrorHandlingCallback) -> Callable:
        ERROR_HANDLERS[exc] = f
        return f

    return decorator
