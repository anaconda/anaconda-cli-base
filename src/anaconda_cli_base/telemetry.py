"""Centralized telemetry for the Anaconda CLI framework.

Plugin authors use module-level functions with plugin_name:

    from anaconda_cli_base.telemetry import count, histogram, traced, log_event

    count("models_downloaded", plugin_name="ai")
    histogram("download_size_bytes", plugin_name="ai", value=result.size)
    log_event("download complete", event_name="model_downloaded", plugin_name="ai")

    with traced("models_download", plugin_name="ai") as span:
        ...

All functions are no-ops when telemetry is disabled or anaconda-opentelemetry
is not installed.
"""

import logging
import os
import sys
import threading
import time
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Dict, Optional, Union

from anaconda_cli_base.telemetry_config import (
    TelemetryConfig,
    AUTHENTICATED_ENDPOINT,
    PUBLIC_ENDPOINT,
)

logger = logging.getLogger(__name__)

AttributeValue = Union[str, bool, int, float, Sequence[Union[str, bool, int, float]]]

config = TelemetryConfig()

_lock = threading.Lock()
_backend_initialized = False
_suppress_http: ContextVar[bool] = ContextVar("_suppress_http", default=False)


# ---------------------------------------------------------------------------
# Environment detection helpers
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _get_plugin_versions() -> Dict[str, str]:
    from importlib.metadata import entry_points
    from anaconda_cli_base import __version__

    versions = {"anaconda-cli-base": __version__}
    for ep in entry_points(group="anaconda_cli.subcommand"):
        if ep.dist:
            versions[ep.dist.name] = ep.dist.metadata["Version"]
    return versions


def _detect_ci_vendor() -> str:
    ci_env_vars = {
        "GITHUB_ACTIONS": "github-actions",
        "GITLAB_CI": "gitlab-ci",
        "JENKINS_URL": "jenkins",
        "CIRCLECI": "circleci",
        "TRAVIS": "travis-ci",
        "BUILDKITE": "buildkite",
        "TF_BUILD": "azure-pipelines",
        "CODEBUILD_BUILD_ID": "aws-codebuild",
        "TEAMCITY_VERSION": "teamcity",
        "BITBUCKET_PIPELINE_UUID": "bitbucket-pipelines",
    }
    for env_var, vendor in ci_env_vars.items():
        if os.environ.get(env_var):
            return vendor
    if os.environ.get("CI"):
        return "unknown-ci"
    return ""


def _detect_ai_agent() -> str:
    indicators = {
        "CURSOR_TRACE_ID": "cursor",
        "CURSOR_SESSION_ID": "cursor",
        "CLINE_TASK_ID": "cline",
        "CONTINUE_GLOBAL_DIR": "continue",
        "WINDSURF_SESSION_ID": "windsurf",
        "OPENCODE": "opencode",
    }
    for env_var, agent in indicators.items():
        if os.environ.get(env_var):
            return agent
    term_program = os.environ.get("TERM_PROGRAM", "")
    if "cursor" in term_program.lower():
        return "cursor"
    if os.environ.get("CLAUDE_CODE"):
        return "claude-code"
    return ""


def _detect_tty() -> bool:
    return sys.stdout.isatty()


def _is_first_run() -> bool:
    from pathlib import Path

    marker = Path.home() / ".anaconda" / ".telemetry_initialized"
    if marker.exists():
        return False
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError:
        pass
    return True


def _get_api_key() -> Optional[str]:
    try:
        from anaconda_auth.token import TokenInfo

        token_info = TokenInfo.load("anaconda.com")
        return token_info.api_key
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Backend lifecycle
# ---------------------------------------------------------------------------


def _init_backend() -> None:
    global _backend_initialized
    if _backend_initialized:
        return
    with _lock:
        if _backend_initialized:
            return
        if not config.enabled:
            return
        try:
            os.environ.setdefault("GRPC_VERBOSITY", "NONE")

            import re
            import platform as platform_mod

            from anaconda_opentelemetry.config import Configuration
            from anaconda_opentelemetry.attributes import ResourceAttributes
            from anaconda_opentelemetry.signals import initialize_telemetry

            from anaconda_cli_base import __version__

            api_key = _get_api_key()
            if config.endpoint:
                endpoint = config.endpoint
            elif api_key:
                endpoint = AUTHENTICATED_ENDPOINT
            else:
                endpoint = PUBLIC_ENDPOINT

            otel_config = Configuration(
                default_endpoint=endpoint,
                default_auth_token=api_key,  # type: ignore[arg-type]
            )
            otel_config.set_skip_internet_check(True)
            if config.proxy_url:
                otel_config.set_proxy_url(config.proxy_url)

            otel_config.set_metrics_export_interval_ms(1000)
            otel_config.set_tracing_export_interval_ms(1000)

            service_version = re.sub(r"[^a-zA-Z0-9._-]", ".", __version__)[:30]

            attrs = ResourceAttributes(
                service_name="anaconda-cli-base",
                service_version=service_version,
                platform=f"{platform_mod.system().lower()}-{platform_mod.machine()}",
                environment="production",
                anon_usage=config.share_session_identity,
            )
            attrs.set_attributes(
                plugin_versions=_get_plugin_versions(),
                ci_vendor=_detect_ci_vendor(),
                auth_state="authenticated" if api_key else "anonymous",
                is_first_run=_is_first_run(),
                ai_agent=_detect_ai_agent(),
                is_tty=_detect_tty(),
            )
            initialize_telemetry(
                config=otel_config,
                attributes=attrs,
                signal_types=["logging", "metrics", "tracing"],
            )
            _backend_initialized = True
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("Telemetry initialization failed: %s", exc)


def shutdown() -> None:
    if not _backend_initialized:
        return
    try:
        import atexit

        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry import trace, metrics

        from anaconda_opentelemetry.logging import _AnacondaLogger

        flush_timeout = config.flush_timeout_ms

        trace_provider = trace.get_tracer_provider()
        if isinstance(trace_provider, TracerProvider):
            trace_provider.force_flush(timeout_millis=flush_timeout)
            if trace_provider._atexit_handler is not None:
                atexit.unregister(trace_provider._atexit_handler)
                trace_provider._atexit_handler = None

        meter_provider = metrics.get_meter_provider()
        if isinstance(meter_provider, MeterProvider):
            meter_provider.force_flush(timeout_millis=flush_timeout)
            if meter_provider._atexit_handler is not None:
                atexit.unregister(meter_provider._atexit_handler)
                meter_provider._atexit_handler = None

        if _AnacondaLogger._instance is not None:
            logger_provider = _AnacondaLogger._instance._provider
            if isinstance(logger_provider, LoggerProvider):
                logger_provider.force_flush(timeout_millis=flush_timeout)
                if logger_provider._at_exit_handler is not None:
                    atexit.unregister(logger_provider._at_exit_handler)
                    logger_provider._at_exit_handler = None
    except Exception:
        pass


def is_enabled() -> bool:
    return _backend_initialized


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@contextmanager
def traced(
    name: str, *, plugin_name: str, attributes: Optional[Dict[str, Any]] = None
) -> Generator[Any, None, None]:
    _init_backend()
    if not _backend_initialized:
        yield _NoOpSpan()
        return
    try:
        from anaconda_opentelemetry import get_trace

        with get_trace(name, attributes=_build_attrs(attributes, plugin_name)) as span:
            yield span
    except Exception:
        yield _NoOpSpan()


def count(
    name: str,
    *,
    plugin_name: str,
    value: int = 1,
    attributes: Optional[Dict[str, Any]] = None,
) -> None:
    _init_backend()
    if not _backend_initialized:
        return
    try:
        from anaconda_opentelemetry import increment_counter

        increment_counter(
            name, by=value, attributes=_build_attrs(attributes, plugin_name)
        )
    except Exception:
        pass


def histogram(
    name: str,
    *,
    plugin_name: str,
    value: float,
    attributes: Optional[Dict[str, Any]] = None,
) -> None:
    _init_backend()
    if not _backend_initialized:
        return
    try:
        from anaconda_opentelemetry import record_histogram

        record_histogram(name, value, attributes=_build_attrs(attributes, plugin_name))
    except Exception:
        pass


def log_event(
    body: str,
    *,
    event_name: str,
    plugin_name: str,
    attributes: Optional[Dict[str, Any]] = None,
) -> None:
    _init_backend()
    if not _backend_initialized:
        return
    try:
        from anaconda_opentelemetry.signals import send_event

        send_event(body, event_name, attributes=_build_attrs(attributes, plugin_name))
    except Exception:
        pass


def get_otel_handler(level: int = logging.WARNING) -> logging.Handler:
    _init_backend()
    if not _backend_initialized:
        return logging.NullHandler()
    try:
        from anaconda_opentelemetry.signals import get_telemetry_logger_handler

        handler = get_telemetry_logger_handler()
        if handler is None:
            return logging.NullHandler()
        handler.setLevel(level)
        return handler
    except Exception:
        return logging.NullHandler()


def _build_attrs(
    attributes: Optional[Dict[str, Any]], plugin_name: str
) -> Dict[str, Any]:
    attrs = dict(attributes or {})
    attrs["source"] = "anaconda-cli-base"
    attrs["plugin"] = plugin_name
    return attrs


# ---------------------------------------------------------------------------
# CLI framework internals
# ---------------------------------------------------------------------------


@dataclass
class _CommandInfo:
    command: str
    plugin: str
    flags: str
    start_time: float = field(default_factory=time.perf_counter)


def _before_command(
    args: Optional[Sequence[str]], prog_name: Optional[str]
) -> Optional[_CommandInfo]:
    _init_backend()
    if not _backend_initialized:
        return None
    command_name = " ".join(args[:2]) if args else prog_name or "unknown"
    plugin_name = args[0] if args else "root"
    flags = ",".join(a.split("=")[0] for a in (args or []) if a.startswith("-"))
    return _CommandInfo(command=command_name, plugin=plugin_name, flags=flags)


def _after_command(
    info: Optional[_CommandInfo],
    success: bool,
    error: Optional[Exception] = None,
    exit_code: int = 0,
) -> None:
    if info is None:
        return
    try:
        from anaconda_opentelemetry import increment_counter, record_histogram

        duration_ms = (time.perf_counter() - info.start_time) * 1000
        attrs: Dict[str, AttributeValue] = {
            "command": info.command,
            "plugin": info.plugin,
            "source": "anaconda-cli-base",
            "flags": info.flags,
            "exit_code": exit_code if not success else 0,
        }

        record_histogram("cli_command_duration_ms", duration_ms, attrs)
        increment_counter("cli_command_invoked", attributes=attrs)
        if not success:
            error_attrs: Dict[str, AttributeValue] = {
                **attrs,
                "error.type": type(error).__name__ if error else "unknown",
                "error.code": str(getattr(error, "code", getattr(error, "errno", ""))),
                "error.message": str(error)[:500] if error else "",
            }
            increment_counter("cli_command_errors", attributes=error_attrs)
    except Exception:
        pass

    shutdown()


# ---------------------------------------------------------------------------
# HTTP span suppression
# ---------------------------------------------------------------------------


@contextmanager
def suppress_http_spans() -> Generator[None, None, None]:
    token = _suppress_http.set(True)
    try:
        yield
    finally:
        _suppress_http.reset(token)


def is_http_suppressed() -> bool:
    return _suppress_http.get()


# ---------------------------------------------------------------------------
# NoOp span
# ---------------------------------------------------------------------------


class _NoOpSpan:
    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        pass

    def add_exception(self, exc: Exception) -> None:
        pass

    def set_error_status(self, msg: Optional[str] = None) -> None:
        pass

    def add_attributes(self, attributes: Dict[str, Any]) -> None:
        pass
