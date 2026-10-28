"""Conversation-state optimisation: pruning old specialist outputs and summary compaction (offline)."""

import asyncio

from google.genai import types

from waypoint.agents.runtime import APP_NAME, WaypointRuntime
from waypoint.agents.state import PRUNED, prune_old_tool_outputs
from waypoint.config import Settings


def fr(name: str, text: str) -> types.Part:
    return types.Part(function_response=types.FunctionResponse(name=name, response={"result": text}))


def test_prune_keeps_the_current_turn():
    contents = [
        types.Content(role="user", parts=[types.Part(text="turn 1")]),
        types.Content(role="user", parts=[fr("policy_agent", "long answer 1")]),
        types.Content(role="model", parts=[types.Part(text="answer 1")]),
        types.Content(role="user", parts=[types.Part(text="turn 2")]),
        types.Content(role="user", parts=[fr("course_data_agent", "fresh answer 2")]),
    ]
    assert prune_old_tool_outputs(contents) == 1
    assert contents[1].parts[0].function_response.response == {"result": PRUNED}
    assert contents[2].parts[0].text == "answer 1"  # the orchestrator's own summary of turn 1 stays
    assert contents[4].parts[0].function_response.response == {"result": "fresh answer 2"}
    assert prune_old_tool_outputs(contents) == 0  # idempotent


async def test_conversation_is_compacted_and_summary_metered():
    settings = Settings(compaction_interval=2, compaction_overlap=1)
    runtime = WaypointRuntime.from_settings(settings)
    session_id = None
    for question in ["What is MAT224 about?", "And MAT301?", "How many credits is the Math minor?"]:
        answer = await runtime.ask("alice", question, session_id)
        session_id = answer.session_id
    for _ in range(50):  # compaction runs after the answer is returned
        session = await runtime.session_service.get_session(app_name=APP_NAME, user_id="alice", session_id=session_id)
        if any(e.actions and e.actions.compaction for e in session.events):
            break
        await asyncio.sleep(0.05)
    assert any(e.actions and e.actions.compaction for e in session.events)
    assert runtime.summarizer is not None and runtime.summarizer.usage.calls >= 1


def test_defaults_keep_history_append_only():
    runtime = WaypointRuntime.from_settings(Settings())
    config = runtime.app.events_compaction_config
    assert config is not None and config.token_threshold == 30000 and config.compaction_interval is None
    assert not any(p.name == "waypoint_prune_old_tool_outputs" for p in runtime.app.plugins)


def test_state_opt_can_be_switched_off():
    runtime = WaypointRuntime.from_settings(Settings(state_opt=False))
    assert runtime.summarizer is None and runtime.app.events_compaction_config is None
