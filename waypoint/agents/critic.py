"""Self-reflection for plans: a drafter writes a plan, a critic reviews it, and the drafter revises.

    planner_agent (PlanWithCritic)
    ├── plan_drafter   PlanReActPlanner + planning tools      writes the plan  -> state["plan_draft"]
    └── plan_critic    check_plan (deterministic validation)   reviews it       -> state["plan_critique"]

The loop is plain Python rather than a LoopAgent so the stopping rule is explicit and testable:
- the critic's first line is APPROVE or REVISE;
- a plan that fails `check_plan` is never approved, whatever the critic wrote (code beats the model);
- at most `max_revisions` revisions (2), then the latest draft goes out with the critic's open issues noted.

`planner_agent` returns only the final draft, so the orchestrator never sees the critique chatter.
"""

from collections.abc import AsyncGenerator, Callable
from typing import Any

from google.adk.agents import BaseAgent, LlmAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.events import Event
from google.adk.tools.tool_context import ToolContext
from google.adk.utils.context_utils import Aclosing
from google.genai import types

from waypoint.agents import prompts
from waypoint.tools.catalog import Catalog
from waypoint.tools.plan import validate_plan_text

DRAFT_KEY = "plan_draft"
CRITIQUE_KEY = "plan_critique"
CHECKS_KEY = "plan_checks"  # one bool per check_plan call, in order


def make_check_plan_tool(catalog: Catalog) -> Callable[..., Any]:
    def check_plan(plan: str, completed_courses: list[str], tool_context: ToolContext) -> dict:
        """Deterministically validate the plan under review. plan format:
        "2026 Fall: MAT224H5, STA256H5; 2027 Winter: MAT301H5". completed_courses: codes the student said they
        passed. Returns valid, errors and warnings."""
        result = validate_plan_text(plan, completed_courses, catalog)
        # Recorded in state so PlanWithCritic can overrule an APPROVE on a plan that fails validation.
        tool_context.state[CHECKS_KEY] = [*tool_context.state.get(CHECKS_KEY, []), bool(result["valid"])]
        return result

    return check_plan


def drafter_instruction(base: str) -> Callable[[ReadonlyContext], str]:
    def provider(ctx: ReadonlyContext) -> str:
        text = f"{base}\n\n{prompts.today_line()}"
        critique = ctx.state.get(CRITIQUE_KEY)
        if critique:
            text += (
                "\n\nA reviewer checked your previous draft and asked for a revision. Fix every issue below, "
                f"validate the revised plan with validate_plan, then write the full revised answer:\n{critique}"
            )
        return text

    return provider


def critic_instruction(ctx: ReadonlyContext) -> str:
    request = ""
    if ctx.user_content and ctx.user_content.parts:
        request = " ".join(p.text for p in ctx.user_content.parts if p.text)
    draft = ctx.state.get(DRAFT_KEY, "")
    return (
        f"{prompts.CRITIC}\n\n{prompts.today_line()}\n\n"
        f"<<<request>>>\n{request}\n<<<end request>>>\n\n<<<draft>>>\n{draft}\n<<<end draft>>>"
    )


def is_approved(critique: str) -> bool:
    first_line = next((line.strip() for line in critique.splitlines() if line.strip()), "")
    return first_line.lstrip("*# ").upper().startswith("APPROVE")


class PlanWithCritic(BaseAgent):
    """Draft → critique → revise, at most `max_revisions` times. Sub-agents: [drafter, critic]."""

    max_revisions: int = 2

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        drafter, critic = self.sub_agents
        draft = ""
        approved = False
        for _ in range(self.max_revisions + 1):
            async with Aclosing(drafter.run_async(ctx)) as agen:
                async for event in agen:
                    yield event
            new_draft = str(ctx.session.state.get(DRAFT_KEY, "") or "")
            if not new_draft.strip() or prompts.BUDGET_MESSAGE in new_draft:
                break  # out of steps: keep the last complete draft
            draft = new_draft

            checks_before = len(ctx.session.state.get(CHECKS_KEY, []))
            async with Aclosing(critic.run_async(ctx)) as agen:
                async for event in agen:
                    yield event
            critique = str(ctx.session.state.get(CRITIQUE_KEY, "") or "")
            if prompts.BUDGET_MESSAGE in critique:
                break
            new_checks = ctx.session.state.get(CHECKS_KEY, [])[checks_before:]
            approved = is_approved(critique) and all(new_checks)
            if approved:
                break

        if not draft:
            draft = "I couldn't put a plan together within Waypoint's step limit. Try asking about one program."
        elif not approved:
            draft += (
                "\n\nNote: Waypoint's plan reviewer still had concerns about this draft, so check it carefully "
                "with an academic advisor."
            )
        yield Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            content=types.Content(role="model", parts=[types.Part(text=draft)]),
        )


def build_plan_with_critic(
    *, drafter: LlmAgent, critic: LlmAgent, description: str, max_revisions: int = 2
) -> PlanWithCritic:
    drafter.output_key = DRAFT_KEY
    critic.output_key = CRITIQUE_KEY
    return PlanWithCritic(
        name="planner_agent", description=description, sub_agents=[drafter, critic], max_revisions=max_revisions
    )
