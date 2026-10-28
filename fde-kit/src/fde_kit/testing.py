"""Offline test double for ADK models: `ScriptedLlm` returns queued responses in order, with token usage.

Give every agent in a tree the same `ScriptedLlm` and the script reads as the whole conversation, in the order
ADK makes the calls (parent, then the child an AgentTool runs, then the parent again...):

    llm = ScriptedLlm(script=[
        tool_call_response("lookup_agent", {"request": "MAT224"}),   # parent delegates
        text_response("MAT224 needs MAT102.", input_tokens=120),     # child answers
        text_response("You need MAT102 first."),                     # parent's final answer
    ])
    child = LlmAgent(name="lookup_agent", model=llm, ...)
    parent = LlmAgent(name="root", model=llm, tools=[AgentTool(child)])

Usage metadata is attached to each response so cost and token tracking can be tested without a network.
If agents run in parallel, call order isn't fixed; script per agent (one ScriptedLlm each) in that case.
"""

from collections.abc import AsyncGenerator
from typing import Any

from google.adk.models import LlmCapabilities
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import Field


class ScriptEmptyError(RuntimeError):
    """The agent made more model calls than the test scripted."""


class ScriptedLlm(BaseLlm):
    """An ADK model that replays `script` and records every request it was sent (in `requests`)."""

    model: str = "scripted-llm"
    script: list[LlmResponse] = Field(default_factory=list)
    requests: list[LlmRequest] = Field(default_factory=list)

    # Must be overridden: BaseLlm's default works out capabilities from the model *name*, a deprecated
    # fallback that doesn't know a made-up name like "scripted-llm".
    @property
    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities(output_schema_and_tools=False)

    def push(self, *responses: LlmResponse) -> None:
        self.script.extend(responses)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self.requests.append(llm_request)
        if not self.script:
            # Fail loudly: silently answering would hide an agent that loops or calls the model too often.
            raise ScriptEmptyError(f"ScriptedLlm has no response left for call #{len(self.requests)}.")
        response = self.script.pop(0)
        if not response.model_version:
            # Real models report the version they ran; cost tracking prices by it.
            response.model_version = self.model
        yield response


def usage(
    input_tokens: int = 10, output_tokens: int = 5, thinking_tokens: int = 0, cached_tokens: int = 0
) -> types.GenerateContentResponseUsageMetadata:
    """Usage metadata shaped like Gemini's (cached tokens are part of input; thinking is separate)."""
    return types.GenerateContentResponseUsageMetadata(
        prompt_token_count=input_tokens,
        candidates_token_count=output_tokens,
        thoughts_token_count=thinking_tokens or None,
        cached_content_token_count=cached_tokens or None,
        total_token_count=input_tokens + output_tokens + thinking_tokens,
    )


def text_response(
    text: str,
    *,
    input_tokens: int = 10,
    output_tokens: int = 5,
    thinking_tokens: int = 0,
    cached_tokens: int = 0,
    model: str | None = None,
) -> LlmResponse:
    """A final text answer."""
    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        usage_metadata=usage(input_tokens, output_tokens, thinking_tokens, cached_tokens),
        model_version=model,
    )


def tool_call_response(
    name: str,
    args: dict[str, Any] | None = None,
    *,
    input_tokens: int = 10,
    output_tokens: int = 5,
    thinking_tokens: int = 0,
    cached_tokens: int = 0,
    model: str | None = None,
) -> LlmResponse:
    """A call to the tool (or AgentTool) named `name`."""
    call = types.FunctionCall(name=name, args=args or {})
    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part(function_call=call)]),
        usage_metadata=usage(input_tokens, output_tokens, thinking_tokens, cached_tokens),
        model_version=model,
    )
