# anaconda-cli-base

A base CLI entrypoint supporting Anaconda CLI plugins using [Typer](https://github.com/fastapi/typer).

## Registering plugins

To develop a subcommand in a third-party package, first create a `typer.Typer()` app with one or more commands.
See [this example](https://typer.tiangolo.com/#example-upgrade). The commands defined in your package will be prefixed
with the *subcommand* you define when you register the plugin.

In your `pyproject.toml` subcommands can be registered as follows:

```toml
# In pyproject.toml

[project.entry-points."anaconda_cli.subcommand"]
auth = "anaconda_cloud_auth.cli:app"
```

In the example above:

* `"anaconda_cloud_cli.subcommand"` is the required string to use for registration. The quotes are important.
* `auth` is the name of the new subcommand, i.e. `anaconda auth`
  * All `typer.Typer` commands you define in your package are accessible the registered subcommand
  * i.e. `anaconda auth <command>`.
* `anaconda_cloud_auth.cli:app` signifies the object named `app` in the `anaconda_cloud_auth.cli` module is the entry point for the subcommand.

### Error handling

By default any exception raised during CLI execution in your registered plugin will be caught and only a minimal
message will be displayed to the user.

You can define a custom callback for individual exceptions that may be thrown from your subcommand. You can
register handlers for standard library exceptions or custom defined exceptions. It may be best to use custom
exceptions to avoid unintended consequences for other plugins.

To register the callback decorate a function that takes an exception as input, and return an integer error code.
The error code will be sent back through the CLI and your subcommand will exit with that error code.

```python
from typing import Type
from anaconda_cli_base.exceptions import register_error_handler

@register_error_handler(MyCustomException)
def better_exception_handling(e: Type[Exception]) -> int:
    # do something or print useful information
    return 1

@register_error_handler(AnotherException)
def just_ignore_it(e: Type[Exception])
    # ignore the error and let the CLI exit successfully
    return 0


@register_error_handler(YetAnotherException)
def fix_the_error_and_try_again(e: Type[Exception]) -> int:
    # do something and retry the CLI command
    return -1
```

In the second example the handler returns `-1`. This means that the handler has attempted to correct the error
and the CLI subcommand should be re-tried. The handler could call another interactive command, like a login action,
before attempting the CLI subcommand again.

See the [anaconda-cloud-auth](https://github.com/anaconda/anaconda-cloud-tools/blob/main/libs/anaconda-cloud-auth/src/anaconda_cloud_auth/cli.py) plugin for an example custom handler.

### Config file

If your plugin wants to utilize the Anaconda config file, default location `~/.anaconda/config.toml`, to read configuration
parameters you can derive from `anaconda_cli_base.config.AnacondaBaseSettings` to add a section in the config file for
your plugin.
 Each subclass of `AnacondaBaseSettings`
defines the section header. The base class is configured so that parameters defined in subclasses can be read in the
following priority from lowest to highest.

1. default value in the subclass of `AnacondaBaseSettings`
1. Global config file at ~/.anaconda/config.toml
1. `ANACONDA_<PLUGIN-NAME>_<FIELD>` variables defined in the .env file in your working directory
1. `ANACONDA_<PLUGIN-NAME>_<FIELD>` env variables set in your shell or on command invocation
1. value passed as kwarg when using the config subclass directly

Notes:

* `AnacondaBaseSettings` is a subclass of `BaseSettings` from [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/#usage).
* Nested pydantic models are also supported.

Here's an example subclass

```python
from anaconda_cli_base.config import AnacondaBaseSettings

class MyPluginConfig(AnacondaBaseSettings, plugin_name="my_plugin"):
    foo: str = "bar"
```

To read the config value in your plugin according to the above
priority:

```python
config = MyPluginConfig()
assert config.foo == "bar"
```

Since there is no value of `foo` in the config file it assumes the default value from the subclass definition.

The value of `foo` can now be written to the config file under the section `my_plugin`

```toml
# ~/.anaconda/config.toml
[plugin.my_plugin]
foo = "baz"
```

Now that the config file has been written, the value of `foo` is read from the
config.toml file:

```python
config = MyPluginConfig()
assert config.foo == "baz"
```

See the [tests](https://github.com/anaconda/anaconda-cloud-tools/blob/feat/cli-base-config-file/libs/anaconda-cli-base/tests/test_config.py) for more examples.

## Setup for development

Ensure you have `conda` installed.
Then run:

```shell
make setup
```

## Run the unit tests

```shell
make test
```

## Run the unit tests across isolated environments with tox
```shell
make tox
```
