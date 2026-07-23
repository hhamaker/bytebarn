"""Auto-updater tied to the GitHub repo (hhamaker/bytebarn).

Two install modes, two strategies:

- **Source checkout** (normal ``git clone`` + venv): updates ride the git
  remote — check compares HEAD against ``origin``'s default branch, update
  is a fast-forward pull plus ``pip install -e .`` and a restart.
- **Frozen app** (PyInstaller bundle): checks the GitHub Releases API and
  points the user at the release page for the new build.

Zero Qt in here; the UI drives these coroutines and renders the results.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_REPO = "hhamaker/bytebarn"
_RELEASES_URL = "https://api.github.com/repos/{repo}/releases/latest"


def current_version() -> str:
    try:
        from importlib.metadata import version

        return version("bytebarn")
    except Exception:
        return "0.0.0"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def repo_root() -> Path | None:
    """The git checkout this code runs from (None for frozen bundles)."""
    if is_frozen():
        return None
    root = Path(__file__).resolve().parent.parent
    return root if (root / ".git").exists() else None


def parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", text)[:3]) or (0,)


@dataclass
class UpdateInfo:
    kind: str                     # "none" | "git" | "release" | "error"
    message: str = ""
    behind: int = 0               # git: commits behind origin
    commits: list[str] = field(default_factory=list)
    version: str = ""             # release: latest tag
    url: str = ""                 # release: html page
    dirty: bool = False           # git: local uncommitted changes present

    @property
    def available(self) -> bool:
        return self.kind in ("git", "release")


async def _git(root: Path, *args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(root), *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace").strip()


async def _default_branch(root: Path) -> str:
    code, out = await _git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if code == 0 and out.startswith("origin/"):
        return out.split("/", 1)[1]
    return "main"


async def check_git_update(root: Path) -> UpdateInfo:
    """Compare the local checkout against origin's default branch."""
    code, out = await _git(root, "fetch", "--quiet", "origin")
    if code != 0:
        return UpdateInfo("error", f"git fetch failed: {out[:200]}")
    branch = await _default_branch(root)
    code, out = await _git(root, "rev-list", "--count", f"HEAD..origin/{branch}")
    if code != 0:
        return UpdateInfo("error", f"git rev-list failed: {out[:200]}")
    behind = int(out or "0")
    if behind == 0:
        return UpdateInfo("none", "You're up to date.")
    _, log = await _git(root, "log", "--oneline", f"HEAD..origin/{branch}", "-n", "10")
    _, dirty = await _git(root, "status", "--porcelain")
    return UpdateInfo(
        "git", f"{behind} update{'s' if behind != 1 else ''} available",
        behind=behind, commits=log.splitlines(), dirty=bool(dirty),
    )


async def apply_git_update(root: Path, run_pip: bool = True) -> tuple[bool, str]:
    """Fast-forward to origin and reinstall; (ok, detail)."""
    branch = await _default_branch(root)
    code, out = await _git(root, "pull", "--ff-only", "origin", branch)
    if code != 0:
        return False, f"git pull failed: {out[:400]}"
    if run_pip:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", "-e", str(root),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        pip_out, _ = await proc.communicate()
        if proc.returncode != 0:
            return False, f"pip install failed: {pip_out.decode()[-400:]}"
    return True, out


async def check_release_update(repo: str = DEFAULT_REPO, fetch=None) -> UpdateInfo:
    """Compare the running version against the latest GitHub release."""
    if fetch is None:
        async def fetch(url: str) -> dict:
            import httpx

            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    url, headers={"Accept": "application/vnd.github+json"})
                response.raise_for_status()
                return response.json()

    try:
        data = await fetch(_RELEASES_URL.format(repo=repo))
    except Exception as exc:
        return UpdateInfo("error", f"release check failed: {exc}")
    tag = str(data.get("tag_name") or "")
    if not tag:
        return UpdateInfo("none", "No releases published yet.")
    if parse_version(tag) <= parse_version(current_version()):
        return UpdateInfo("none", "You're up to date.")
    return UpdateInfo(
        "release", f"Version {tag.lstrip('v')} is available",
        version=tag, url=str(data.get("html_url") or f"https://github.com/{repo}/releases"),
    )


async def check_for_update() -> UpdateInfo:
    """Pick the right strategy for how this copy of ByteBarn is installed."""
    root = repo_root()
    if root is not None:
        return await check_git_update(root)
    return await check_release_update()


def restart() -> None:
    """Replace this process with a fresh copy of the app."""
    if is_frozen():
        os.execv(sys.executable, [sys.executable] + sys.argv[1:])
    else:
        os.execv(sys.executable,
                 [sys.executable, "-m", "crew.main"] + sys.argv[1:])
