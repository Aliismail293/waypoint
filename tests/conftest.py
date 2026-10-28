"""Test-session setup. OpenTelemetry's global TracerProvider can be set only once per process, so the whole
test session shares one provider with an in-memory exporter (tests read spans via the `spans` fixture)."""

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import waypoint.observability.tracing as tracing_module

EXPORTER = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(EXPORTER))
trace.set_tracer_provider(_provider)
tracing_module._provider = _provider  # setup_tracing() becomes a no-op in tests: no real exporters


@pytest.fixture
def spans() -> InMemorySpanExporter:
    EXPORTER.clear()
    return EXPORTER
