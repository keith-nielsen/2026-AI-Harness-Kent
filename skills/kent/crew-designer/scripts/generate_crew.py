#!/usr/bin/env python3
"""
Kent — Crew Configuration Generator

Takes a template name and a JSON customisation blob, produces deployment-ready
agents.yaml + tasks.yaml + crew_config.json in the output directory.

The customisations JSON replaces {placeholder} tokens in the template YAML
and can override agent-level fields (goal, backstory, tier).

Usage:
    python3 generate_crew.py \
        --template research \
        --output-dir /tmp/kent-crew-staging \
        --project-name "Competitor pricing analysis" \
        --customisations '{"topic": "SaaS competitor pricing", ...}'

Exit codes:
    0 — success
    1 — validation failure (bad YAML, missing template, placeholder remnants)
    2 — argument error
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = SCRIPT_DIR.parent / "templates"

PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
UNFILLED_RE = re.compile(r"\{(\w+)\}")


def load_template(template_name: str) -> tuple[dict, dict]:
    """Load agents.yaml and tasks.yaml from a named template directory."""
    tpl_dir = TEMPLATE_DIR / template_name
    if not tpl_dir.is_dir():
        available = [d.name for d in TEMPLATE_DIR.iterdir() if d.is_dir()]
        print(f"ERROR: Template '{template_name}' not found.", file=sys.stderr)
        print(f"  Available: {', '.join(sorted(available))}", file=sys.stderr)
        sys.exit(1)

    agents_path = tpl_dir / "agents.yaml"
    tasks_path = tpl_dir / "tasks.yaml"

    if not agents_path.exists() or not tasks_path.exists():
        print(f"ERROR: Template '{template_name}' missing agents.yaml or tasks.yaml", file=sys.stderr)
        sys.exit(1)

    with open(agents_path) as f:
        agents = yaml.safe_load(f)
    with open(tasks_path) as f:
        tasks = yaml.safe_load(f)

    return agents, tasks


def fill_placeholders(text: str, params: dict) -> str:
    """Replace {key} placeholders with values from params dict.

    Unmatched placeholders are left as-is (caught by validation).
    """
    def replacer(match):
        key = match.group(1)
        if key in params:
            return str(params[key])
        return match.group(0)  # leave unfilled

    return PLACEHOLDER_RE.sub(replacer, text)


def apply_customisations(agents: dict, tasks: dict, customisations: dict) -> tuple[dict, dict]:
    """Apply customisations to template agents and tasks.

    Customisation keys:
        - Any key matching a {placeholder} in task descriptions/outputs
        - agent_overrides: dict of {agent_key: {field: value}} for agent-level changes
        - task_overrides: dict of {task_key: {field: value}} for task-level changes
        - remove_agents: list of agent keys to remove
        - remove_tasks: list of task keys to remove
    """
    # Extract structured overrides before using the rest as placeholder params
    agent_overrides = customisations.pop("agent_overrides", {})
    task_overrides = customisations.pop("task_overrides", {})
    remove_agents = customisations.pop("remove_agents", [])
    remove_tasks = customisations.pop("remove_tasks", [])
    params = customisations  # remaining keys are placeholder values

    # Remove agents/tasks if requested
    for key in remove_agents:
        agents.pop(key, None)
    for key in remove_tasks:
        tasks.pop(key, None)

    # Apply agent overrides
    for agent_key, overrides in agent_overrides.items():
        if agent_key in agents:
            agents[agent_key].update(overrides)

    # Apply task overrides
    for task_key, overrides in task_overrides.items():
        if task_key in tasks:
            tasks[task_key].update(overrides)

    # Fill placeholders in all string fields of tasks
    for task_key, task_def in tasks.items():
        for field in ("description", "expected_output"):
            if field in task_def and isinstance(task_def[field], str):
                task_def[field] = fill_placeholders(task_def[field], params)

    # Fill placeholders in agent goals/backstories (less common but supported)
    for agent_key, agent_def in agents.items():
        for field in ("goal", "backstory"):
            if field in agent_def and isinstance(agent_def[field], str):
                agent_def[field] = fill_placeholders(agent_def[field], params)

    return agents, tasks


def strip_non_crewai_fields(agents: dict) -> dict:
    """Remove Kent-specific fields (tier) that aren't part of CrewAI's schema.

    Returns a separate tier map for crew_config.json.
    """
    tier_map = {}
    for agent_key, agent_def in agents.items():
        tier_map[agent_key] = agent_def.pop("tier", "smart")
    return tier_map


def validate_output(agents: dict, tasks: dict) -> list[str]:
    """Validate the generated config. Returns list of error strings."""
    errors = []

    if not agents:
        errors.append("No agents defined")
    if not tasks:
        errors.append("No tasks defined")

    # Check for unfilled placeholders
    for task_key, task_def in tasks.items():
        for field in ("description", "expected_output"):
            val = task_def.get(field, "")
            if isinstance(val, str):
                unfilled = UNFILLED_RE.findall(val)
                # Filter out known non-placeholder patterns
                real_unfilled = [u for u in unfilled if u not in ("type", "json_object")]
                if real_unfilled:
                    errors.append(
                        f"Task '{task_key}'.{field} has unfilled placeholders: "
                        f"{', '.join('{' + u + '}' for u in real_unfilled)}"
                    )

    # Check agent references in tasks
    agent_keys = set(agents.keys())
    for task_key, task_def in tasks.items():
        agent_ref = task_def.get("agent", "")
        if agent_ref and agent_ref not in agent_keys:
            errors.append(
                f"Task '{task_key}' references agent '{agent_ref}' "
                f"which is not defined. Available: {', '.join(sorted(agent_keys))}"
            )

        # Check context references
        task_keys = set(tasks.keys())
        for ctx_ref in task_def.get("context", []):
            if ctx_ref not in task_keys:
                errors.append(
                    f"Task '{task_key}' context references '{ctx_ref}' "
                    f"which is not a defined task."
                )

    # Check required agent fields
    for agent_key, agent_def in agents.items():
        for required in ("role", "goal"):
            if not agent_def.get(required):
                errors.append(f"Agent '{agent_key}' missing required field: {required}")

    return errors


def write_output(
    output_dir: Path,
    agents: dict,
    tasks: dict,
    config: dict,
):
    """Write agents.yaml, tasks.yaml, and crew_config.json to the output dir."""
    output_dir.mkdir(parents=True, exist_ok=True)

    agents_path = output_dir / "agents.yaml"
    tasks_path = output_dir / "tasks.yaml"
    config_path = output_dir / "crew_config.json"

    # Write agents with a header comment
    with open(agents_path, "w") as f:
        f.write(f"# Generated by Kent crew-designer — {config['generated_at']}\n")
        f.write(f"# Template: {config['template']} | Project: {config['project_name']}\n")
        yaml.dump(agents, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    with open(tasks_path, "w") as f:
        f.write(f"# Generated by Kent crew-designer — {config['generated_at']}\n")
        f.write(f"# Template: {config['template']} | Project: {config['project_name']}\n")
        yaml.dump(tasks, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    return agents_path, tasks_path, config_path


def main():
    parser = argparse.ArgumentParser(description="Generate CrewAI crew configuration from template")
    parser.add_argument("--template", required=True, help="Template name (research, content, analysis)")
    parser.add_argument("--output-dir", required=True, help="Directory to write generated configs")
    parser.add_argument("--project-name", required=True, help="Human-readable project name")
    parser.add_argument("--customisations", default="{}", help="JSON string of customisation parameters")
    parser.add_argument("--process", default=None, help="Override process type (sequential or hierarchical)")
    parser.add_argument("--dry-run", action="store_true", help="Validate only, don't write files")

    args = parser.parse_args()

    # Parse customisations
    try:
        customisations = json.loads(args.customisations)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in --customisations: {e}", file=sys.stderr)
        sys.exit(2)

    # Load template
    agents, tasks = load_template(args.template)

    # Determine process type
    # Default process types per template
    default_processes = {
        "research": "sequential",
        "content": "sequential",
        "analysis": "hierarchical",
    }
    process = args.process or customisations.pop("process", None) or default_processes.get(args.template, "sequential")

    # Apply customisations
    agents, tasks = apply_customisations(agents, tasks, customisations)

    # Extract tier map before stripping non-CrewAI fields
    tier_map = strip_non_crewai_fields(agents)

    # Validate
    errors = validate_output(agents, tasks)
    if errors:
        print("VALIDATION ERRORS:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        if not args.dry_run:
            # Still write on warnings (unfilled placeholders), fail on structural errors
            structural = [e for e in errors if "missing required" in e or "not defined" in e or "No agents" in e or "No tasks" in e]
            if structural:
                sys.exit(1)
            print("WARNING: Non-structural issues found. Writing anyway.", file=sys.stderr)

    if args.dry_run:
        print("DRY RUN — validation complete.")
        print(f"  Template: {args.template}")
        print(f"  Process: {process}")
        print(f"  Agents: {', '.join(agents.keys())}")
        print(f"  Tasks: {', '.join(tasks.keys())}")
        print(f"  Tier map: {json.dumps(tier_map)}")
        if errors:
            print(f"  Warnings: {len(errors)}")
        sys.exit(0 if not errors else 1)

    # Build config metadata
    config = {
        "project_name": args.project_name,
        "template": args.template,
        "process": process,
        "tier_map": tier_map,
        "agent_count": len(agents),
        "task_count": len(tasks),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generator": "kent-crew-designer-v1.0.0",
    }

    # Write
    output_dir = Path(args.output_dir)
    agents_path, tasks_path, config_path = write_output(output_dir, agents, tasks, config)

    print(f"Crew configuration generated:")
    print(f"  {agents_path}")
    print(f"  {tasks_path}")
    print(f"  {config_path}")
    print(f"  Process: {process}")
    print(f"  Agents: {len(agents)} | Tasks: {len(tasks)}")


if __name__ == "__main__":
    main()
