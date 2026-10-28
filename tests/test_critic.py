"""The plan critic loop, with scripted model turns so each path is exact."""

from google.adk.agents import LlmAgent
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from waypoint.agents.critic import build_plan_with_critic, critic_instruction, is_approved, make_check_plan_tool
from waypoint.llm.mock import MockLlm
from waypoint.tools.catalog import InMemoryCatalog

VALID = "2026 Fall: MAT102H5"
INVALID = "2026 Fall: MAT301H5"  # prerequisites not met


def text(t: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=t)]))


def check(plan: str) -> LlmResponse:
    call = types.FunctionCall(name="check_plan", args={"plan": plan, "completed_courses": []})
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=call)]))


def build(drafts: list[str], critic_turns: list[LlmResponse]):
    drafter = LlmAgent(name="plan_drafter", model=MockLlm(script=[text(d) for d in drafts]), instruction="draft")
    critic = LlmAgent(
        name="plan_critic",
        model=MockLlm(script=critic_turns),
        instruction=critic_instruction,
        include_contents="none",
        tools=[make_check_plan_tool(InMemoryCatalog())],
    )
    return build_plan_with_critic(drafter=drafter, critic=critic, description="plans")


async def run(agent) -> tuple[str, list[str]]:
    runner = Runner(app_name="t", agent=agent, session_service=InMemorySessionService(), auto_create_session=True)
    message = types.Content(role="user", parts=[types.Part(text="Plan my Math minor")])
    final, authors = "", []
    async for event in runner.run_async(user_id="u", session_id="s", new_message=message):
        authors.append(event.author)
        if event.author == "planner_agent" and event.content:
            final = event.content.parts[0].text
    return final, authors


def test_verdict_parsing():
    assert is_approved("APPROVE\nlooks good")
    assert is_approved("**APPROVE**")
    assert not is_approved("REVISE\n- move MAT301H5")
    assert not is_approved("")


async def test_approved_first_time():
    final, authors = await run(build(["draft 1"], [check(VALID), text("APPROVE")]))
    assert final == "draft 1"
    assert authors.count("plan_drafter") == 1


async def test_revision_then_approval():
    critic_turns = [check(INVALID), text("REVISE\n- fix MAT301H5"), check(VALID), text("APPROVE")]
    agent = build(["draft 1", "draft 2"], critic_turns)
    final, authors = await run(agent)
    assert final == "draft 2"
    assert "plan_critic" in authors


async def test_failed_check_overrules_an_approve():
    agent = build(["draft 1", "draft 2"], [check(INVALID), text("APPROVE"), check(VALID), text("APPROVE")])
    final, _ = await run(agent)
    assert final == "draft 2"  # the model said APPROVE, but the deterministic check failed


async def test_revisions_are_capped_and_concerns_flagged():
    agent = build(["d1", "d2", "d3", "d4"], [text("REVISE\n- a"), text("REVISE\n- b"), text("REVISE\n- c"), text("x")])
    final, authors = await run(agent)
    assert final.startswith("d3")  # 1 draft + 2 revisions, never a 4th
    assert "reviewer still had concerns" in final
