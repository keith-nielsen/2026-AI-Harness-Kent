"""Gent tools for CrewAI workers. All network access goes through the egress
proxy (HTTP(S)_PROXY -> Squid on the Kent bridge; the container network itself is
internal-only). File and command tools are confined to /data/workspace."""
from __future__ import annotations

import html
import os
import re
import subprocess
from pathlib import Path

import requests
from crewai.tools import tool

WORKSPACE = Path(os.environ.get("GENT_WORKSPACE", "/data/workspace"))
TIMEOUT = 30
UA = "Kent-Gent/1.0 (research agent)"


def _in_workspace(path: str) -> Path:
    p = (WORKSPACE / path.lstrip("/").removeprefix("data/workspace/")).resolve()
    if p != WORKSPACE and WORKSPACE not in p.parents:
        raise ValueError("path must be inside /data/workspace")
    return p


def _text(html_src: str, limit: int = 6000) -> str:
    t = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", html_src)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", html.unescape(t)).strip()
    return t[:limit] + (" [truncated]" if len(t) > limit else "")


@tool("Web Search")
def web_search(query: str) -> str:
    """Search the web. Returns up to 8 results as title, URL and snippet."""
    try:
        r = requests.get("https://html.duckduckgo.com/html/", params={"q": query},
                         headers={"User-Agent": UA}, timeout=TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as e:
        return f"search failed: {e}"
    results = []
    for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>',
                         r.text, re.S):
        url, title, snip = m.group(1), _text(m.group(2), 200), _text(m.group(3), 400)
        results.append(f"- {title}\n  {url}\n  {snip}")
        if len(results) == 8:
            break
    return "\n".join(results) or f"no results for: {query}"


@tool("Read Web Page")
def read_web_page(url: str) -> str:
    """Fetch a public web page (GET only) and return its readable text."""
    if not url.startswith(("https://", "http://")):
        return "only http(s) URLs are allowed"
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as e:
        return f"fetch failed: {e}"
    return _text(r.text) if "html" in r.headers.get("content-type", "") else r.text[:12000]


@tool("Read File")
def read_file(path: str) -> str:
    """Read a text file from the project workspace (/data/workspace)."""
    try:
        return _in_workspace(path).read_text()[:50000]
    except (OSError, ValueError) as e:
        return f"error: {e}"


@tool("Write File")
def write_file(path: str, content: str) -> str:
    """Write a text file into the project workspace (/data/workspace). Creates folders.
    Files starting with a #! line are made executable."""
    try:
        p = _in_workspace(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        if content.startswith("#!"):
            p.chmod(0o750)
        return f"wrote {len(content)} chars to {p.relative_to(WORKSPACE)}"
    except (OSError, ValueError) as e:
        return f"error: {e}"


@tool("List Files")
def list_files(subdir: str = "") -> str:
    """List files in the project workspace."""
    try:
        base = _in_workspace(subdir or ".")
        return "\n".join(str(p.relative_to(WORKSPACE)) for p in sorted(base.rglob("*")) if p.is_file())[:8000] or "(empty)"
    except (OSError, ValueError) as e:
        return f"error: {e}"


@tool("Run Script")
def run_script(path: str, args: str = "") -> str:
    """Run a Python (.py) or shell (.sh) script from the workspace with a 120s timeout.
    Network access only through the egress proxy. Returns exit code and output."""
    try:
        p = _in_workspace(path)
    except ValueError as e:
        return f"error: {e}"
    if not p.is_file() or p.suffix not in (".py", ".sh"):
        return "error: only existing .py or .sh files in the workspace can be run"
    cmd = ["python3", str(p)] if p.suffix == ".py" else ["bash", str(p)]
    try:
        r = subprocess.run(cmd + args.split(), cwd=WORKSPACE, capture_output=True, text=True, timeout=120)
        return f"exit={r.returncode}\n--- stdout ---\n{r.stdout[-3000:]}\n--- stderr ---\n{r.stderr[-2000:]}"
    except subprocess.TimeoutExpired:
        return "error: timed out after 120s"


def all_tools() -> list:
    return [web_search, read_web_page, read_file, write_file, list_files, run_script]
