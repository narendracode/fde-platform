"""OpenTelemetry setup — single entry point for all instrumentation.

Call setup_otel() once at process startup, then instrument_*() for each
subsystem. All functions are no-ops when OTEL_ENABLED is not "true".
"""

from __future__ import annotations

import os

from opentelemetry import trace

_initialized = False


def is_enabled() -> bool:
    return os.getenv("OTEL_ENABLED", "false").lower() in ("true", "1", "yes")


def setup_otel() -> None:
    """Initialize the OTel SDK — idempotent, safe to call multiple times."""
    global _initialized
    if _initialized or not is_enabled():
        return

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({
        SERVICE_NAME: os.getenv("OTEL_SERVICE_NAME", "fde-agent"),
        "service.version": "0.1.0",
        "deployment.environment": os.getenv("ENVIRONMENT", "development"),
    })

    provider = TracerProvider(resource=resource)

    # OTEL_EXPORTER_OTLP_ENDPOINT should be the base URL (e.g. http://jaeger:4318).
    # The HTTP exporter appends /v1/traces automatically.
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
    exporter = OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces")
    provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)
    _initialized = True


# ── Per-subsystem instrumentation ────────────────────────────────────────────

def instrument_fastapi(app) -> None:
    if not is_enabled():
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    FastAPIInstrumentor.instrument_app(
        app,
        # Exclude health/metrics endpoints from traces to reduce noise.
        excluded_urls=r"health.*,metrics.*",
    )


def instrument_sqlalchemy(engine=None) -> None:
    """Must be called before the first DB query is executed.

    AsyncEngine wraps a sync engine internally. SQLAlchemy's event system only
    supports sync listeners, so we must pass the underlying sync engine.
    """
    if not is_enabled():
        return
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    kwargs = {}
    if engine is not None:
        # AsyncEngine → use .sync_engine; plain sync engine → pass as-is
        kwargs["engine"] = getattr(engine, "sync_engine", engine)
    SQLAlchemyInstrumentor().instrument(**kwargs)


def instrument_redis() -> None:
    if not is_enabled():
        return
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    RedisInstrumentor().instrument()


def instrument_celery() -> None:
    if not is_enabled():
        return
    from opentelemetry.instrumentation.celery import CeleryInstrumentor
    CeleryInstrumentor().instrument()


# ── Helpers used by application code ─────────────────────────────────────────

def get_tracer(name: str):
    """Return an OTel Tracer. Always safe — returns a no-op tracer when disabled."""
    return trace.get_tracer(name)


def current_trace_id() -> str | None:
    """Return the active span's trace ID as a 32-char hex string, or None."""
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        return format(ctx.trace_id, "032x")
    return None


def jaeger_url(trace_id: str | None) -> str | None:
    """Construct a Jaeger UI deep-link for a trace ID."""
    if not trace_id:
        return None
    base = os.getenv("JAEGER_UI_URL", "http://localhost:16686")
    return f"{base.rstrip('/')}/trace/{trace_id}"
