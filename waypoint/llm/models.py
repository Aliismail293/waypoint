"""Chooses the model behind every agent: the offline mock, or Gemini on Vertex AI."""

import logging
import os
from collections.abc import AsyncGenerator

from google.adk.models.base_llm import BaseLlm
from google.adk.models.google_llm import Gemini
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from waypoint.config import Settings, get_settings
from waypoint.llm.mock import MockLlm

logger = logging.getLogger(__name__)
RETRYABLE_FINISH = {"MALFORMED_RESPONSE", "MALFORMED_FUNCTION_CALL"}


class RetryingGemini(Gemini):
    """Gemini that retries a turn the model returned as malformed.

    Gemini 3.5 Flash occasionally ends a turn with finish_reason MALFORMED_RESPONSE and no content (seen in
    ~1 of 5 live runs right after an AgentTool result). ADK passes that through as an empty final answer, so
    we retry the same request, at most `max_retries` times. Only non-streaming calls are retried.
    """

    max_retries: int = 2

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        for attempt in range(self.max_retries + 1):
            responses = [r async for r in super().generate_content_async(llm_request, stream)]
            last = responses[-1] if responses else None
            malformed = last is not None and (
                last.error_code in RETRYABLE_FINISH
                or (last.finish_reason is not None and last.finish_reason.name in RETRYABLE_FINISH)
            )
            if not malformed or stream or attempt == self.max_retries:
                for response in responses:
                    yield response
                return
            logger.warning("Gemini returned %s; retrying (attempt %d)", last.error_code, attempt + 1)  # type: ignore[union-attr]


def agent_model(settings: Settings | None = None, *, name: str | None = None) -> BaseLlm:
    """A model for an LlmAgent. Real calls only when WAYPOINT_LLM=vertex (spec ground rule 3)."""
    settings = settings or get_settings()
    if settings.llm != "vertex":
        return MockLlm()
    # ADK's Gemini model reads these to route through Vertex AI with application-default credentials.
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", settings.gcp_project)
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", settings.model_location)
    # 429s on the shared global endpoint come in short bursts; back off and retry instead of failing the request.
    retry = types.HttpRetryOptions(attempts=4, initial_delay=2, max_delay=20, http_status_codes=[429, 503])
    return RetryingGemini(model=name or settings.agent_model, retry_options=retry)
