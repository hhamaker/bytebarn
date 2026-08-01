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
_ALWAYS_ALLOWED = {
    "read", "glob", "grep", "todowrite", "question", "task", "memory", "skill",
}

_DEFAULTS: dict[str, str] = {
    "bash": ASK,
    "edit": ASK,
    "write": ASK,
    "webfetch": ASK,
    "websearch": ASK,
}

# Mutating tools blocked in Plan mode (and typically in Safe).
_MUTATING_TOOLS = {"edit", "write"}

# Read-only bash prefixes for Plan mode (explore without changing the tree).
# Matched with fnmatch against the full command string.
PLAN_BASH_ALLOW = (
    "git status*", "git log*", "git diff*", "git show*", "git branch*",
    "git rev-parse*", "git remote*", "git blame*", "git ls-*",
    "ls*", "cat*", "head*", "tail*", "less*", "more*",
    "rg*", "grep*", "find*", "wc*", "file*", "tree*",
    "pwd", "pwd *", "which*", "type*", "echo*", "printf*",
    "stat*", "du*", "df*", "uname*", "date*", "env*", "printenv*",
)

SAFE = "safe"        # deny bash/edit/write/webfetch/websearch
PLAN = "plan"        # explore + design: allow reads/web; deny mutations
ASK_MODE = "ask"     # config-resolved, ask by default
FULL_AUTO = "full"   # allow everything

# UI / config order for the status-bar mode combo
SESSION_MODES = (SAFE, PLAN, ASK_MODE, FULL_AUTO)
SESSION_MODE_LABELS = {
    SAFE: "Safe",
    PLAN: "Plan",
    ASK_MODE: "Ask",
    FULL_AUTO: "Full-auto",
}

# Injected into the system prompt whenever Plan mode is active.
PLAN_MODE_NOTICE = """\
<plan-mode>
You are in PLAN MODE. Explore freely (read, search, read-only shell, web) and
produce a concrete implementation plan — ordered steps, real file paths, risks,
and how to verify. Do NOT modify any files, run commands that change state, or
apply patches. When the plan is ready, present it clearly and stop; the user
will leave Plan mode (Ask or Full-auto) to implement.
</plan-mode>"""


def bash_is_readonly(command: str) -> bool:
    """True if a bash command looks safe for Plan mode exploration."""
    cmd = (command or "").strip()
    if not cmd:
        return False
    # multi-statement / redirects / writes are never readonly for our purposes
    if any(ch in cmd for ch in (";", "&&", "||", "|", ">", "<", "`", "\n")):
        # allow simple pipes of readonly tools? keep strict for safety
        if ">" in cmd or ">>" in cmd or ";" in cmd or "&&" in cmd or "||" in cmd:
            return False
        # single pipeline of readonly tools is ok (e.g. git log | head)
        parts = [p.strip() for p in cmd.split("|")]
        return all(any(fnmatch(p, pat) for pat in PLAN_BASH_ALLOW) for p in parts if p)
    return any(fnmatch(cmd, pat) for pat in PLAN_BASH_ALLOW)


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
        if self.session_mode == PLAN:
            return self._resolve_plan(tool, arg)
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

    def _resolve_plan(self, tool: str, arg: str) -> str:
        """Plan mode: explore and design; hard-deny anything that mutates.

        Unlike Safe, web search/fetch and read-only bash are allowed so the
        agent can research and inspect before writing a plan.
        """
        if tool in _MUTATING_TOOLS:
            return DENY
        if tool.startswith("mcp__"):
            return DENY
        if tool == "bash":
            return ALLOW if bash_is_readonly(arg) else DENY
        if tool in _ALWAYS_ALLOWED or tool in ("webfetch", "websearch"):
            return ALLOW
        # unknown tools: deny rather than ask (plan mode is a hard fence)
        return DENY

    def with_added_allow(self, tool: str, pattern: str) -> None:
        """Session-local effect of "Allow always" (config write happens separately)."""
        rule = self.rules.setdefault(tool, PermissionRule(default=_DEFAULTS.get(tool, ALLOW)))
        rule.allow.append(pattern)
