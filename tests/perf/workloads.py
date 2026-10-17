"""Shared Kent-shaped workloads for spec_bench.py and mtp_probe.py (fixed text, no randomness)."""
REPO = "/home/administrator/Documents/repo/harness-kent"
SRC = open(f"{REPO}/install/services/llama/bin/kent_llama_launch.py").read()
LOG = "\n".join(
    f"2026-10-0{1 + i % 3}T{8 + i % 10:02d}:{i * 7 % 60:02d}:{i * 13 % 60:02d}Z kent-gent[{3100 + i}] "
    + ("task t%02d finished in %d s, verdict PASS" % (i, 40 + i * 3) if i % 5
       else "task t%02d FAILED: validator rejected output (missing column 'date')" % i)
    for i in range(60))

WORKLOADS = {
    "edit": ([{"role": "user", "content":
               "Here is a Python file:\n\n```python\n" + SRC + "```\n\n"
               "Add a setting SPEC (environment, default \"none\") that is passed to llama-server as "
               "`--spec-type` instead of the fixed value \"none\", and mention it in the docstring's Settings "
               "line. Return the complete updated file in one code block and nothing else."}], 3000),
    "agentic": ([{"role": "system", "content": "You are Kent, an agent on a Linux host. Be exact."},
                 {"role": "user", "content": "Check today's Gent task log and write the report."},
                 {"role": "assistant", "content": "Running: journalctl -u kent-gent --since -3d"},
                 {"role": "user", "content": "Tool output:\n" + LOG + "\n\nWrite report.md: a table of every "
                  "FAILED task (task id, timestamp, the full log line), then one paragraph on the pattern."}],
                1500),
    "prose": ([{"role": "user", "content": "In about 350 words, explain how a mixture-of-experts language "
                "model decides which experts handle each token, and why that matters for running it on a PC "
                "with a small GPU."}], 700),
}
