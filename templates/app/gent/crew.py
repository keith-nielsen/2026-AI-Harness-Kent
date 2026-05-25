"""
Kent — Gent Crew Factory

Builds CrewAI Crew instances with LLM bindings resolved at runtime
based on the Router's tier decision. agents.yaml defines ROLES;
this module binds them to the correct gateway tier.

Key design:
- agents.yaml has no LLM config — roles only
- LLM binding happens here, per-task, based on routing
- manager_llm is always smart (hierarchical process coordination)
- Workers get the tier the router decided (fast or smart)
"""

import os
from pathlib import Path
from typing import Optional

import yaml
from crewai import Agent, Crew, LLM, Task

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://172.30.0.1:4000")
CONFIG_DIR = Path(__file__).parent.parent / "config"


def _make_llm(tier: str, api_key: str) -> LLM:
    """Create a CrewAI LLM pointing at our gateway with the given tier."""
    return LLM(
        model=tier,
        base_url=GATEWAY_URL,
        api_key=api_key,
    )


def _load_yaml(filename: str) -> dict:
    # Check /data/ first (project-specific overrides at spawn time),
    # then fall back to /app/config/ (baked-in defaults).
    # This lets any Gent be customised by dropping config files
    # into /data/ — no compose modifications or image rebuilds needed.
    data_path = Path("/data") / filename
    config_path = CONFIG_DIR / filename

    if data_path.exists():
        path = data_path
    elif config_path.exists():
        path = config_path
    else:
        raise FileNotFoundError(
            f"Config not found: tried /data/{filename} and {config_path}"
        )

    with open(path) as f:
        return yaml.safe_load(f)


def build_agents(
    tier: str,
    api_key: str,
    role_filter: Optional[list[str]] = None,
    tools: Optional[list] = None,
) -> list[Agent]:
    """Build Agent instances from agents.yaml, bound to the given tier.

    Args:
        tier: "fast" or "smart" — determines which gateway model agents use.
        api_key: The Gent's scoped gateway API key.
        role_filter: If provided, only build agents whose key is in this list.
        tools: Optional list of tool instances to attach to all agents.

    Returns:
        List of CrewAI Agent instances ready for crew assembly.
    """
    agent_configs = _load_yaml("agents.yaml")
    llm = _make_llm(tier, api_key)
    agents = []

    for role_key, config in agent_configs.items():
        if role_filter and role_key not in role_filter:
            continue

        agent = Agent(
            role=config["role"],
            goal=config["goal"],
            backstory=config.get("backstory", ""),
            llm=llm,
            tools=tools or [],
            verbose=config.get("verbose", False),
            allow_delegation=config.get("allow_delegation", False),
            memory=config.get("memory", True),
        )
        agents.append(agent)

    return agents


def build_tasks(task_descriptions: list[dict], agents: list[Agent]) -> list[Task]:
    """Build Task instances from a list of task descriptions.

    Each task_description dict should have:
        - description: str (the task text)
        - agent_index: int (index into the agents list)
        - expected_output: str (optional, what good output looks like)

    Falls back to tasks.yaml defaults if task_descriptions is empty.
    """
    if not task_descriptions:
        defaults = _load_yaml("tasks.yaml")
        task_descriptions = [
            {
                "description": t.get("description", ""),
                "expected_output": t.get("expected_output", ""),
                "agent_index": i % len(agents),
            }
            for i, t in enumerate(defaults.values())
        ]

    tasks = []
    for td in task_descriptions:
        idx = td.get("agent_index", 0)
        agent = agents[min(idx, len(agents) - 1)]
        tasks.append(
            Task(
                description=td["description"],
                expected_output=td.get("expected_output", "A complete response."),
                agent=agent,
            )
        )

    return tasks


def build_crew(
    tier: str,
    api_key: str,
    task_descriptions: list[dict],
    process: str = "sequential",
    role_filter: Optional[list[str]] = None,
    tools: Optional[list] = None,
) -> Crew:
    """Build a complete Crew with tier-appropriate LLM bindings.

    Args:
        tier: "fast" or "smart".
        api_key: Gent's scoped gateway key.
        task_descriptions: List of task dicts (see build_tasks).
        process: "sequential" or "hierarchical".
        role_filter: Subset of agent roles to activate.
        tools: Custom tool instances for workers.

    Returns:
        A Crew ready to kickoff().
    """
    agents = build_agents(tier, api_key, role_filter=role_filter, tools=tools)

    if not agents:
        raise ValueError("No agents built — check agents.yaml and role_filter.")

    tasks = build_tasks(task_descriptions, agents)

    crew_kwargs = {
        "agents": agents,
        "tasks": tasks,
        "process": process,
        "verbose": True,
    }

    # Hierarchical process needs an explicit manager LLM.
    # Manager always uses smart — it's doing coordination/planning.
    if process == "hierarchical":
        crew_kwargs["manager_llm"] = _make_llm("smart", api_key)

    return Crew(**crew_kwargs)
