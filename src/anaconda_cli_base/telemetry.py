"""Centralized telemetry for the Anaconda CLI framework.

All functions are safe to call regardless of whether telemetry is configured.
When no endpoint is set, every function is a no-op. Actual imports of the
OTel SDK are deferred until telemetry is enabled to keep CLI startup fast.
"""

import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

_initialized = False

_suppress_http: ContextVar[bool] = ContextVar("_suppress_http", default=False)


@dataclass
class _CommandInfo:
    command: str
    plugin: str
    start_time: float = field(default_factory=time.perf_counter)


def _is_enabled() -> bool:
    # Early-exit: OTel SDK checks this deeper in the stack, but catching it here
    # avoids reading config.toml and importing anaconda-opentelemetry for nothing.
    if os.environ.get("OTEL_SDK_DISABLED", "").lower() in ("true", "1", "yes"):
        return False
    try:
        from anaconda_cli_base.telemetry_config import TelemetryConfig

        cfg = TelemetryConfig()
        return bool(cfg.endpoint)
    except Exception:
        return False


def _ensure_initialized() -> None:
    global _initialized
    if _initialized or not _is_enabled():
        return
    try:
        from anaconda_opentelemetry import (
            Configuration,
            ResourceAttributes,
            initialize_telemetry,
        )

        from anaconda_cli_base import __version__
        from anaconda_cli_base.telemetry_config import TelemetryConfig

        cfg = TelemetryConfig()

        api_key = _get_api_key()
        if api_key:
            endpoint = cfg.endpoint
        else:
            endpoint = cfg.public_endpoint

        config = Configuration(
            default_endpoint=endpoint,
            default_auth_token=api_key,
        )
        if cfg.skip_internet_check:
            config.set_skip_internet_check(True)

        config.set_metrics_export_interval_ms(1000)
        config.set_tracing_export_interval_ms(1000)

        attrs = ResourceAttributes(
            service_name="anaconda-cli-base",
            service_version=__version__,
            environment="",
            anon_usage=cfg.anon_usage,
        )
        initialize_telemetry(
            config=config,
            attributes=attrs,
            signal_types=["metrics", "tracing"],
        )
        _initialized = True
    except Exception:
        pass


def _get_api_key():
    try:
        from anaconda_auth.exceptions import TokenNotFoundError
        from anaconda_auth.token import TokenInfo

        token_info = TokenInfo.load("anaconda.com")
        return token_info.api_key
    except TokenNotFoundError:
        return None
    except Exception:
        return None


def _before_command(args, prog_name) -> Optional[_CommandInfo]:
    _ensure_initialized()
    if not _initialized:
        return None
    command_name = " ".join(args[:2]) if args else prog_name or "unknown"
    plugin_name = args[0] if args else "root"
    return _CommandInfo(command=command_name, plugin=plugin_name)


def _after_command(
    info: Optional[_CommandInfo], success: bool, error: Optional[Exception] = None
) -> None:
    if info is None:
        return
    try:
        from anaconda_opentelemetry import increment_counter, record_histogram

        duration_ms = (time.perf_counter() - info.start_time) * 1000
        attrs = {
            "command": info.command,
            "plugin": info.plugin,
            "source": "anaconda-cli-base",
        }

        record_histogram("cli_command_duration_ms", duration_ms, attrs)
        increment_counter("cli_command_invoked", attributes=attrs)
        if not success:
            error_attrs = {
                **attrs,
                "error.type": type(error).__name__ if error else "unknown",
            }
            increment_counter("cli_command_errors", attributes=error_attrs)
    except Exception:
        pass

    _shutdown_telemetry()


_FLUSH_TIMEOUT_MS = 500


def _shutdown_telemetry() -> None:
    try:
        from opentelemetry import trace, metrics

        trace_provider = trace.get_tracer_provider()
        if hasattr(trace_provider, "shutdown"):
            trace_provider.shutdown(timeout_millis=_FLUSH_TIMEOUT_MS)

        meter_provider = metrics.get_meter_provider()
        if hasattr(meter_provider, "shutdown"):
            meter_provider.shutdown(timeout_millis=_FLUSH_TIMEOUT_MS)
    except Exception:
        pass


def is_telemetry_enabled() -> bool:
    return _initialized


@contextmanager
def traced(
    name: str,
    attributes: Optional[Dict[str, Any]] = None,
    source: Optional[str] = None,
):
    """Create a traced span. No-ops when telemetry is disabled.

    The except clause yielding _NoOpSpan is defensive against an upstream
    bug in anaconda-opentelemetry's get_trace(): when tracing is not
    initialized, it does `return None` inside a @contextmanager generator
    instead of yielding, which causes a TypeError/StopIteration. This wrapper
    guarantees callers always get a usable span object.
    """
    if not _initialized:
        yield _NoOpSpan()
        return
    try:
        from anaconda_opentelemetry import get_trace

        span_attrs = dict(attributes or {})
        if source:
            span_attrs["source"] = source
        with get_trace(name, attributes=span_attrs) as span:
            yield span
    except Exception:
        yield _NoOpSpan()


def count(
    name: str,
    by: int = 1,
    attributes: Optional[Dict[str, Any]] = None,
    source: Optional[str] = None,
) -> None:
    if not _initialized:
        return
    try:
        from anaconda_opentelemetry import increment_counter

        metric_attrs = dict(attributes or {})
        if source:
            metric_attrs["source"] = source
        increment_counter(name, by=by, attributes=metric_attrs)
    except Exception:
        pass


def histogram(
    name: str,
    value: float,
    attributes: Optional[Dict[str, Any]] = None,
    source: Optional[str] = None,
) -> None:
    if not _initialized:
        return
    try:
        from anaconda_opentelemetry import record_histogram

        metric_attrs = dict(attributes or {})
        if source:
            metric_attrs["source"] = source
        record_histogram(name, value, attributes=metric_attrs)
    except Exception:
        pass


def log_event(
    body: str,
    event_name: str,
    attributes: Optional[Dict[str, Any]] = None,
    source: Optional[str] = None,
) -> None:
    """Send a structured log event. No-ops when telemetry is disabled.

    Note: Unlike increment_counter/record_histogram (which return False when
    uninitialized), the upstream send_event() raises RuntimeError. The broad
    except here is intentional — it ensures the CLI never crashes due to
    telemetry, even if _initialized becomes stale or the upstream behavior
    changes.
    """
    if not _initialized:
        return
    try:
        from anaconda_opentelemetry.signals import send_event

        event_attrs = dict(attributes or {})
        if source:
            event_attrs["source"] = source
        send_event(body, event_name, attributes=event_attrs)
    except Exception:
        pass


@contextmanager
def suppress_http_spans():
    """Suppress HTTP-level spans inside this block.

    Uses contextvars for proper isolation across threads and asyncio tasks.
    The parent span (from traced()) still records the full duration.
    HTTP metrics (counters/histograms) are still emitted — only spans are suppressed.
    """
    token = _suppress_http.set(True)
    try:
        yield
    finally:
        _suppress_http.reset(token)


def is_http_suppressed() -> bool:
    return _suppress_http.get()


class _NoOpSpan:
    def add_event(self, name: str, attributes=None):
        pass

    def add_exception(self, exc: Exception):
        pass

    def set_error_status(self, msg: Optional[str] = None):
        pass

    def add_attributes(self, attributes: dict):
        pass
