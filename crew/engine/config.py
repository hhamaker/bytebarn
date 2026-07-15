"""Config loading, merging, and format-preserving patching.

Two layers: global (~/.crew/config.json) and project (<project>/.crew/config.json).
Project wins per key via deep merge. Files are JSON with // comments and
trailing commas tolerated. Programmatic writes patch key-by-key, preserving
the untouched text of the file (comments, formatting).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

GLOBAL_DIR = Path(os.environ.get("CREW_HOME", str(Path.home() / ".crew")))


class _Deleted:
    """Sentinel: remove this key when patching."""

    def __repr__(self) -> str:  # pragma: no cover
        return "DELETE"


DELETE = _Deleted()


# ---------------------------------------------------------------------------
# Lenient JSON
# ---------------------------------------------------------------------------


def _strip_comments_and_trailing_commas(text: str) -> str:
    out: list[str] = []
    i = 0
    n = len(text)
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        if c == ",":
            # trailing comma if next non-ws char is } or ]
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            # also skip comments between comma and closer
            k = j
            while k + 1 < n and text[k] == "/" and text[k + 1] == "/":
                while k < n and text[k] != "\n":
                    k += 1
                while k < n and text[k] in " \t\r\n":
                    k += 1
            if k < n and text[k] in "}]":
                i += 1
                continue
        out.append(c)
        i += 1
    return "".join(out)


def lenient_json_loads(text: str) -> Any:
    if not text.strip():
        return {}
    return json.loads(_strip_comments_and_trailing_commas(text))


# ---------------------------------------------------------------------------
# Deep merge
# ---------------------------------------------------------------------------


def deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    api_key_env: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    api: str = "anthropic"  # "anthropic" | "openai"

    def resolve_key(self) -> str | None:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.environ.get(self.api_key_env)
        return None


class PermissionRule(BaseModel):
    default: str = "ask"  # allow | ask | deny
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)


def normalize_permission(value: Any) -> PermissionRule:
    if isinstance(value, str):
        return PermissionRule(default=value)
    if isinstance(value, PermissionRule):
        return value
    return PermissionRule(**value)


class AgentOverride(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    color: str | None = None
    prompt: str | None = None
    description: str | None = None
    mode: str | None = None
    steps: int | None = None
    hidden: bool | None = None
    permission: dict[str, Any] | None = None


class Config(BaseModel):
    model_config = ConfigDict(extra="allow")

    provider: dict[str, ProviderConfig] = Field(default_factory=dict)
    model: str = ""          # empty = use provider's first curated model
    small_model: str = ""
    agent: dict[str, AgentOverride] = Field(default_factory=dict)
    permission: dict[str, Any] = Field(default_factory=dict)
    instructions: list[str] = Field(default_factory=lambda: ["AGENTS.md", "CLAUDE.md"])

    def permission_rules(self) -> dict[str, PermissionRule]:
        return {tool: normalize_permission(v) for tool, v in self.permission.items()}


DEFAULT_CONFIG = {
    "provider": {
        "anthropic": {"api_key_env": "ANTHROPIC_API_KEY", "api": "anthropic"},
        "openai": {"api_key_env": "OPENAI_API_KEY", "api": "openai"},
        "xai": {
            "api_key_env": "XAI_API_KEY",
            "api": "openai",
            "base_url": "https://api.x.ai/v1",
        },
    },
}


def load_config(project_dir: Path | str | None = None, global_dir: Path | None = None) -> Config:
    global_dir = global_dir or GLOBAL_DIR
    merged: dict = dict(DEFAULT_CONFIG)
    gpath = global_dir / "config.json"
    if gpath.exists():
        merged = deep_merge(merged, lenient_json_loads(gpath.read_text()))
    if project_dir is not None:
        ppath = Path(project_dir) / ".crew" / "config.json"
        if ppath.exists():
            merged = deep_merge(merged, lenient_json_loads(ppath.read_text()))
    return Config(**merged)


# ---------------------------------------------------------------------------
# Format-preserving patcher
# ---------------------------------------------------------------------------
#
# A tiny recursive-descent parser over JSON-with-comments that records, for
# every value, its (start, end) span in the original text, and for every
# object, the span of its braces and the end position of its last member.
# Patching replaces/inserts/deletes spans, leaving all other text untouched.


class _Node:
    __slots__ = ("start", "end", "kind", "members", "last_member_end", "member_spans")

    def __init__(self, start: int, end: int, kind: str):
        self.start = start
        self.end = end
        self.kind = kind  # "object" | "other"
        self.members: dict[str, _Node] = {}
        self.member_spans: dict[str, tuple[int, int]] = {}  # key -> (key_start, value_end)
        self.last_member_end = -1


class _SpanParser:
    def __init__(self, text: str):
        self.text = text
        self.i = 0

    def error(self, msg: str) -> Exception:
        return ValueError(f"config parse error at {self.i}: {msg}")

    def skip_ws(self) -> None:
        t = self.text
        n = len(t)
        while self.i < n:
            c = t[self.i]
            if c in " \t\r\n":
                self.i += 1
            elif c == "/" and self.i + 1 < n and t[self.i + 1] == "/":
                while self.i < n and t[self.i] != "\n":
                    self.i += 1
            elif c == "/" and self.i + 1 < n and t[self.i + 1] == "*":
                self.i += 2
                while self.i + 1 < n and not (t[self.i] == "*" and t[self.i + 1] == "/"):
                    self.i += 1
                self.i += 2
            else:
                return

    def parse_string(self) -> str:
        t = self.text
        assert t[self.i] == '"'
        start = self.i
        self.i += 1
        while self.i < len(t):
            c = t[self.i]
            if c == "\\":
                self.i += 2
                continue
            if c == '"':
                self.i += 1
                return json.loads(t[start : self.i])
            self.i += 1
        raise self.error("unterminated string")

    def parse_value(self) -> _Node:
        self.skip_ws()
        t = self.text
        c = t[self.i]
        start = self.i
        if c == "{":
            return self.parse_object()
        if c == "[":
            depth = 0
            in_str = False
            while self.i < len(t):
                c = t[self.i]
                if in_str:
                    if c == "\\":
                        self.i += 1
                    elif c == '"':
                        in_str = False
                elif c == '"':
                    in_str = True
                elif c == "[":
                    depth += 1
                elif c == "]":
                    depth -= 1
                    if depth == 0:
                        self.i += 1
                        return _Node(start, self.i, "other")
                self.i += 1
            raise self.error("unterminated array")
        if c == '"':
            self.parse_string()
            return _Node(start, self.i, "other")
        # number / true / false / null
        while self.i < len(t) and t[self.i] not in ",}] \t\r\n/":
            self.i += 1
        return _Node(start, self.i, "other")

    def parse_object(self) -> _Node:
        t = self.text
        assert t[self.i] == "{"
        start = self.i
        node = _Node(start, -1, "object")
        self.i += 1
        while True:
            self.skip_ws()
            if self.i >= len(t):
                raise self.error("unterminated object")
            if t[self.i] == "}":
                node.end = self.i + 1
                self.i += 1
                return node
            if t[self.i] == ",":
                self.i += 1
                continue
            key_start = self.i
            key = self.parse_string()
            self.skip_ws()
            if t[self.i] != ":":
                raise self.error("expected ':'")
            self.i += 1
            value = self.parse_value()
            node.members[key] = value
            node.member_spans[key] = (key_start, value.end)
            node.last_member_end = value.end


def _format_value(value: Any, indent: int) -> str:
    text = json.dumps(value, indent=2)
    pad = " " * indent
    lines = text.split("\n")
    return "\n".join([lines[0]] + [pad + ln for ln in lines[1:]])


def _nested(path: list[str], value: Any) -> Any:
    for key in reversed(path):
        value = {key: value}
    return value


def patch_config_file(path: Path | str, updates: dict[str, Any]) -> None:
    """Apply dotted-key updates to a config file, preserving formatting.

    ``updates`` maps dotted paths (``"agent.build.model"``) to new values.
    ``DELETE`` as a value removes the key. Missing files/parents are created.
    """
    path = Path(path)
    if not path.exists():
        data: dict = {}
        for dotted, value in updates.items():
            if value is DELETE:
                continue
            parts = dotted.split(".")
            cur = data
            for p in parts[:-1]:
                cur = cur.setdefault(p, {})
            cur[parts[-1]] = value
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n")
        return

    for dotted, value in updates.items():
        text = path.read_text()
        if not text.strip():
            text = "{}"
        parser = _SpanParser(text)
        parser.skip_ws()
        root = parser.parse_object()
        parts = dotted.split(".")

        # descend to the deepest existing object along the path
        node = root
        depth = 0
        while depth < len(parts) - 1:
            child = node.members.get(parts[depth])
            if child is None or child.kind != "object":
                break
            node = child
            depth += 1

        remaining = parts[depth:]
        leaf_key = remaining[0]

        if len(remaining) == 1 and leaf_key in node.members:
            if value is DELETE:
                key_start, value_end = node.member_spans[leaf_key]
                # consume a trailing comma and the rest of the line if empty
                j = value_end
                while j < len(text) and text[j] in " \t":
                    j += 1
                if j < len(text) and text[j] == ",":
                    j += 1
                while j < len(text) and text[j] in " \t":
                    j += 1
                if j < len(text) and text[j] == "\n":
                    j += 1
                    # deleted whole line: also remove its leading indent
                    line_start = text.rfind("\n", 0, key_start) + 1
                    if text[line_start:key_start].strip() == "":
                        key_start = line_start
                # a dangling comma left before "}" is tolerated by lenient parse
                text = text[:key_start] + text[j:]
            else:
                vstart = node.members[leaf_key].start
                vend = node.members[leaf_key].end
                line_start = text.rfind("\n", 0, vstart) + 1
                indent = len(text[line_start:vstart]) - len(text[line_start:vstart].lstrip())
                text = text[:vstart] + _format_value(value, indent) + text[vend:]
        else:
            if value is DELETE:
                continue  # nothing to delete
            # insert into `node`: build the nested remainder as one member
            insert_value = _nested(remaining[1:], value)
            # figure indentation from an existing member or default 2
            obj_line_start = text.rfind("\n", 0, node.start) + 1
            base_indent = node.start - obj_line_start
            member_indent = base_indent + 2
            if node.members:
                any_key_start = next(iter(node.member_spans.values()))[0]
                mline = text.rfind("\n", 0, any_key_start) + 1
                member_indent = any_key_start - mline
            snippet = f'"{leaf_key}": {_format_value(insert_value, member_indent)}'
            if node.last_member_end != -1:
                pos = node.last_member_end
                text = text[:pos] + ",\n" + " " * member_indent + snippet + text[pos:]
            else:
                pos = node.start + 1
                closing_indent = " " * base_indent
                text = (
                    text[:pos]
                    + "\n"
                    + " " * member_indent
                    + snippet
                    + "\n"
                    + closing_indent
                    + text[pos:]
                )
        path.write_text(text)
