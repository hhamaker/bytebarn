"""Git worktree isolation for parallel subagents.

When a subagent is spawned into a git project, it can work in its own
worktree + branch so concurrent writers do not stomp each other. On finish
we merge non-conflicting file changes back into the parent working tree and
remove the worktree.

Non-git projects skip isolation (subagents share the parent directory).
"""

from __future__ import annotations

import asyncio
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Worktree:
    """One isolated subagent workspace."""

    session_id: str
    path: Path
    branch: str
    base_commit: str
    parent_cwd: Path
    git_root: Path


@dataclass
class ApplyResult:
    applied: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    error: str = ""

    def summary(self) -> str:
        if self.error:
            return f"worktree merge failed: {self.error}"
        parts = [f"applied {len(self.applied)} file(s)"]
        if self.deleted:
            parts.append(f"deleted {len(self.deleted)}")
        if self.conflicts:
            parts.append(f"conflicts: {', '.join(self.conflicts)}")
        return "worktree: " + "; ".join(parts)


async def _git(cwd: Path, *args: str, timeout: float = 30.0) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (OSError, asyncio.TimeoutError) as exc:
        return 1, str(exc)
    text = (out or b"").decode(errors="replace")
    if proc.returncode != 0:
        text = (text + "\n" + (err or b"").decode(errors="replace")).strip()
    return proc.returncode or 0, text.strip()


async def git_root(cwd: Path) -> Path | None:
    """Return the git toplevel for cwd, or None if not a repo."""
    code, out = await _git(cwd, "rev-parse", "--show-toplevel")
    if code != 0 or not out:
        return None
    path = Path(out)
    return path if path.is_dir() else None


async def head_commit(git_root: Path) -> str | None:
    code, out = await _git(git_root, "rev-parse", "HEAD")
    return out if code == 0 and out else None


class WorktreeManager:
    """Create / apply / remove per-subagent worktrees under a store root."""

    def __init__(self, store_root: Path):
        self.store_root = store_root
        self._active: dict[str, Worktree] = {}  # session_id -> Worktree

    def get(self, session_id: str) -> Worktree | None:
        return self._active.get(session_id)

    async def create(
        self,
        session_id: str,
        parent_cwd: Path,
        project_key: str = "default",
    ) -> Worktree | None:
        """Add a worktree for session_id. Returns None if isolation is unavailable."""
        root = await git_root(parent_cwd)
        if root is None:
            return None
        base = await head_commit(root)
        if not base:
            return None  # empty repo / no commits

        branch = f"bytebarn/{session_id[:12]}"
        path = self.store_root / project_key / session_id
        if path.exists():
            # leftover from a crashed run — drop it
            await self._force_remove(root, path, branch)

        path.parent.mkdir(parents=True, exist_ok=True)
        code, out = await _git(
            root, "worktree", "add", "-b", branch, str(path), base,
            timeout=60.0,
        )
        if code != 0:
            # branch may already exist; try without -b
            code2, out2 = await _git(
                root, "worktree", "add", str(path), base, timeout=60.0,
            )
            if code2 != 0:
                return None
            branch = f"(detached@{base[:8]})"
            out = out2

        wt = Worktree(
            session_id=session_id,
            path=path.resolve(),
            branch=branch,
            base_commit=base,
            parent_cwd=parent_cwd.resolve(),
            git_root=root,
        )
        self._active[session_id] = wt
        return wt

    async def changed_files(self, wt: Worktree) -> tuple[list[str], list[str], list[str]]:
        """Return (modified_or_added, deleted, untracked) paths relative to git root."""
        # relative path from git root to worktree (usually absolute path outside)
        code, diff_m = await _git(
            wt.path, "diff", "--name-only", "--diff-filter=ACMR", wt.base_commit,
        )
        modified = [l for l in diff_m.splitlines() if l.strip()] if code == 0 else []

        code, diff_d = await _git(
            wt.path, "diff", "--name-only", "--diff-filter=D", wt.base_commit,
        )
        deleted = [l for l in diff_d.splitlines() if l.strip()] if code == 0 else []

        code, untracked = await _git(
            wt.path, "ls-files", "--others", "--exclude-standard",
        )
        extra = [l for l in untracked.splitlines() if l.strip()] if code == 0 else []
        return modified, deleted, extra

    async def _blob_at(self, wt: Worktree, rel: str) -> bytes | None:
        """File bytes at base commit, or None if the path did not exist then."""
        code, _ = await _git(wt.path, "cat-file", "-e", f"{wt.base_commit}:{rel}")
        if code != 0:
            return None
        proc = await asyncio.create_subprocess_exec(
            "git", "show", f"{wt.base_commit}:{rel}",
            cwd=str(wt.path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        return out if proc.returncode == 0 else None

    def _parent_path(self, wt: Worktree, rel: str) -> Path:
        # Files are relative to the worktree root (= git root content).
        # Parent session cwd may be the git root or a subdir — map into parent_cwd
        # when parent_cwd is the git root or inside it.
        git_rel = Path(rel)
        try:
            parent_rel = wt.parent_cwd.resolve().relative_to(wt.git_root.resolve())
            # parent is inside repo: path relative to parent = strip parent_rel prefix
            # if file is under parent_cwd
            try:
                return (wt.parent_cwd / git_rel.relative_to(parent_rel)).resolve()
            except ValueError:
                # file outside parent cwd but in repo — write under git root mapping
                return (wt.git_root / git_rel).resolve()
        except ValueError:
            return (wt.parent_cwd / git_rel).resolve()

    async def apply(self, wt: Worktree) -> ApplyResult:
        """Copy non-conflicting changes from the worktree into the parent tree."""
        result = ApplyResult()
        try:
            modified, deleted, untracked = await self.changed_files(wt)
        except Exception as exc:
            result.error = str(exc)
            return result

        for rel in modified + untracked:
            src = (wt.path / rel).resolve()
            if not src.is_file():
                continue
            dst = self._parent_path(wt, rel)
            try:
                new_bytes = src.read_bytes()
            except OSError as exc:
                result.conflicts.append(f"{rel} ({exc})")
                continue
            base_bytes = await self._blob_at(wt, rel)
            if dst.is_file():
                try:
                    current = dst.read_bytes()
                except OSError:
                    current = b""
                if (
                    base_bytes is not None
                    and current != base_bytes
                    and current != new_bytes
                ):
                    result.conflicts.append(rel)
                    continue
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(new_bytes)
                # preserve mode when possible
                try:
                    shutil.copystat(src, dst, follow_symlinks=True)
                except OSError:
                    pass
                result.applied.append(rel)
            except OSError as exc:
                result.conflicts.append(f"{rel} ({exc})")

        for rel in deleted:
            dst = self._parent_path(wt, rel)
            if not dst.exists():
                result.deleted.append(rel)
                continue
            base_bytes = await self._blob_at(wt, rel)
            try:
                current = dst.read_bytes() if dst.is_file() else b""
            except OSError:
                current = b""
            if base_bytes is not None and current != base_bytes:
                result.conflicts.append(rel)
                continue
            try:
                if dst.is_file() or dst.is_symlink():
                    dst.unlink()
                elif dst.is_dir():
                    shutil.rmtree(dst)
                result.deleted.append(rel)
            except OSError as exc:
                result.conflicts.append(f"{rel} ({exc})")

        return result

    async def remove(self, session_id: str) -> None:
        wt = self._active.pop(session_id, None)
        if wt is None:
            return
        await self._force_remove(wt.git_root, wt.path, wt.branch)

    async def _force_remove(self, git_root: Path, path: Path, branch: str) -> None:
        if path.exists():
            code, _ = await _git(git_root, "worktree", "remove", "--force", str(path), timeout=60.0)
            if code != 0 and path.exists():
                shutil.rmtree(path, ignore_errors=True)
                await _git(git_root, "worktree", "prune")
        # drop the throwaway branch if we created one
        if branch.startswith("bytebarn/"):
            await _git(git_root, "branch", "-D", branch)

    async def apply_and_remove(self, session_id: str) -> ApplyResult | None:
        wt = self._active.get(session_id)
        if wt is None:
            return None
        result = await self.apply(wt)
        await self.remove(session_id)
        return result
