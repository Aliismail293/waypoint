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
            yielded_content = False
            retry = False
            # Responses are forwarded as they arrive (so streamed text isn't delayed). A malformed turn carries
            # no content, so it can be held back and retried as long as nothing was forwarded before it.
            async for response in super().generate_content_async(llm_request, stream):
                malformed = response.error_code in RETRYABLE_FINISH or (
                    response.finish_reason is not None and response.finish_reason.name in RETRYABLE_FINISH
                )
                if malformed and not yielded_content and attempt < self.max_retries:
                    logger.warning("Gemini returned %s; retrying (attempt %d)", response.error_code, attempt + 1)
                    retry = True
                    break
                if response.content and response.content.parts:
                    yielded_content = True
                yield response
            if not retry:
                return


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
