"""OpenTelemetry's global TracerProvider can be set only once per process, so the whole test session shares one
provider with an in-memory exporter; tests read finished spans through the `spans` fixture."""

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

EXPORTER = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(EXPORTER))  # Simple (not Batch): spans are exported on end
trace.set_tracer_provider(_provider)


@pytest.fixture
def spans() -> InMemorySpanExporter:
    EXPORTER.clear()
    return EXPORTER
