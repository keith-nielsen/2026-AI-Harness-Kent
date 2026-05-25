"""
Kent — Gent Custom Tools

Proxy-aware tool wrappers for CrewAI workers. These replace CrewAI's
bundled tools to ensure all external access flows through our egress
proxy and is visible in telemetry.

All HTTP requests use the container's HTTP_PROXY/HTTPS_PROXY env vars
(set in docker-compose.gent.yml → Squid on 172.30.0.1:3128).

Tools are CrewAI-compatible: decorated with @tool or subclassing BaseTool.
"""

import os
from typing import Optional

import requests
import urllib3
from crewai.tools import tool

# Suppress SSL warnings through Squid CONNECT tunnel
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Proxy config is inherited from container environment.
# requests respects HTTP_PROXY/HTTPS_PROXY automatically.
REQUEST_TIMEOUT = 30


@tool("Web Search")
def web_search(query: str) -> str:
    """Search the web for information. Returns ranked results with titles,
    URLs, and snippets.

    Use this when you need current information, facts, or data that
    may not be in your training data.
    """
    import re as _re
    import time as _time
    # Retry once on transient SSL/proxy errors
    for attempt in range(2):
        try:
            resp = requests.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                timeout=REQUEST_TIMEOUT,
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"},
                verify=False,
            )
            resp.raise_for_status()
            break  # success
        except requests.exceptions.SSLError:
            if attempt == 0:
                _time.sleep(1)
                continue
            return "Search failed: SSL handshake error (intermittent proxy tunnel issue)"

    html = resp.text

    # Split on individual result blocks
    blocks = _re.findall(
        r'<div class="result[^"]* results_links[^"]* web-result[^"]*">(.*?)</div>\s*</div>\s*</div>',
        html, _re.DOTALL,
    )

    results = []
    for block in blocks[:8]:
        # Title
        title_match = _re.search(
            r'class="result__a"[^>]*>(.*?)</a>', block, _re.DOTALL,
        )
        title = _re.sub(r"<[^>]+>", "", title_match.group(1)).strip() if title_match else ""

        # Display URL
        url_match = _re.search(
            r'class="result__url"[^>]*>(.*?)</a>', block, _re.DOTALL,
        )
        url = _re.sub(r"<[^>]+>", "", url_match.group(1)).strip() if url_match else ""

        # Snippet
        snippet_match = _re.search(
            r'class="result__snippet"[^>]*>(.*?)</a>', block, _re.DOTALL,
        )
        snippet = _re.sub(r"<[^>]+>", "", snippet_match.group(1)).strip() if snippet_match else ""

        if title or snippet:
            results.append(f"[{title}]({url})\n{snippet}")

    if results:
        return "\n\n---\n\n".join(results)
    return f"No results found for: {query}"


@tool("Read Web Page")
def read_web_page(url: str) -> str:
    """Fetch and read the text content of a web page.

    Use this to read articles, documentation, or any public web page.
    Only GET requests are allowed through the egress proxy.
    """
    try:
        resp = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "Kent-Gent/1.0"},
        )
        resp.raise_for_status()

        # Basic text extraction — strip HTML tags
        text = resp.text
        # Simple tag stripping; for production, use BeautifulSoup
        import re
        text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        # Truncate to avoid blowing up context
        max_chars = 10000
        if len(text) > max_chars:
            text = text[:max_chars] + "\n\n[Truncated]"

        return text

    except requests.RequestException as e:
        return f"Failed to fetch {url}: {e}"


@tool("Read File")
def read_file(filepath: str) -> str:
    """Read the contents of a file in the project data directory.

    Files are located under /data/. Provide a path relative to /data/
    or an absolute path starting with /data/.
    """
    if not filepath.startswith("/data/"):
        filepath = f"/data/{filepath.lstrip('/')}"

    # Security: prevent path traversal
    resolved = os.path.realpath(filepath)
    if not resolved.startswith("/data/"):
        return "Error: Access denied — path must be within /data/"

    try:
        with open(resolved, "r") as f:
            content = f.read()
        if len(content) > 50000:
            content = content[:50000] + "\n\n[Truncated]"
        return content
    except FileNotFoundError:
        return f"File not found: {filepath}"
    except Exception as e:
        return f"Error reading {filepath}: {e}"


@tool("Write File")
def write_file(filepath: str, content: str) -> str:
    """Write content to a file in the project data directory.

    Files are written under /data/. Provide a path relative to /data/
    or an absolute path starting with /data/.
    """
    if not filepath.startswith("/data/"):
        filepath = f"/data/{filepath.lstrip('/')}"

    resolved = os.path.realpath(filepath)
    if not resolved.startswith("/data/"):
        return "Error: Access denied — path must be within /data/"

    try:
        os.makedirs(os.path.dirname(resolved), exist_ok=True)
        with open(resolved, "w") as f:
            f.write(content)
        return f"Written {len(content)} chars to {filepath}"
    except Exception as e:
        return f"Error writing {filepath}: {e}"


def get_default_tools() -> list:
    """Return the standard tool set for Gent workers."""
    return [web_search, read_web_page, read_file, write_file]
