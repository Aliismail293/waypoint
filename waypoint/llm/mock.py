"""A deterministic, offline stand-in for Gemini, used by every test and by local dev (WAYPOINT_LLM=mock).

It behaves like a tiny rule-based agent so multi-agent flows run end to end without network:

1. If the latest turn holds tool results, it writes a final answer that quotes those results and
   cites every chunk id it saw (so citation plumbing is testable).
2. Otherwise, if the agent has tools, it picks one with keyword routing rules, fills its arguments
   from the user's text, and emits a function call.
3. Otherwise it echoes a short answer.

It also reports fake but consistent token counts (~4 characters per token), so cost and token
tracking can be tested offline. For exact control in a unit test, pass `script`: a list of
LlmResponses returned in order.
"""

import json
import re
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.models._capabilities import LlmCapabilities
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import Field

COURSE_CODE = re.compile(r"\b([A-Z]{3}\d{3})(?:[HY]5)?\b")
CHUNK_ID = re.compile(r"(cal:[a-z0-9-]+#[a-z0-9-]+)")

# (tool name, trigger regex), checked in order; first tool that exists and matches wins.
ROUTING_RULES: list[tuple[str, str]] = [
    ("planner_agent", r"\bplan\b|\bby 20\d\d\b|\bfinish\b|\bgraduate\b|term-by-term"),
    ("timetable_agent", r"\bsections?\b|timetable|\b(mon|tue|wed|thu|fri)|afternoon|morning|\bfit\b"),
    ("course_data_agent", r"[A-Z]{3}\d{3}|prerequisite|prereq|exclusion|course"),
    ("policy_agent", r".*"),
    ("search_sections", r".*"),
    ("get_course", r"[A-Z]{3}\d{3}"),
    ("search_courses", r".*"),
    ("search_calendar", r".*"),
]


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class MockLlm(BaseLlm):
    model: str = "mock-llm"
    script: list[LlmResponse] = Field(default_factory=list)

    @property
    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities(output_schema_and_tools=False)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        if self.script:
            yield self.script.pop(0)
            return
        response = self._respond(llm_request)
        prompt_text = _request_text(llm_request)
        output_text = json.dumps(response.content.model_dump(mode="json")) if response.content else ""
        response.usage_metadata = types.GenerateContentResponseUsageMetadata(
            prompt_token_count=estimate_tokens(prompt_text),
            candidates_token_count=estimate_tokens(output_text),
            total_token_count=estimate_tokens(prompt_text) + estimate_tokens(output_text),
        )
        yield response

    def _respond(self, llm_request: LlmRequest) -> LlmResponse:
        last = llm_request.contents[-1] if llm_request.contents else None
        tool_results = [p.function_response for p in (last.parts or []) if p.function_response] if last else []
        if tool_results:
            return _text_response(_summarise(tool_results))

        user_text = _latest_user_text(llm_request)
        call = _choose_tool(llm_request, user_text)
        if call is not None:
            return LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=call)]))
        return _text_response(f"(mock) I can only answer from Waypoint's tools. You asked: {user_text}")


def _text_response(text: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))


def _request_text(llm_request: LlmRequest) -> str:
    parts = []
    if llm_request.config and llm_request.config.system_instruction:
        parts.append(str(llm_request.config.system_instruction))
    for content in llm_request.contents:
        for part in content.parts or []:
            parts.append(part.text or json.dumps(part.model_dump(mode="json", exclude_none=True)))
    return "\n".join(parts)


def _latest_user_text(llm_request: LlmRequest) -> str:
    for content in reversed(llm_request.contents):
        texts = [p.text for p in content.parts or [] if p.text]
        if content.role == "user" and texts:
            return " ".join(texts)
    return ""


def _choose_tool(llm_request: LlmRequest, user_text: str) -> types.FunctionCall | None:
    tools = llm_request.tools_dict
    if not tools:
        return None
    for name, pattern in ROUTING_RULES:
        if name in tools and re.search(pattern, user_text, re.IGNORECASE):
            return types.FunctionCall(name=name, args=_fill_args(tools[name], user_text))
    name = sorted(tools)[0]
    return types.FunctionCall(name=name, args=_fill_args(tools[name], user_text))


def _fill_args(tool: Any, user_text: str) -> dict[str, Any]:
    declaration = tool._get_declaration()
    schema = declaration.parameters if declaration else None
    names = list(schema.properties or {}) if schema else []
    if not names and declaration and declaration.parameters_json_schema:
        names = list(declaration.parameters_json_schema.get("properties", {}))
    code = COURSE_CODE.search(user_text)
    args: dict[str, Any] = {}
    for name in names:
        if name in ("course", "code", "course_code"):
            args[name] = code.group(1) if code else ""
        elif name == "term":
            args[name] = "2027 Winter" if re.search(r"winter", user_text, re.I) else "2026 Fall"
        elif name in ("query", "request", "question", "keyword"):
            args[name] = user_text
        elif name == "completed_courses":
            args[name] = []
    return args


def _summarise(results: list[types.FunctionResponse]) -> str:
    rendered = []
    for result in results:
        payload = json.dumps(result.response, default=str)
        rendered.append(f"{result.name}: {payload[:600]}")
    cited = sorted(set(CHUNK_ID.findall(" ".join(rendered))))
    citation_line = " ".join(f"[{cid}]" for cid in cited)
    return "(mock answer) " + " | ".join(rendered) + (f"\nSources: {citation_line}" if cited else "")
