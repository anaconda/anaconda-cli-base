# anaconda-cli-base

A base CLI entrypoint supporting Anaconda CLI plugins

## Registering plugins

Subcommands can be registered as follows:

```toml
# In pyproject.toml

[project.entry-points."anaconda_cli.subcommand"]
auth = "anaconda_cloud_auth.cli:app"
```

In the example above:

* `"anaconda_cloud_cli.subcommand"` is the required string to use for registration. The quotes are important.
* `auth` is the name of the new subcommand, i.e. `anaconda auth`
* `anaconda_cloud_auth.cli:app` signifies the object named `app` in the `anaconda_cloud_auth.cli` module is the entry point for the subcommand.


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
