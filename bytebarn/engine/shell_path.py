"""Recover the user's real PATH when launched from the macOS GUI.

An app opened from Finder or the Dock inherits launchd's PATH
(``/usr/bin:/bin:/usr/sbin:/sbin``) — not the one from ``.zprofile``. So
``claude``, ``node``, ``rg`` and everything else installed by Homebrew, npm
or cargo simply do not exist as far as the app is concerned, and every
spawn fails with "No such file or directory".

Asking the login shell once at startup fixes it for the whole process: the
Claude Code runtime, the bash tool, and MCP stdio servers all inherit it.
"""

from __future__ import annotations

import os
import shutil
import subprocess

# Where per-user tooling actually lands; used when the login shell cannot be
# asked (odd shells, sandboxes) so the app still finds the common cases.
_FALLBACK_DIRS = (
    "/opt/homebrew/bin",        # Homebrew, Apple silicon
    "/opt/homebrew/sbin",
    "/usr/local/bin",           # Homebrew, Intel + most installers
    "/usr/local/sbin",
    "~/.local/bin",             # pipx, uv, pip --user
    "~/.bun/bin",
    "~/.volta/bin",
    "~/.npm-global/bin",
    "~/.cargo/bin",
    "~/.claude/local/bin",      # Claude Code's own installer
    "~/.deno/bin",
)

# The PATH launchd hands a GUI app; anything at or below this is a sign we
# were not started from a shell.
_MINIMAL = {"/usr/bin", "/bin", "/usr/sbin", "/sbin"}


def looks_minimal(path: str | None = None) -> bool:
    # None means "ask the environment"; an empty string is itself an answer
    raw = os.environ.get("PATH", "") if path is None else path
    entries = {p for p in raw.split(os.pathsep) if p}
    return not entries or entries <= _MINIMAL


def login_shell_path(timeout: float = 4.0) -> str:
    """PATH as the user's login shell sees it ("" if it cannot be asked)."""
    shell = os.environ.get("SHELL") or "/bin/zsh"
    try:
        out = subprocess.run(
            [shell, "-ilc", 'printf %s "$PATH"'],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (out.stdout or "").strip()


def hydrate_path(force: bool = False) -> str:
    """Merge the login shell's PATH into this process; returns the new PATH.

    A no-op when the current PATH already looks like it came from a shell,
    unless ``force``. Existing entries keep priority, so nothing the caller
    set up is overridden."""
    current = os.environ.get("PATH", "")
    if not force and not looks_minimal(current):
        return current

    entries = [p for p in current.split(os.pathsep) if p]
    seen = set(entries)
    for candidate in login_shell_path().split(os.pathsep) + list(_FALLBACK_DIRS):
        if not candidate:
            continue
        expanded = os.path.expanduser(candidate)
        if expanded not in seen and os.path.isdir(expanded):
            entries.append(expanded)
            seen.add(expanded)

    merged = os.pathsep.join(entries)
    os.environ["PATH"] = merged
    return merged


def resolve_command(command: str) -> str | None:
    """Absolute path for ``command``, hydrating PATH first if it looks bare."""
    found = shutil.which(command)
    if found:
        return found
    hydrate_path()
    return shutil.which(command)
