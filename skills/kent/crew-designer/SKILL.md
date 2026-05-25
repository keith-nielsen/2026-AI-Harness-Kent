---
name: crew-designer
description: >
  Design CrewAI crew configurations for Gent stacks. Use when the operator
  asks to set up a new project, design a crew, create agents, plan a research
  task, build a content pipeline, or run an analysis workflow. Covers template
  selection, parameter filling, crew review, and YAML generation for spawn.
version: 1.0.0
author: Keith Nielsen / KeyTurning Insights
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Kent, CrewAI, Crew Design, Agents, Gent, Orchestration]
    related_skills: []
    config:
      - key: crew_designer.template_dir
        description: "Path to crew templates (agents.yaml + tasks.yaml pairs)"
        default: "${HERMES_SKILL_DIR}/templates"
        prompt: "Crew template directory"
      - key: crew_designer.output_dir
        description: "Where to write generated crew configs before spawn"
        default: "/tmp/kent-crew-staging"
        prompt: "Crew config staging directory"
---

# Crew Designer

Design and generate CrewAI crew configurations for Gent project stacks.

## When to Use

Activate when the operator:
- Asks to "set up a new project" or "design a crew"
- Describes a task that needs multiple agents (research, writing, analysis)
- Says "I need a crew to..." or "create a Gent for..."
- Asks to "spawn a Gent" for a specific purpose
- Wants to review or modify an existing crew template

Do NOT activate for:
- Monitoring existing Gents (use estate monitoring)
- Single-turn questions Kent can answer directly
- Tasks that don't need a multi-agent crew

## Quick Reference

| Template   | Agents                              | Process      | Best For                                      |
|------------|-------------------------------------|--------------|-----------------------------------------------|
| research   | Researcher, Analyst, Reviewer       | sequential   | Fact-finding, source synthesis, gap analysis   |
| content    | Researcher, Writer, Editor          | sequential   | Reports, articles, briefs, documentation       |
| analysis   | Collector, Analyst, Writer, Reviewer| hierarchical | Data-driven insights, competitive analysis     |

## Procedure

Follow this four-phase flow. Do NOT skip phases or generate YAML before the operator approves the design.

### Phase 1: Intent Capture

Ask the operator these questions conversationally (not as a checklist):

1. **Goal**: What is the specific deliverable? (e.g. "a competitive pricing report")
2. **Inputs**: What data or sources are available? (URLs, files in /data/, databases)
3. **Output format**: What should the final output look like? (report, spreadsheet, summary, presentation)
4. **Quality bar**: Quick scan or deep analysis? (affects agent count and review gates)
5. **Constraints**: Time limits, cost limits, no internet access, specific model tier?

After gathering answers, summarise your understanding back to the operator in 2-3 sentences and ask for confirmation before proceeding.

### Phase 2: Template Selection and Customisation

Select the closest matching template from the table above. Then customise:

1. **Load the template** by reading the agents.yaml and tasks.yaml from:
   ```
   ${HERMES_SKILL_DIR}/templates/<template_name>/agents.yaml
   ${HERMES_SKILL_DIR}/templates/<template_name>/tasks.yaml
   ```

2. **Customise agent goals and backstories** to match the operator's specific domain and deliverable. Keep role names generic (Researcher, Analyst, Writer) but make goals specific.

3. **Customise task descriptions** with the operator's actual inputs, sources, and success criteria. Fill in concrete details, not placeholders.

4. **Select process type**:
   - `sequential` for pipelines where each agent builds on the previous output (most common, 2-3 agents)
   - `hierarchical` for complex tasks where a manager delegates subtasks (4+ agents, higher token cost)

5. **Assign model tiers**:
   - Workers default to `fast` for the research template, `smart` for content and analysis
   - Manager (hierarchical only) always uses `smart`
   - Reviewer agents use `smart` regardless of template
   - If the operator says "use frontier" or the task clearly exceeds local capability, note this as requiring escalation

### Phase 3: Design Review

Present the design to the operator as a readable summary. Use this format:

```
PROJECT: <one-line description>
TEMPLATE: <template_name> (customised)
PROCESS: <sequential|hierarchical>

AGENTS:
  1. <Role> — <Goal summary> [tier: fast|smart]
  2. <Role> — <Goal summary> [tier: fast|smart]
  ...

TASK PIPELINE:
  T1: <Task summary> → assigned to Agent 1
      Expected output: <what good looks like>
  T2: <Task summary> → assigned to Agent 2
      Depends on: T1 output
      Expected output: <what good looks like>
  ...

ESTIMATED COST: <low|medium|high> (based on agent count and tiers)
```

Do NOT show raw YAML at this stage. The operator should approve the design before seeing config files.

Wait for one of:
- **Approval**: "looks good", "go ahead", "approved" → proceed to Phase 4
- **Changes**: Iterate on the design, then re-present
- **Reject**: Start over from Phase 1

### Phase 4: Generate Configuration

On approval, generate the crew configuration files:

1. Run the generation script:
   ```
   python3 ${HERMES_SKILL_DIR}/scripts/generate_crew.py \
     --template <template_name> \
     --output-dir <staging_dir> \
     --project-name "<operator's project name>" \
     --customisations '<JSON string of customisations>'
   ```

2. The script produces:
   - `agents.yaml` — customised agent definitions
   - `tasks.yaml` — customised task definitions
   - `crew_config.json` — metadata (template, process type, tier assignments, timestamp)

3. Show the operator the generated file paths and offer to display the YAML for final review.

4. If the operator approves for spawn, report that the configs are staged and ready. The actual spawn is a separate operation (spawn-gent script reads from the staging directory).

## Template Details

### Research Template
Best for fact-finding, source validation, and structured summaries.

- **Researcher**: Finds and cross-references information from available sources
- **Analyst**: Evaluates findings for patterns, contradictions, and confidence levels
- **Reviewer**: Validates the final output for accuracy and completeness

Process: sequential (Researcher → Analyst → Reviewer)
Default tier: fast for Researcher, smart for Analyst and Reviewer

### Content Template
Best for producing written deliverables: reports, articles, briefs.

- **Researcher**: Gathers background information and source material
- **Writer**: Produces the draft deliverable adapted to the target audience
- **Editor**: Reviews for clarity, accuracy, tone, and completeness

Process: sequential (Researcher → Writer → Editor)
Default tier: smart for all agents (writing quality matters)

### Analysis Template
Best for data-driven insights from structured or semi-structured data.

- **Collector**: Gathers and normalises data from specified sources
- **Analyst**: Processes data, identifies patterns, runs comparisons
- **Writer**: Produces the analysis report with methodology and findings
- **Reviewer**: Validates methodology, checks calculations, flags limitations

Process: hierarchical (Analyst manages, delegates to Collector and Writer, Reviewer validates)
Default tier: fast for Collector, smart for Analyst/Writer/Reviewer

## Pitfalls

- **Over-engineering**: Resist the urge to add agents. Start with the template's default count. The operator can always iterate.
- **Vague goals**: If the operator says "research AI", push back. Get a specific deliverable: "a 2-page summary of transformer architecture advances in 2025 with source citations."
- **Missing inputs**: A crew with no input data will hallucinate. Confirm what sources are available before designing.
- **Frontier assumptions**: Never default to frontier tier. It costs real money and requires escalation. Only flag it if the operator explicitly requests it or the task clearly exceeds smart-tier capability.
- **Premature YAML**: Don't dump YAML at the operator. Present the human-readable design first. YAML is the output artifact, not the communication medium.

## Verification

After generation, verify:
1. All generated YAML files parse without errors (the generation script validates internally)
2. Agent count matches the approved design
3. Task count matches the approved pipeline
4. Process type matches the approved design
5. No placeholder text remains (no `<fill in>` or `TODO` strings)
