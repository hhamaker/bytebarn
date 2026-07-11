"""Markdown -> HTML for the transcript (markdown-it-py + pygments)."""

from __future__ import annotations

import html

from markdown_it import MarkdownIt
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.util import ClassNotFound

_formatter = HtmlFormatter(nowrap=True, noclasses=True, style="monokai")


def _highlight_code(code: str, lang: str, _attrs) -> str:
    try:
        lexer = get_lexer_by_name(lang) if lang else TextLexer()
    except ClassNotFound:
        lexer = TextLexer()
    highlighted = highlight(code, lexer, _formatter)
    # explicit monokai fg via <font> (Qt rich text ignores css color on <pre>):
    # plain tokens otherwise inherit the theme text color, which is
    # near-black on light themes -> unreadable on the dark bg
    return (
        '<pre style="background-color:#282828; padding:8px; border-radius:4px;">'
        f'<font color="#f8f8f2"><code>{highlighted}</code></font></pre>'
    )


_md = MarkdownIt("commonmark", {"highlight": _highlight_code}).enable("table").enable("strikethrough")


def render_markdown(text: str) -> str:
    return _md.render(text)


def escape(text: str) -> str:
    return html.escape(text)
