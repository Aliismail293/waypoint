"""A scripted ADK model for the repros: no network, no credentials."""

from collections.abc import AsyncGenerator

from google.adk.models._capabilities import LlmCapabilities
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import Field


class FakeModel(BaseLlm):
    model: str = "fake-model"
    script: list[LlmResponse] = Field(default_factory=list)
    calls: int = 0

    @property
    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities(output_schema_and_tools=False)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self.calls += 1
        yield self.script.pop(0)


def text(t: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=t)]))


def call(name: str, args: dict | None = None) -> LlmResponse:
    fc = types.FunctionCall(name=name, args=args or {})
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=fc)]))
