"""The assistant: one ADK agent over the MCP tools, with fde-kit's MetricsPlugin for budget and cost."""

import os
import sys
import time
from dataclasses import dataclass
from typing import Any

from fde_kit.adk_metrics import MetricsPlugin, request_scope
from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.models.base_llm import BaseLlm
from google.adk.models.google_llm import Gemini
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from google.genai import types
from mcp import StdioServerParameters

from parks_demo.config import Settings

INSTRUCTION = """You answer questions about visits to Parks Canada places (national parks, national historic
sites, marine conservation areas) using the attendance tools. Always use a tool; never answer from memory.
Fiscal years run April to March and are written like 2024-25. Give numbers exactly as the tools return them,
with the fiscal year. If a place didn't report for a year, say so instead of guessing. If no place matches,
say that. Only answer questions about Parks Canada attendance; politely decline anything else.
End every answer with: "Source: Parks Canada attendance, Open Government Licence – Canada." """


@dataclass
class Reply:
    text: str
    metrics: dict[str, Any]


def default_model(settings: Settings) -> BaseLlm:
    if settings.llm == "vertex":
        os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", settings.gcp_project)
        os.environ.setdefault("GOOGLE_CLOUD_LOCATION", settings.model_location)
        retry = types.HttpRetryOptions(attempts=4, initial_delay=2, max_delay=20, http_status_codes=[429, 503])
        return Gemini(model=settings.model, retry_options=retry)
    from fde_kit.testing import ScriptedLlm

    return ScriptedLlm()  # tests push responses; an empty script fails loudly


class Assistant:
    def __init__(self, settings: Settings, model: BaseLlm | None = None, server_command: list[str] | None = None):
        command, *args = server_command or [sys.executable, "-m", "parks_demo.mcp_server"]
        tools = McpToolset(
            connection_params=StdioConnectionParams(
                server_params=StdioServerParameters(command=command, args=args, env=dict(os.environ)), timeout=20
            )
        )
        agent = LlmAgent(
            name="parks_assistant",
            model=model or default_model(settings),
            instruction=INSTRUCTION,
            tools=[tools],
            generate_content_config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=2048,
                thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL)
                if settings.llm == "vertex"
                else None,
            ),
        )
        self.metrics_plugin = MetricsPlugin(max_model_calls=settings.max_model_calls, attribute_prefix="parks")
        self.runner = Runner(
            app=App(name="parks_demo", root_agent=agent, plugins=[self.metrics_plugin]),
            session_service=InMemorySessionService(),
        )

    async def ask(self, user_id: str, question: str) -> Reply:
        session = await self.runner.session_service.create_session(app_name="parks_demo", user_id=user_id)
        text = ""
        started = time.perf_counter()
        with request_scope() as metrics:
            message = types.Content(role="user", parts=[types.Part(text=question)])
            async for event in self.runner.run_async(user_id=user_id, session_id=session.id, new_message=message):
                if event.is_final_response() and event.content:
                    text = "\n".join(p.text for p in event.content.parts or [] if p.text and not p.thought) or text
        return Reply(text=text, metrics=metrics.to_dict(time.perf_counter() - started))
