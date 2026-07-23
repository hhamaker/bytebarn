"""Permission resolution (spec §8).

Per tool: "allow" | "ask" | "deny" or {default, allow: [...], deny: [...]}.
Bash patterns glob-match the command string; edit/write patterns match file
paths. Resolution: deny-list -> allow-list -> default. Per-agent overrides
layer over global config. Session presets map to blanket policies.
"""

from __future__ import annotations

from fnmatch import fnmatch
from typing import Any, Callable

from .config import PermissionRule, normalize_permission

ALLOW = "allow"
ASK = "ask"
DENY = "deny"

# tools that never need permission (memory writes are confined to the
# project's memory bundle under the crew home dir)
_ALWAYS_ALLOWED = {"read", "glob", "grep", "todowrite", "question", "task", "memory"}

_DEFAULTS: dict[str, str] = {
    "bash": ASK,
    "edit": ASK,
    "write": ASK,
    "webfetch": ASK,
    "websearch": ASK,
}

SAFE = "safe"        # deny bash/edit/write/webfetch/websearch
ASK_MODE = "ask"     # config-resolved, ask by default
FULL_AUTO = "full"   # allow everything


class PermissionPolicy:
    def __init__(
        self,
        config_permission: dict[str, Any] | None = None,
        agent_permission: dict[str, Any] | None = None,
        session_mode: str | Callable[[], str] = ASK_MODE,
    ):
        merged: dict[str, Any] = dict(config_permission or {})
        merged.update(agent_permission or {})
        self.rules: dict[str, PermissionRule] = {
            tool: normalize_permission(v) for tool, v in merged.items()
        }
        # a callable makes the mode *live*: policies are created when a run
        # starts, but a Full-auto switch must apply to runs already going
        self._session_mode = session_mode

    @property
    def session_mode(self) -> str:
        return self._session_mode() if callable(self._session_mode) else self._session_mode

    def resolve(self, tool: str, arg: str = "") -> str:
        """Return "allow" | "ask" | "deny" for a tool call.

        ``arg`` is the command string for bash, the file path for edit/write.
        """
        if self.session_mode == FULL_AUTO:
            return ALLOW
        if tool in _ALWAYS_ALLOWED and tool not in self.rules:
            return ALLOW
        # external MCP tools can have arbitrary side effects: ask by default
        is_mcp = tool.startswith("mcp__")
        if self.session_mode == SAFE and (tool in _DEFAULTS or is_mcp):
            return DENY
        rule = self.rules.get(tool)
        if rule is None:
            return ASK if is_mcp else _DEFAULTS.get(tool, ALLOW)
        for pattern in rule.deny:
            if fnmatch(arg, pattern):
                return DENY
        for pattern in rule.allow:
            if fnmatch(arg, pattern):
                return ALLOW
        return rule.default

    def with_added_allow(self, tool: str, pattern: str) -> None:
        """Session-local effect of "Allow always" (config write happens separately)."""
        rule = self.rules.setdefault(tool, PermissionRule(default=_DEFAULTS.get(tool, ALLOW)))
        rule.allow.append(pattern)
