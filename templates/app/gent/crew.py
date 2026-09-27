"""Build a CrewAI crew for one kanban task. Agents come from the project's
agents.yaml; every agent uses the gateway's local "fast" tier through CrewAI's
native OpenAI client (no LiteLLM package in the image)."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from crewai import LLM, Agent, Crew, Process, Task

from gent.tools import all_tools

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://172.30.0.1:4000/v1")
PROJECT = Path(os.environ.get("GENT_PROJECT", "/data/project"))


def limits() -> dict:
    """Per-project effort caps (project.yaml `limits:`). Small defaults keep a Gent's
    local-model time short; raise them per project when the work needs it."""
    lim = (load("project.yaml").get("limits") or {}) if (PROJECT / "project.yaml").exists() else {}
    return {"max_iter": int(lim.get("max_iter", 6)), "max_tokens": int(lim.get("max_tokens", 1500))}


def llm(api_key: str, tier: str = "fast") -> LLM:
    return LLM(model=tier, provider="openai", base_url=GATEWAY_URL, api_key=api_key,
               temperature=0.3, timeout=900, max_tokens=limits()["max_tokens"])


def load(name: str) -> dict:
    return yaml.safe_load((PROJECT / name).read_text()) or {}


def team_knowledge() -> str:
    """Learnings inherited from the stack template (written by Kent at spawn)."""
    p = PROJECT / "LEARNINGS.md"
    return p.read_text()[:4000] if p.exists() else ""


def build_crew(api_key: str, agent_key: str, description: str, expected_output: str, on_step=None) -> Crew:
    agents = load("agents.yaml")
    spec = agents.get(agent_key) or next(iter(agents.values()))
    knowledge = team_knowledge()
    backstory = spec.get("backstory", "")
    if knowledge:
        backstory += "\n\nTeam knowledge from earlier projects (apply where relevant):\n" + knowledge
    agent = Agent(role=spec["role"], goal=spec["goal"], backstory=backstory, llm=llm(api_key),
                  tools=all_tools(), allow_delegation=False, verbose=True, max_iter=limits()["max_iter"])
    task = Task(description=description, expected_output=expected_output or "A complete result.", agent=agent)
    return Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=True, step_callback=on_step)
