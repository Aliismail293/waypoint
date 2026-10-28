"""Chooses the model behind every agent: the offline mock, or Gemini on Vertex AI."""

import os

from google.adk.models.base_llm import BaseLlm

from waypoint.config import Settings, get_settings
from waypoint.llm.mock import MockLlm


def agent_model(settings: Settings | None = None, *, name: str | None = None) -> BaseLlm | str:
    """A model for an LlmAgent. Real calls only when WAYPOINT_LLM=vertex (spec ground rule 3)."""
    settings = settings or get_settings()
    if settings.llm != "vertex":
        return MockLlm()
    # ADK's Gemini model reads these to route through Vertex AI with application-default credentials.
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", settings.gcp_project)
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", settings.model_location)
    return name or settings.agent_model
