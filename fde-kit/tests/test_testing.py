import pytest
from google.adk.models.llm_request import LlmRequest

from fde_kit.testing import ScriptedLlm, ScriptEmptyError, text_response, tool_call_response


async def next_response(llm: ScriptedLlm):
    return [r async for r in llm.generate_content_async(LlmRequest())]


async def test_replays_script_in_order_and_records_requests():
    llm = ScriptedLlm(model="gemini-3.5-flash", script=[tool_call_response("search", {"q": "x"})])
    llm.push(text_response("done", input_tokens=7, thinking_tokens=3, model="pinned-model"))

    (first,) = await next_response(llm)
    assert first.content.parts[0].function_call.name == "search"
    assert first.model_version == "gemini-3.5-flash"  # filled in from the model name, for pricing
    assert first.usage_metadata.prompt_token_count == 10

    (second,) = await next_response(llm)
    assert second.content.parts[0].text == "done"
    assert second.model_version == "pinned-model"
    assert second.usage_metadata.thoughts_token_count == 3
    assert len(llm.requests) == 2


async def test_running_out_of_script_fails_loudly():
    with pytest.raises(ScriptEmptyError):
        await next_response(ScriptedLlm())


def test_declares_capabilities():
    assert ScriptedLlm().capabilities.output_schema_and_tools is False
