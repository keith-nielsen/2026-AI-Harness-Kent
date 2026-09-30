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

4. **Order the tasks**: the Gent CEO runs tasks one at a time in `tasks.yaml` order,
   and each task sees the results of the earlier ones. Put dependencies first.

5. **Plan for the local model**: every Gent worker runs on the local `fast` tier; Gents
   cannot use smart or frontier. Keep each task small and concrete, with one deliverable
   named in `output:` (see "Spawning"). Where a task needs expert judgement a small model is likely to get wrong
   (licensing, security-critical choices, novel design), mark it `escalate: true` with a
   precise `question`: you (Kent) will answer it on frontier before the team starts.

### Phase 3: Design Review

Present the design to the operator as a readable summary. Use this format:

```
PROJECT: <one-line description>
TEMPLATE: <template_name> (customised)
PROCESS: <sequential|hierarchical>

AGENTS:
  1. <Role> — <Goal summary>
  2. <Role> — <Goal summary>
  ...

TASK PIPELINE:
  T1: <Task summary> → assigned to Agent 1
      Expected output: <what good looks like>
  T2: <Task summary> → assigned to Agent 2
      Depends on: T1 output
      Expected output: <what good looks like>
  ...

ESCALATIONS: <tasks marked escalate: true, with the question> (frontier, via Kent)
EFFORT: <limits: max_iter / max_tokens> (keep small on local hardware)
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
     --goal "<the deliverable and who uses it>" \
     --customisations '<JSON string of customisations>'
   ```

2. The script produces:
   - `project.yaml` — name, goal and effort limits (required by `kent-gent spawn`)
   - `agents.yaml` — customised agent definitions
   - `tasks.yaml` — customised task definitions
   - `crew_config.json` — metadata (template, process type, timestamp)

3. Show the operator the generated file paths and offer to display the YAML for final review.

4. If the operator approves, spawn it: `kent-gent spawn --name "<name>" --project <staging_dir>`,
   then report the stack id (see "Spawning" below).

## Template Details

### Research Template
Best for fact-finding, source validation, and structured summaries.

- **Researcher**: Finds and cross-references information from available sources
- **Analyst**: Evaluates findings for patterns, contradictions, and confidence levels
- **Reviewer**: Validates the final output for accuracy and completeness

Process: sequential (Researcher → Analyst → Reviewer)
All workers run on the local fast tier (Gents are local-only)

### Content Template
Best for producing written deliverables: reports, articles, briefs.

- **Researcher**: Gathers background information and source material
- **Writer**: Produces the draft deliverable adapted to the target audience
- **Editor**: Reviews for clarity, accuracy, tone, and completeness

Process: sequential (Researcher → Writer → Editor)
All workers run on the local fast tier; escalate genuinely hard writing decisions

### Analysis Template
Best for data-driven insights from structured or semi-structured data.

- **Collector**: Gathers and normalises data from specified sources
- **Analyst**: Processes data, identifies patterns, runs comparisons
- **Writer**: Produces the analysis report with methodology and findings
- **Reviewer**: Validates methodology, checks calculations, flags limitations

Process: hierarchical (Analyst manages, delegates to Collector and Writer, Reviewer validates)
All workers run on the local fast tier. The Gent CEO runs tasks sequentially, so list them in order

## Pitfalls

- **Over-engineering**: Resist the urge to add agents. Start with the template's default count. The operator can always iterate.
- **Vague goals**: If the operator says "research AI", push back. Get a specific deliverable: "a 2-page summary of transformer architecture advances in 2025 with source citations."
- **Missing inputs**: A crew with no input data will hallucinate. Confirm what sources are available before designing.
- **Frontier assumptions**: Gents never call frontier. Use `escalate: true` sparingly (budget: 5 per Gent per day); each one is a frontier call made by you.
- **Oversized tasks**: the local model is slow. Several small tasks beat one big one.
- **Premature YAML**: Don't dump YAML at the operator. Present the human-readable design first. YAML is the output artifact, not the communication medium.

## Verification

After generation, verify:
1. All generated YAML files parse without errors (the generation script validates internally)
2. Agent count matches the approved design
3. Task count matches the approved pipeline
4. Process type matches the approved design
5. No placeholder text remains (no `<fill in>` or `TODO` strings)


## Spawning (kent-gent)

After the operator approves the design, write three files into a new directory
(e.g. `/tmp/kent-crew-staging/<short-name>/`) and spawn:

`project.yaml`
```yaml
name: "Short project name"
goal: "One or two sentences: the deliverable and who uses it"
```

`project.yaml` may add `limits: {max_iter: 6, max_tokens: 4000}` (the defaults; keep tasks small — the local model is slow).

`agents.yaml` — one entry per worker (key -> role, goal, backstory). Keep it to 2-4 agents.

`tasks.yaml` — ordered; each task runs as its own crew step and sees earlier results:
```yaml
research:
  agent: researcher                 # key from agents.yaml
  description: "What to do, concretely. Name the files to write in /data/workspace."
  expected_output: "What 'done' looks like"
build:
  agent: developer
  description: "..."
  expected_output: "..."
  escalate: true                    # optional: ask Kent (frontier) before starting
  question: "The precise expert question"   # optional, used with escalate
write:
  agent: writer
  description: "..."
  expected_output: "..."
  output: paper.md                  # the task's one deliverable, relative to /data/workspace
  max_tokens: 6000                  # optional: longer replies for this task (cap 16000)
review:
  agent: reviewer
  description: "Check each deliverable against its task. One line per file; last line: Overall: PASS or Overall: FAIL."
  expected_output: "review.md ending with Overall: PASS or Overall: FAIL"
  output: review.md
  verdict: true                     # its overall PASS/FAIL is reported to Kent
```

Rules that make tasks succeed on the local model:
- **One deliverable per task, named in `output:`.** Small models often put the deliverable in
  their final answer instead of calling Write File; with `output:` the Gent saves that answer
  as the file, so the task does not fail on a missing file.
- **Ask for terse content**: 2-3 sentences per finding or section item, not paragraphs; a long
  answer is cut off at `max_tokens`.
- **End with a review task marked `verdict: true`** whose deliverable's last line is `Overall: PASS`
  or `Overall: FAIL`. The Gent reports that one word to Kent (never the review's text), and it
  appears in the operator's notice next to Kent's own frontier review.
- **Raise `max_tokens` only for writing tasks** whose deliverable is long (a ~1500-word paper
  needs about 3000 tokens plus headroom: 6000).

Then run: `kent-gent spawn --name "<name>" --project <dir>` and report the stack id.
Follow progress with `kent-gent status <id>`; when it is complete run
`kent-gent assess <id>` and summarise the assessment for the operator.
