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
from typing import Any, Dict, Optional, Union

logger = logging.getLogger(__name__)

AttributeValue = Union[str, bool, int, float, Sequence[Union[str, bool, int, float]]]

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
        return cfg.enabled
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
        from anaconda_cli_base.telemetry_config import (
            TelemetryConfig,
            AUTHENTICATED_ENDPOINT,
            PUBLIC_ENDPOINT,
        )

        cfg = TelemetryConfig()

        api_key = _get_api_key()
        if api_key:
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

        attrs = ResourceAttributes(
            service_name="anaconda-cli-base",
            service_version=__version__,
            environment="",
            anon_usage=cfg.share_session_identity,
        )
        initialize_telemetry(
            config=config,
            attributes=attrs,
            signal_types=["metrics", "tracing"],
        )
        _initialized = True
    except ImportError:
        # anaconda-opentelemetry not installed — expected in environments
        # without the [telemetry] extra.
        pass
    except Exception as exc:
        # SDK is installed but initialization failed (bad endpoint, auth,
        # version mismatch, etc.). Log so misconfiguration is diagnosable.
        logger.warning("Telemetry enabled but failed to initialize: %s", exc)


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
    return _CommandInfo(command=command_name, plugin=plugin_name)


def _after_command(
    info: Optional[_CommandInfo], success: bool, error: Optional[Exception] = None
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
