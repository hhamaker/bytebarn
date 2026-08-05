"""Config-declared lifecycle hooks for tool calls.

Config example::

    "hooks": {
      "pre_tool": [
        {"tool": "bash", "match": "rm *", "action": "deny",
         "message": "rm blocked by hook"},
        {"tool": "write", "match": "*", "command": "echo wrote"}
      ],
      "post_tool": [
        {"tool": "edit", "match": "*.py", "command": "echo edited"}
      ]
    }

``tool`` may be ``*`` for any tool. ``match`` is an fnmatch against the
permission arg (command string / path). ``action`` is ``deny`` or omit/allow.
``command`` runs a shell side-effect (best-effort, not sandboxed).
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Any


@dataclass
class HookResult:
    allowed: bool = True
    message: str = ""
    ran: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.ran is None:
            self.ran = []


@dataclass
class HookRule:
    tool: str = "*"
    match: str = "*"
    action: str = "allow"  # allow | deny
    message: str = ""
    command: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "HookRule":
        return cls(
            tool=str(d.get("tool") or "*"),
            match=str(d.get("match") or "*"),
            action=str(d.get("action") or "allow"),
            message=str(d.get("message") or ""),
            command=str(d.get("command") or ""),
        )


class HookRunner:
    def __init__(self, config: dict[str, Any] | None = None):
        raw = config or {}
        self.pre = [HookRule.from_dict(r) for r in (raw.get("pre_tool") or []) if isinstance(r, dict)]
        self.post = [HookRule.from_dict(r) for r in (raw.get("post_tool") or []) if isinstance(r, dict)]

    @classmethod
    def from_engine_config(cls, model_extra: dict | None, top_level: dict | None = None) -> "HookRunner":
        """Read hooks from model_extra or a top-level config dict."""
        extra = model_extra or {}
        hooks = extra.get("hooks")
        if hooks is None and top_level:
            hooks = top_level.get("hooks")
        if not isinstance(hooks, dict):
            hooks = {}
        return cls(hooks)

    def _matching(self, rules: list[HookRule], tool: str, arg: str) -> list[HookRule]:
        out = []
        for rule in rules:
            if rule.tool not in ("*", tool) and not fnmatch(tool, rule.tool):
                continue
            if not fnmatch(arg or "", rule.match):
                continue
            out.append(rule)
        return out

    async def run_pre(self, tool: str, arg: str, cwd: str | None = None) -> HookResult:
        result = HookResult()
        for rule in self._matching(self.pre, tool, arg):
            if rule.action == "deny":
                result.allowed = False
                result.message = rule.message or f"blocked by pre_tool hook ({tool})"
                return result
            if rule.command:
                await self._side(rule.command, cwd)
                result.ran.append(rule.command)
        return result

    async def run_post(self, tool: str, arg: str, cwd: str | None = None) -> HookResult:
        result = HookResult()
        for rule in self._matching(self.post, tool, arg):
            if rule.command:
                await self._side(rule.command, cwd)
                result.ran.append(rule.command)
        return result

    async def _side(self, command: str, cwd: str | None) -> None:
        """Best-effort side command; always kill on timeout or cancel."""
        proc = None
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=cwd or None,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=os.environ.copy(),
                start_new_session=True,
            )
            await asyncio.wait_for(proc.wait(), timeout=30)
        except asyncio.TimeoutError:
            if proc is not None and proc.returncode is None:
                try:
                    os.killpg(os.getpgid(proc.pid), 9)
                except (ProcessLookupError, PermissionError, OSError):
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
        except asyncio.CancelledError:
            if proc is not None and proc.returncode is None:
                try:
                    os.killpg(os.getpgid(proc.pid), 9)
                except (ProcessLookupError, PermissionError, OSError):
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
            raise
        except OSError:
            pass
