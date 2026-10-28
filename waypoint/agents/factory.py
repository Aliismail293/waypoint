"""Builds Waypoint's agent tree.

    orchestrator (root)                         hierarchical delegation: specialists are AgentTools
    ├── policy_agent        search_calendar     hybrid RAG over the calendar, always cites
    ├── course_data_agent   catalog tools       allow-listed BigQuery queries + prerequisite checks
    ├── timetable_agent     MCP toolset         custom legacy-adapter MCP server over stdio
    └── planner_agent       PlanReActPlanner    ReAct loop over the tools above + validate_plan
         (critic_agent joins in Phase 2)

Dependencies (retriever, catalog, timetable connection) are injected so tests can run the same tree
offline with in-memory stores and the mock LLM.
"""

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.planners import PlanReActPlanner
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from google.genai import types
from mcp import StdioServerParameters

from waypoint.agents import prompts
from waypoint.config import Settings, get_settings
from waypoint.llm.models import agent_model
from waypoint.retrieval.hybrid import HybridRetriever
from waypoint.tools.catalog import Catalog
from waypoint.tools.courses import make_course_tools, make_planning_tools
from waypoint.tools.plan import make_plan_tools
from waypoint.tools.policy import make_policy_tools


@dataclass
class Dependencies:
    retriever: HybridRetriever
    catalog: Catalog
    settings: Settings = field(default_factory=get_settings)
    # Command that starts the timetable MCP server; tests can point it elsewhere.
    timetable_command: list[str] = field(default_factory=lambda: [sys.executable, "-m", "waypoint.mcp.timetable"])


def _instruction(text: str) -> Callable[[ReadonlyContext], str]:
    # A provider (not a plain string) so ADK skips {state} templating and we can add today's date.
    def provider(_: ReadonlyContext) -> str:
        return f"{text}\n\n{prompts.today_line()}"

    return provider


def _config(settings: Settings, output_tokens: int | None = None) -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        temperature=0.1,
        max_output_tokens=output_tokens or settings.max_output_tokens,
        thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel(settings.thinking_level.upper())),
    )


def timetable_toolset(deps: Dependencies) -> McpToolset:
    command, *args = deps.timetable_command
    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(command=command, args=args), timeout=20
        )
    )


def build_agents(deps: Dependencies) -> dict[str, LlmAgent]:
    s = deps.settings
    course_tools = make_course_tools(deps.catalog)
    policy_tools = make_policy_tools(deps.retriever)
    plan_tools = make_plan_tools(deps.catalog)

    policy = LlmAgent(
        name="policy_agent",
        description="Answers program-requirement and academic-policy questions from the UTM calendar, with citations.",
        model=agent_model(s),
        instruction=_instruction(prompts.POLICY),
        tools=list(policy_tools),
        generate_content_config=_config(s),
    )
    course_data = LlmAgent(
        name="course_data_agent",
        description="Course facts: prerequisites, exclusions, credits, what a course unlocks, prerequisite checks.",
        model=agent_model(s),
        instruction=_instruction(prompts.COURSE_DATA),
        tools=list(course_tools),
        generate_content_config=_config(s),
    )
    timetable = LlmAgent(
        name="timetable_agent",
        description="Sections and meeting times for a course in a term (sample data in this version).",
        model=agent_model(s),
        instruction=_instruction(prompts.TIMETABLE),
        tools=[timetable_toolset(deps)],
        generate_content_config=_config(s),
    )
    # Lean toolset on purpose: one overview call + validation beats a dozen single-course lookups.
    get_course = next(t for t in course_tools if t.__name__ == "get_course")
    planner_tools: list[Any] = [*make_planning_tools(deps.catalog), *plan_tools, get_course, *policy_tools]
    planner = LlmAgent(
        name="planner_agent",
        description="Builds and validates a term-by-term course plan toward a program.",
        model=agent_model(s),
        instruction=_instruction(prompts.PLANNER),
        planner=PlanReActPlanner(),
        tools=planner_tools,
        generate_content_config=_config(s, s.max_output_tokens * 2),
    )
    orchestrator = LlmAgent(
        name="waypoint",
        description="Waypoint orchestrator: routes questions to specialists and composes a cited answer.",
        model=agent_model(s),
        instruction=_instruction(prompts.ORCHESTRATOR),
        tools=[AgentTool(policy), AgentTool(course_data), AgentTool(timetable), AgentTool(planner)],
        generate_content_config=_config(s),
    )
    return {
        "waypoint": orchestrator,
        "policy_agent": policy,
        "course_data_agent": course_data,
        "timetable_agent": timetable,
        "planner_agent": planner,
    }


def build_root_agent(deps: Dependencies) -> LlmAgent:
    return build_agents(deps)["waypoint"]
