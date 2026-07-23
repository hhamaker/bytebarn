from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Literal

import httpx
from pydantic import BaseModel

from .base import Tool, ToolContext, ToolResult

MAX_BYTES = 5 * 1024 * 1024


class _HtmlToMarkdown(HTMLParser):
    _SKIP = {"script", "style", "noscript", "head", "svg"}
    _BLOCK = {"p", "div", "section", "article", "br", "tr", "li", "ul", "ol",
              "table", "blockquote", "pre", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0
        self._href: str | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag.startswith("h") and len(tag) == 2 and tag[1].isdigit():
            self.parts.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag == "a":
            self._href = dict(attrs).get("href")
            self.parts.append("[")
        elif tag in ("code", "pre"):
            self.parts.append("`")
        elif tag in ("b", "strong"):
            self.parts.append("**")
        elif tag in ("i", "em"):
            self.parts.append("*")
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag == "a":
            self.parts.append(f"]({self._href})" if self._href else "]")
            self._href = None
        elif tag in ("code", "pre"):
            self.parts.append("`")
        elif tag in ("b", "strong"):
            self.parts.append("**")
        elif tag in ("i", "em"):
            self.parts.append("*")
        elif tag in self._BLOCK or (tag.startswith("h") and len(tag) == 2):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def html_to_markdown(html: str) -> str:
    parser = _HtmlToMarkdown()
    parser.feed(html)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class WebFetchParams(BaseModel):
    url: str
    format: Literal["text", "markdown"] = "markdown"


class WebFetchTool(Tool):
    name = "webfetch"
    Params = WebFetchParams

    async def execute(self, params: WebFetchParams, ctx: ToolContext) -> ToolResult:
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
                async with client.stream("GET", params.url) as resp:
                    resp.raise_for_status()
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in resp.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_BYTES:
                            return ToolResult(
                                f"error: response exceeds {MAX_BYTES // (1024 * 1024)}MB cap",
                                is_error=True,
                            )
                        chunks.append(chunk)
                    body = b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")
                    content_type = resp.headers.get("content-type", "")
        except httpx.HTTPError as exc:
            return ToolResult(f"error: {exc}", is_error=True)

        if "html" in content_type and params.format == "markdown":
            body = html_to_markdown(body)
        return ToolResult(body, title=params.url)

    def permission_arg(self, params: WebFetchParams) -> str:
        return params.url
