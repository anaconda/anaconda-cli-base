"""Centralized telemetry for the Anaconda CLI framework.

All functions are safe to call regardless of whether telemetry is configured.
When no endpoint is set, every function is a no-op. Actual imports of the
OTel SDK are deferred until telemetry is enabled to keep CLI startup fast.
"""

import logging
import os
import time
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Dict, Optional, Union

logger = logging.getLogger(__name__)

AttributeValue = Union[str, bool, int, float, Sequence[Union[str, bool, int, float]]]

_initialized = False
_flush_timeout_ms = 500

_suppress_http: ContextVar[bool] = ContextVar("_suppress_http", default=False)


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


@dataclass
class _CommandInfo:
    command: str
    plugin: str
    flags: str
    start_time: float = field(default_factory=time.perf_counter)


def _is_enabled() -> bool:
    # Early-exit: OTel SDK checks this deeper in the stack, but catching it here
    # avoids reading config.toml and importing anaconda-opentelemetry for nothing.
    if os.environ.get("OTEL_SDK_DISABLED", "").lower() in ("true", "1", "yes"):
        return False
    try:
        from anaconda_cli_base.telemetry_config import TelemetryConfig

        cfg = TelemetryConfig()
        return cfg.enabled
    except Exception:
        return False


def _ensure_initialized() -> None:
    global _initialized, _flush_timeout_ms
    if _initialized or not _is_enabled():
        return
    try:
        os.environ.setdefault("GRPC_VERBOSITY", "NONE")

        from anaconda_opentelemetry.config import Configuration
        from anaconda_opentelemetry.attributes import ResourceAttributes
        from anaconda_opentelemetry.signals import initialize_telemetry

        import re

        from anaconda_cli_base import __version__
        from anaconda_cli_base.telemetry_config import (
            TelemetryConfig,
            AUTHENTICATED_ENDPOINT,
            PUBLIC_ENDPOINT,
        )

        cfg = TelemetryConfig()

        api_key = _get_api_key()
        if cfg.endpoint:
            endpoint = cfg.endpoint
        elif api_key:
            endpoint = AUTHENTICATED_ENDPOINT
        else:
            endpoint = PUBLIC_ENDPOINT

        config = Configuration(
            default_endpoint=endpoint,
            default_auth_token=api_key,  # type: ignore[arg-type]  # upstream accepts None despite annotation
        )
        config.set_skip_internet_check(True)
        if cfg.proxy_url:
            config.set_proxy_url(cfg.proxy_url)

        config.set_metrics_export_interval_ms(1000)
        config.set_tracing_export_interval_ms(1000)

        import platform as platform_mod

        service_version = re.sub(r"[^a-zA-Z0-9._-]", ".", __version__)[:30]

        attrs = ResourceAttributes(
            service_name="anaconda-cli-base",
            service_version=service_version,
            platform=f"{platform_mod.system().lower()}-{platform_mod.machine()}",
            environment="production",
            anon_usage=cfg.share_session_identity,
        )
        attrs.set_attributes(
            plugin_versions=_get_plugin_versions(),
            ci_vendor=_detect_ci_vendor(),
            auth_state="authenticated" if api_key else "anonymous",
            is_first_run=_is_first_run(),
        )
        initialize_telemetry(
            config=config,
            attributes=attrs,
            signal_types=["logging", "metrics", "tracing"],
        )
        _flush_timeout_ms = cfg.flush_timeout_ms
        _initialized = True
    except ImportError:
        # anaconda-opentelemetry not installed — expected in environments
        # without the [telemetry] extra.
        pass
    except Exception as exc:
        # SDK is installed but initialization failed (bad endpoint, auth,
        # version mismatch, etc.). Never surface to end users — telemetry
        # must fail silently. Diagnosable only at DEBUG level.
        logger.debug("Telemetry initialization failed: %s", exc)


def _get_api_key() -> Optional[str]:
    try:
        from anaconda_auth.exceptions import TokenNotFoundError
        from anaconda_auth.token import TokenInfo

        token_info = TokenInfo.load("anaconda.com")
        return token_info.api_key
    except TokenNotFoundError:
        return None
    except Exception:
        return None


def _before_command(
    args: Optional[Sequence[str]], prog_name: Optional[str]
) -> Optional[_CommandInfo]:
    """Start tracking a command. Returns None when telemetry is inactive."""
    _ensure_initialized()
    if not _initialized:
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
            }
            increment_counter("cli_command_errors", attributes=error_attrs)
    except Exception:
        pass

    _shutdown_telemetry()


def _shutdown_telemetry() -> None:
    """Flush all telemetry and disable atexit handlers to prevent exit hangs."""
    try:
        import atexit

        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry import trace, metrics

        from anaconda_opentelemetry.logging import _AnacondaLogger

        trace_provider = trace.get_tracer_provider()
        if isinstance(trace_provider, TracerProvider):
            trace_provider.force_flush(timeout_millis=_flush_timeout_ms)
            if trace_provider._atexit_handler is not None:
                atexit.unregister(trace_provider._atexit_handler)
                trace_provider._atexit_handler = None

        meter_provider = metrics.get_meter_provider()
        if isinstance(meter_provider, MeterProvider):
            meter_provider.force_flush(timeout_millis=_flush_timeout_ms)
            if meter_provider._atexit_handler is not None:
                atexit.unregister(meter_provider._atexit_handler)
                meter_provider._atexit_handler = None

        if _AnacondaLogger._instance is not None:
            logger_provider = _AnacondaLogger._instance._provider
            if isinstance(logger_provider, LoggerProvider):
                logger_provider.force_flush(timeout_millis=_flush_timeout_ms)
                if logger_provider._at_exit_handler is not None:
                    atexit.unregister(logger_provider._at_exit_handler)
                    logger_provider._at_exit_handler = None
    except Exception:
        pass


def is_telemetry_enabled() -> bool:
    return _initialized


def get_otel_handler(level: int = logging.WARNING) -> logging.Handler:
    """Get a logging handler that exports log records to the OTel backend.

    Attach to any named logger to forward records at *level* or above to the
    telemetry collector. The handler is additive — existing handlers (stderr,
    file) continue to work normally.

    Returns a NullHandler when telemetry is inactive or unavailable, so it is
    always safe to call unconditionally.

    Usage:
        import logging
        from anaconda_cli_base.telemetry import get_otel_handler

        log = logging.getLogger("anaconda_ai")
        log.addHandler(get_otel_handler())

        # Only WARNING+ goes to OTel; all levels still go to other handlers
        log.error("download failed", extra={"model": model, "error.type": "TimeoutError"})
    """
    if not _initialized:
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


@contextmanager
def traced(
    name: str,
    plugin_name: str,
    attributes: Optional[Dict[str, Any]] = None,
) -> Generator[Any, None, None]:
    """Create a child span for tracing a block of work.

    Use to measure duration and capture events within a logical operation.
    The span appears in trace views as a child of the CLI command's root span,
    giving visibility into where time is spent.

    Usage:
        with traced("models_download", plugin_name="ai", attributes={"model": "llama3"}) as span:
            result = do_download(model)
            span.add_event("download_complete", {"size_bytes": result.size})
    """
    if not _initialized:
        _ensure_initialized()
    if not _initialized:
        yield _NoOpSpan()
        return
    try:
        from anaconda_opentelemetry import get_trace

        span_attrs = _build_attrs(attributes, plugin_name)
        with get_trace(name, attributes=span_attrs) as span:
            yield span
    except Exception:
        # Defensive: upstream get_trace() does `return None` inside a @contextmanager
        # when tracing is uninitialized, which breaks the `with` protocol.
        yield _NoOpSpan()


def count(
    name: str,
    plugin_name: str,
    value: int = 1,
    attributes: Optional[Dict[str, Any]] = None,
) -> None:
    """Increment a counter metric. Use for discrete occurrences you want to sum.

    Counters are aggregated server-side (summed over time windows) and are ideal
    for alerting on rates (e.g., errors/minute). Use instead of log_event when
    you need numeric aggregation rather than individual event records.

    Examples: commands executed, models downloaded, auth failures.
    """
    if not _initialized:
        _ensure_initialized()
    if not _initialized:
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
    plugin_name: str,
    value: float,
    attributes: Optional[Dict[str, Any]] = None,
) -> None:
    """Record a distribution measurement. Use for values you want percentiles of.

    Histograms compute p50/p95/p99 server-side, making them ideal for latency
    and size measurements. Use instead of log_event when you need statistical
    summaries rather than individual event records.

    Examples: command duration, download size, response time.
    """
    if not _initialized:
        _ensure_initialized()
    if not _initialized:
        return
    try:
        from anaconda_opentelemetry import record_histogram

        record_histogram(name, value, attributes=_build_attrs(attributes, plugin_name))
    except Exception:
        pass


def log_event(
    body: str,
    event_name: str,
    plugin_name: str,
    attributes: Optional[Dict[str, Any]] = None,
) -> None:
    """Send a structured log event. No-ops when telemetry is disabled.

    Note: Unlike increment_counter/record_histogram (which return False when
    uninitialized), the upstream send_event() raises RuntimeError. The broad
    except here is intentional — it ensures the CLI never crashes due to
    telemetry, even if _initialized becomes stale or the upstream behavior
    changes.
    """
    if not _initialized:
        _ensure_initialized()
    if not _initialized:
        return
    try:
        from anaconda_opentelemetry.signals import send_event

        send_event(body, event_name, attributes=_build_attrs(attributes, plugin_name))
    except Exception:
        pass


def _build_attrs(
    attributes: Optional[Dict[str, Any]], plugin_name: str
) -> Dict[str, Any]:
    attrs = dict(attributes or {})
    attrs["source"] = "anaconda-cli-base"
    attrs["plugin"] = plugin_name
    return attrs


@contextmanager
def suppress_http_spans() -> Generator[None, None, None]:
    """Suppress HTTP-level spans inside a block to reduce trace noise.

    Use when polling or retrying produces many identical HTTP spans that
    obscure the real operation. The parent span still records full duration,
    and HTTP metrics (counters/histograms) are still emitted — only spans
    are suppressed.

    Usage:
        with traced("servers_wait_for_running") as span:
            with suppress_http_spans():
                while status != "running":
                    status = client.servers.status(server_id)
            span.add_attributes({"final_status": status})
    """
    token = _suppress_http.set(True)
    try:
        yield
    finally:
        _suppress_http.reset(token)


def is_http_suppressed() -> bool:
    return _suppress_http.get()


class _NoOpSpan:
    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        pass

    def add_exception(self, exc: Exception) -> None:
        pass

    def set_error_status(self, msg: Optional[str] = None) -> None:
        pass

    def add_attributes(self, attributes: Dict[str, Any]) -> None:
        pass
