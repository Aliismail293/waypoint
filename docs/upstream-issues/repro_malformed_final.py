"""A MALFORMED_FUNCTION_CALL / MALFORMED_RESPONSE turn becomes a silent, empty final response.

When Gemini ends a turn with finish_reason MALFORMED_FUNCTION_CALL (or MALFORMED_RESPONSE), ADK turns it into an
LlmResponse with error_code set and no content. The runner yields it as the agent's final response, so the
caller gets an empty answer: no exception and no retry. Many callers only read final-response text and
never check error_code.
"""

import asyncio

from _fake_model import FakeModel
from google.adk.agents import LlmAgent
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types


async def main() -> None:
    # The shape ADK's Gemini model produces for this finish reason (google_llm -> LlmResponse.create).
    malformed = LlmResponse.create(
        types.GenerateContentResponse(
            candidates=[types.Candidate(finish_reason=types.FinishReason.MALFORMED_FUNCTION_CALL, content=None)]
        )
    )
    agent = LlmAgent(name="root", model=FakeModel(script=[malformed]))
    runner = InMemoryRunner(agent=agent, app_name="repro")
    session = await runner.session_service.create_session(app_name="repro", user_id="u")
    message = types.Content(role="user", parts=[types.Part(text="hi")])
    async for event in runner.run_async(user_id="u", session_id=session.id, new_message=message):
        print(
            f"final={event.is_final_response()} error_code={event.error_code!r} "
            f"content={event.content!r} text_parts={[p.text for p in (event.content.parts if event.content else [])]}"
        )


asyncio.run(main())
