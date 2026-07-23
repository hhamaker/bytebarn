"""Web search tool — DuckDuckGo HTML endpoint, no API key required."""

from __future__ import annotations

import re
import urllib.parse
from html import unescape

import httpx
from pydantic import BaseModel, Field

from .base import Tool, ToolContext, ToolResult

_ENDPOINT = "https://html.duckduckgo.com/html/"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh) Crew/1.0"}

# result anchors and snippets in the no-JS DuckDuckGo page
_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.S,
)
_SNIPPET_RE = re.compile(
    r'<a[^>]+class="result__snippet"[^>]*>(?P<snippet>.*?)</a>', re.S
)
_TAG_RE = re.compile(r"<[^>]+>")


def _clean(html: str) -> str:
    return unescape(_TAG_RE.sub("", html)).strip()


def _real_url(href: str) -> str:
    """DDG wraps result links as /l/?uddg=<encoded-url> — unwrap them."""
    parsed = urllib.parse.urlparse(href)
    if parsed.path.startswith("/l/"):
        qs = urllib.parse.parse_qs(parsed.query)
        if "uddg" in qs:
            return qs["uddg"][0]
    return href


def parse_results(html: str, limit: int) -> list[tuple[str, str, str]]:
    """(title, url, snippet) triples from the DDG html page."""
    titles = [(m.group("title"), m.group("href")) for m in _RESULT_RE.finditer(html)]
    snippets = [m.group("snippet") for m in _SNIPPET_RE.finditer(html)]
    out: list[tuple[str, str, str]] = []
    for i, (title, href) in enumerate(titles[:limit]):
        snippet = snippets[i] if i < len(snippets) else ""
        out.append((_clean(title), _real_url(href), _clean(snippet)))
    return out


class WebSearchParams(BaseModel):
    query: str
    limit: int = Field(default=8, ge=1, le=20)


class WebSearchTool(Tool):
    name = "websearch"
    Params = WebSearchParams

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self._transport = transport  # tests inject a MockTransport

    async def execute(self, params: WebSearchParams, ctx: ToolContext) -> ToolResult:
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=20, headers=_HEADERS,
                transport=self._transport,
            ) as client:
                resp = await client.get(_ENDPOINT, params={"q": params.query})
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            return ToolResult(f"error: {exc}", is_error=True)

        results = parse_results(resp.text, params.limit)
        if not results:
            return ToolResult("no results", title=params.query)
        lines = []
        for i, (title, url, snippet) in enumerate(results, 1):
            lines.append(f"{i}. {title}\n   {url}")
            if snippet:
                lines.append(f"   {snippet}")
        return ToolResult("\n".join(lines), title=params.query)

    def permission_arg(self, params: WebSearchParams) -> str:
        return params.query
