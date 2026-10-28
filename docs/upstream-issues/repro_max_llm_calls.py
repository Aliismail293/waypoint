"""RunConfig.max_llm_calls doesn't count model calls made inside AgentTool children.

Expected: with max_llm_calls=3, a run raises LlmCallsLimitExceededError before a 4th model call is made.
Actual: each AgentTool child run gets its own counter (with the same limit), and the child's calls are not
added to the parent's. A root that delegates three times, with each child run making 2 calls, makes 9 model
calls. Only then does it raise, on the root's own 4th call. In general a run can make roughly
(delegations + 1) x max_llm_calls calls.
"""

import asyncio

from _fake_model import FakeModel, call, text
from google.adk.agents import LlmAgent
from google.adk.agents.invocation_context import LlmCallsLimitExceededError
from google.adk.agents.run_config import RunConfig
from google.adk.runners import InMemoryRunner
from google.adk.tools.agent_tool import AgentTool
from google.genai import types


def lookup(q: str) -> dict:
    """Look something up."""
    return {"result": q}


async def main() -> None:
    child_model = FakeModel(script=[call("lookup", {"q": "a"}), text("child done")] * 3)
    root_model = FakeModel(script=[call("child", {"request": "go"})] * 3 + [text("root done")])
    child = LlmAgent(name="child", description="does lookups", model=child_model, tools=[lookup])
    root = LlmAgent(name="root", model=root_model, tools=[AgentTool(child)])
    runner = InMemoryRunner(agent=root, app_name="repro")
    session = await runner.session_service.create_session(app_name="repro", user_id="u")
    message = types.Content(role="user", parts=[types.Part(text="hi")])
    try:
        async for _ in runner.run_async(
            user_id="u", session_id=session.id, new_message=message, run_config=RunConfig(max_llm_calls=3)
        ):
            pass
        outcome = "completed without LlmCallsLimitExceededError"
    except LlmCallsLimitExceededError:
        outcome = "LlmCallsLimitExceededError raised"
    print(f"max_llm_calls=3 -> {outcome}; model calls made: {root_model.calls + child_model.calls}")


asyncio.run(main())
