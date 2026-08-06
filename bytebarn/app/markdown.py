"""Markdown -> HTML for the transcript (markdown-it-py + pygments)."""

from __future__ import annotations

import html

from markdown_it import MarkdownIt
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.util import ClassNotFound

# One highlighter per look: monokai's palette is tuned for dark surfaces and
# turns near-invisible on paper white, so light themes get a light style and
# a light block background to match.
_FORMATTERS = {
    "dark": (HtmlFormatter(nowrap=True, noclasses=True, style="monokai"),
             "#1d2027", "#f8f8f2"),
    "light": (HtmlFormatter(nowrap=True, noclasses=True, style="friendly"),
              "#f1f2f5", "#2a2f38"),
}


def _code_style() -> tuple[HtmlFormatter, str, str]:
    from . import theme

    return _FORMATTERS["light" if theme.current_mode() == "light" else "dark"]


def _highlight_code(code: str, lang: str, _attrs) -> str:
    try:
        lexer = get_lexer_by_name(lang) if lang else TextLexer()
    except ClassNotFound:
        lexer = TextLexer()
    formatter, background, foreground = _code_style()
    highlighted = highlight(code, lexer, formatter)
    # explicit fg via <font> (Qt rich text ignores css color on <pre>): plain
    # tokens otherwise inherit the theme text color, which fights the block
    return (
        f'<pre style="background-color:{background}; padding:10px;'
        ' border-radius:8px;">'
        f'<font color="{foreground}"><code>{highlighted}</code></font></pre>'
    )


_md = MarkdownIt("commonmark", {"highlight": _highlight_code}).enable("table").enable("strikethrough")


def render_markdown(text: str) -> str:
    return _md.render(text)


def escape(text: str) -> str:
    return html.escape(text)
