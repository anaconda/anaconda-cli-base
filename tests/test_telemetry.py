from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from typing import Generator, Iterator
from unittest.mock import call

import pytest
from pytest import MonkeyPatch
from pytest_mock import MockerFixture

from .conftest import CLIInvoker


@pytest.fixture(autouse=True)
def isolate_telemetry(monkeypatch: MonkeyPatch) -> Generator[None, None, None]:
    import anaconda_cli_base.telemetry as mod

    monkeypatch.setattr(mod, "_initialized", False)
    yield


@pytest.fixture
def config_toml(tmp_path: Path, monkeypatch: MonkeyPatch) -> Iterator[Path]:
    config_file = tmp_path / "config.toml"
    monkeypatch.setenv("ANACONDA_CONFIG_TOML", str(config_file))
    yield config_file


@pytest.fixture
def telemetry_enabled(monkeypatch: MonkeyPatch, config_toml: Path) -> None:
    config_toml.write_text(
        dedent("""\
            [telemetry]
            endpoint = "http://localhost:19999"
            public_endpoint = "http://localhost:19999"
            skip_internet_check = true
        """)
    )


@pytest.fixture
def mock_otel(mocker: MockerFixture) -> dict:
    """Stub the OTel initialization and recording functions.

    Replaces _ensure_initialized with a simple flag-set so tests exercise
    _before_command/_after_command logic without requiring anaconda-opentelemetry.
    """
    import anaconda_cli_base.telemetry as mod

    mocker.patch(
        "anaconda_cli_base.telemetry._ensure_initialized",
        side_effect=lambda: setattr(mod, "_initialized", True),
    )
    inc = mocker.patch("anaconda_opentelemetry.increment_counter", return_value=True)
    hist = mocker.patch("anaconda_opentelemetry.record_histogram", return_value=True)
    shutdown = mocker.patch("anaconda_cli_base.telemetry._shutdown_telemetry")

    return {
        "increment_counter": inc,
        "record_histogram": hist,
        "shutdown": shutdown,
    }


def test_successful_command_records_metrics(mock_otel: dict) -> None:
    from anaconda_cli_base.telemetry import _before_command, _after_command

    info = _before_command(["ai", "chat"], "anaconda")
    assert info is not None
    assert info.command == "ai chat"
    assert info.plugin == "ai"

    _after_command(info, success=True)

    mock_otel["record_histogram"].assert_called_once()
    hist_args = mock_otel["record_histogram"].call_args
    assert hist_args[0][0] == "cli_command_duration_ms"
    assert hist_args[0][2]["command"] == "ai chat"
    assert hist_args[0][2]["source"] == "anaconda-cli-base"

    mock_otel["increment_counter"].assert_called_once_with(
        "cli_command_invoked",
        attributes={
            "command": "ai chat",
            "plugin": "ai",
            "source": "anaconda-cli-base",
        },
    )


def test_failed_command_records_error_metric(mock_otel: dict) -> None:
    from anaconda_cli_base.telemetry import _before_command, _after_command

    info = _before_command(["ai", "chat"], "anaconda")
    _after_command(info, success=False, error=RuntimeError("connection timeout"))

    calls = mock_otel["increment_counter"].call_args_list
    assert len(calls) == 2

    assert calls[0] == call(
        "cli_command_invoked",
        attributes={
            "command": "ai chat",
            "plugin": "ai",
            "source": "anaconda-cli-base",
        },
    )
    assert calls[1] == call(
        "cli_command_errors",
        attributes={
            "command": "ai chat",
            "plugin": "ai",
            "source": "anaconda-cli-base",
            "error.type": "RuntimeError",
        },
    )


def test_telemetry_disabled_when_endpoint_blanked(
    monkeypatch: MonkeyPatch, config_toml: Path
) -> None:
    config_toml.write_text('[telemetry]\nendpoint = ""\n')

    from anaconda_cli_base.telemetry import _is_enabled

    assert _is_enabled() is False


def test_telemetry_disabled_by_otel_sdk_disabled(
    monkeypatch: MonkeyPatch, telemetry_enabled: None
) -> None:
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")

    from anaconda_cli_base.telemetry import _before_command

    info = _before_command(["ai", "chat"], "anaconda")
    assert info is None


def test_after_command_noop_when_info_is_none(mock_otel: dict) -> None:
    from anaconda_cli_base.telemetry import _after_command

    _after_command(None, success=True)

    mock_otel["increment_counter"].assert_not_called()
    mock_otel["record_histogram"].assert_not_called()


def test_shutdown_called_after_command(mock_otel: dict) -> None:
    from anaconda_cli_base.telemetry import _before_command, _after_command

    info = _before_command(["ai", "chat"], "anaconda")
    _after_command(info, success=True)

    mock_otel["shutdown"].assert_called_once()


def test_duration_is_positive(mock_otel: dict) -> None:
    from anaconda_cli_base.telemetry import _before_command, _after_command
    import time

    info = _before_command(["ai", "chat"], "anaconda")
    time.sleep(0.01)
    _after_command(info, success=True)

    hist_args = mock_otel["record_histogram"].call_args
    duration_ms = hist_args[0][1]
    assert duration_ms >= 10.0


def test_noop_span_methods_are_safe() -> None:
    from anaconda_cli_base.telemetry import _NoOpSpan

    span = _NoOpSpan()
    span.add_event("test", {"key": "value"})
    span.add_exception(RuntimeError("boom"))
    span.set_error_status("error")
    span.add_attributes({"key": "value"})


def test_cli_success_calls_telemetry(
    invoke_cli: CLIInvoker, mocker: MockerFixture
) -> None:
    before = mocker.patch("anaconda_cli_base.cli._before_command")
    after = mocker.patch("anaconda_cli_base.cli._after_command")

    result = invoke_cli(["some-test-subcommand"])
    assert result.exit_code == 0

    before.assert_called_once()
    after.assert_called_once()
    _, kwargs = after.call_args
    assert kwargs["success"] is True


def test_cli_failure_calls_telemetry_with_error(
    invoke_cli: CLIInvoker, mocker: MockerFixture
) -> None:
    from anaconda_cli_base.exceptions import register_error_handler

    class _TelTestError(Exception):
        pass

    @register_error_handler(_TelTestError)
    def _handle(e: type) -> int:
        return 99

    import anaconda_cli_base.cli

    @anaconda_cli_base.cli.app.command("tel-fail")
    def tel_fail() -> None:
        raise _TelTestError("boom")

    before = mocker.patch("anaconda_cli_base.cli._before_command")
    after = mocker.patch("anaconda_cli_base.cli._after_command")

    result = invoke_cli(["tel-fail"])
    assert result.exit_code == 99

    before.assert_called_once()
    after.assert_called_once()
    _, kwargs = after.call_args
    assert kwargs["success"] is False
    assert isinstance(kwargs["error"], _TelTestError)


def test_cli_retry_calls_telemetry_once(
    invoke_cli: CLIInvoker, mocker: MockerFixture
) -> None:
    from anaconda_cli_base.exceptions import register_error_handler

    class _TelRetryError(Exception):
        pass

    call_count = 0

    @register_error_handler(_TelRetryError)
    def _handle(e: type) -> int:
        return -1

    import anaconda_cli_base.cli

    @anaconda_cli_base.cli.app.command("tel-retry")
    def tel_retry() -> None:
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise _TelRetryError("retry me")

    before = mocker.patch("anaconda_cli_base.cli._before_command")
    after = mocker.patch("anaconda_cli_base.cli._after_command")

    result = invoke_cli(["tel-retry"])
    assert result.exit_code == 0
    assert call_count == 2

    before.assert_called_once()
    after.assert_called_once()
