"""OpenTelemetry tracing, on only when OTEL_EXPORTER_OTLP_ENDPOINT is set.

LangSmith needs no setup here: it reads LANGSMITH_TRACING and LANGSMITH_API_KEY itself, and
every LLM client is wrapped for it in ``dropship.llm.client``.
"""

from __future__ import annotations

import os

_configured = False


def tracing_enabled() -> bool:
    return bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))


def setup_tracing(service_name: str) -> None:
    global _configured
    if _configured or not tracing_enabled():
        return
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    _configured = True


def instrument_fastapi(app) -> None:
    if tracing_enabled():
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)


def temporal_interceptors() -> list:
    if not tracing_enabled():
        return []
    from temporalio.contrib.opentelemetry import TracingInterceptor

    return [TracingInterceptor()]
