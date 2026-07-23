"""Run checkpoints: snapshot files before agent writes, review/revert after.

Before the write/edit tools touch a file, the runner records the file's
pre-run contents (or that it didn't exist) under
``<global>/checkpoints/<session>/<run>/``. After the run, the UI can show a
unified diff of everything the run changed and revert it file-by-file or
wholesale — which is what makes Full-auto mode safe to walk away from.

bash writes cannot be snapshotted (arbitrary side effects); the review
panel is scoped to write/edit tool changes.
"""

from __future__ import annotations

import difflib
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RunCheckpoint:
    id: str
    session_id: str
    dir: Path
    started_at: float
    # target path -> snapshot filename ("" = file did not exist before the run)
    originals: dict[str, str] = field(default_factory=dict)

    def manifest(self) -> Path:
        return self.dir / "manifest.json"


class CheckpointStore:
    """Per-session run checkpoints, persisted on disk."""

    def __init__(self, root: Path):
        self.root = root
        self._active: dict[str, RunCheckpoint] = {}   # session_id -> current run
        self._last: dict[str, RunCheckpoint] = {}     # session_id -> last finished run

    # -- lifecycle (called by the runner) -----------------------------------

    def begin(self, session_id: str) -> RunCheckpoint:
        cp = RunCheckpoint(
            id=uuid.uuid4().hex[:12],
            session_id=session_id,
            dir=self.root / session_id / f"{int(time.time())}-{uuid.uuid4().hex[:6]}",
            started_at=time.time(),
        )
        self._active[session_id] = cp
        return cp

    def snapshot(self, session_id: str, path: Path | str) -> None:
        """Record a file's pre-write contents (first write per run wins)."""
        cp = self._active.get(session_id)
        if cp is None:
            cp = self.begin(session_id)
        key = str(Path(path).resolve())
        if key in cp.originals:
            return  # already captured for this run
        cp.dir.mkdir(parents=True, exist_ok=True)
        target = Path(key)
        if target.is_file():
            name = f"{len(cp.originals):04d}-{target.name}"
            try:
                (cp.dir / name).write_bytes(target.read_bytes())
            except OSError:
                return
            cp.originals[key] = name
        else:
            cp.originals[key] = ""  # created by this run
        cp.manifest().write_text(json.dumps(cp.originals, indent=1))

    def finish(self, session_id: str) -> RunCheckpoint | None:
        """Close the active run; keep it as the session's reviewable last run."""
        cp = self._active.pop(session_id, None)
        if cp is not None and cp.originals:
            self._last[session_id] = cp
        return cp

    # -- review (called by the UI) ------------------------------------------

    def last(self, session_id: str) -> RunCheckpoint | None:
        """The most recent finished run that changed files (None = nothing)."""
        return self._last.get(session_id)

    def changed_files(self, cp: RunCheckpoint) -> list[str]:
        return sorted(cp.originals.keys())

    def diff(self, cp: RunCheckpoint, path: str) -> str:
        """Unified diff from the pre-run snapshot to the file's current state."""
        before = self._original_text(cp, path)
        target = Path(path)
        try:
            after = target.read_text() if target.is_file() else ""
        except (OSError, UnicodeDecodeError):
            after = "[binary or unreadable]"
        label = str(path)
        lines = difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True),
            fromfile=f"{label} (before run)", tofile=f"{label} (after run)",
        )
        return "".join(lines) or "(no textual change)"

    def revert_file(self, cp: RunCheckpoint, path: str) -> None:
        """Restore one file to its pre-run state (delete if the run created it)."""
        snap = cp.originals.get(path)
        target = Path(path)
        if snap == "":
            target.unlink(missing_ok=True)
        elif snap:
            source = cp.dir / snap
            if source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())

    def revert_all(self, cp: RunCheckpoint) -> list[str]:
        for path in cp.originals:
            self.revert_file(cp, path)
        return sorted(cp.originals.keys())

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _original_text(cp: RunCheckpoint, path: str) -> str:
        snap = cp.originals.get(path)
        if not snap:
            return ""
        source = cp.dir / snap
        try:
            return source.read_text() if source.is_file() else ""
        except (OSError, UnicodeDecodeError):
            return "[binary or unreadable]"
