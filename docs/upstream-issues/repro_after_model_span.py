"""Plugins can't annotate the model-call span: after_model_callback runs after generate_content has ended.

Expected: inside after_model_callback, trace.get_current_span() is the call's generate_content span, so a plugin
can attach per-call attributes (cost, cache hit rate) to it.
Actual: the generate_content span has already ended, and the attribute lands on the enclosing span.
"""

import asyncio

from _fake_model import FakeModel, text
from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

exporter = InMemorySpanExporter()
provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(exporter))
trace.set_tracer_provider(provider)


class Annotate(BasePlugin):
    def __init__(self) -> None:
        super().__init__(name="annotate")

    async def after_model_callback(self, *, callback_context, llm_response):
        span = trace.get_current_span()
        span.set_attribute("repro.cost_usd", 0.01)
        print(
            f"current span in after_model_callback: {getattr(span, 'name', span)!r}, is_recording={span.is_recording()}"
        )


async def main() -> None:
    agent = LlmAgent(name="root", model=FakeModel(script=[text("hello")]))
    runner = Runner(
        app=App(name="repro", root_agent=agent, plugins=[Annotate()]), session_service=InMemorySessionService()
    )
    session = await runner.session_service.create_session(app_name="repro", user_id="u")
    message = types.Content(role="user", parts=[types.Part(text="hi")])
    async for _ in runner.run_async(user_id="u", session_id=session.id, new_message=message):
        pass
    for span in exporter.get_finished_spans():
        print(f"{span.name:<40} has repro.cost_usd: {'repro.cost_usd' in (span.attributes or {})}")


asyncio.run(main())
