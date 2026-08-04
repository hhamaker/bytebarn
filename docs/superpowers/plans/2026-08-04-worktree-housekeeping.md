# Worktree Housekeeping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When an isolated session is deleted, offer to remove its git worktree and branch, saying first how many commits that would destroy.

**Architecture:** The engine answers "what would I lose?" and performs removal; the UI asks. No new persisted state — everything derives from git plus the existing `session.worktree_branch` column. "Merged" means *reachable from some other ref*, computed by enumerating every ref except this branch and asking `git rev-list` what remains.

**Tech Stack:** Python 3.12, asyncio, aiosqlite, PySide6 + qasync, pytest with `asyncio_mode = "auto"`.

**Spec:** `docs/superpowers/specs/2026-08-04-worktree-housekeeping-design.md`

## Global Constraints

- **Zero Qt in `bytebarn/engine/`.** Nothing under that package may import PySide6. Enforced by `tests/engine/test_no_qt_in_engine.py`, which imports the engine with PySide6 poisoned.
- Tests run offscreen with no network and no API keys: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest`. The interpreter is `.venv/bin/pytest`, never bare `pytest`.
- `asyncio_mode = "auto"` — write `async def test_...` directly, no `@pytest.mark.asyncio`.
- CI runs `ruff check .` (config in `pyproject.toml`: `select = ["E4","E7","E9","F"]`). Ruff is **not** in `[dev]`, so it is not in `.venv` — run `pipx run ruff check .`, which is what CI does. A lint failure fails the build.
- No formatter. Match surrounding style: `from __future__ import annotations`, docstrings that state intent.
- **Cleanup must never block a delete.** If worktree removal fails for any reason, the session is still deleted and the failure is reported. Losing a session row because `git worktree remove` failed is the worse outcome; the worktree is recoverable by hand, the row is not.
- `session.worktree_branch != ""` is the discriminator for "isolated". Never `directory == ""` — ordinary top-level sessions carry a non-empty `directory`.
- Existing behaviour that must not regress: every current caller of `Engine.delete_session` keeps today's behaviour (worktree untouched), and non-isolated deletes show exactly one confirmation dialog, as now.

---

### Task 1: Ask git what a branch deletion would cost

**Files:**
- Modify: `bytebarn/engine/worktree.py` — add two module-level functions after `head_commit` (~line 80); change `_force_remove` (~line 304) into a public `discard`
- Modify: `bytebarn/engine/facade.py` — the one `_force_remove` call site (~line 183)
- Test: `tests/engine/test_worktree.py`

**Interfaces:**
- Consumes: nothing from earlier tasks. `_git(cwd, *args, timeout=30.0) -> tuple[int, str]` already exists in `worktree.py` and is the house style for shelling out to git.
- Produces:
  - `async def unmerged_count(git_root: Path, branch: str) -> int` — commits reachable only from `branch`. `0` when git fails or the branch does not exist.
  - `async def discard(git_root: Path, path: Path, branch: str) -> None` — module-level. Removes the worktree and deletes the branch when it starts with `bytebarn/`.
  - `WorktreeManager._force_remove` is gone; `WorktreeManager.remove` and `_active` cleanup call `discard`.

**Critical background — do not use the obvious command.** An earlier draft of the spec used:

```
git rev-list --count refs/heads/<branch> --not --exclude=refs/heads/<branch> --all
```

This is wrong. `--all` means "all refs in `refs/`, **along with HEAD**", and `--exclude` filters refs by glob without touching HEAD. An isolated session's branch is checked out in its worktree, so a HEAD still points at it and every commit looks reachable. Verified against a real repo: branch checked out in a linked worktree with two commits on it returns `0`. It reports "nothing to lose" in exactly the case this feature exists for.

Enumerate the other refs explicitly. The empty case (branch is the repo's only ref) leaves a trailing `--not` with no arguments, which git accepts and which correctly counts every commit on the branch.

- [ ] **Step 1: Write the failing tests**

Add to `tests/engine/test_worktree.py`. The file already has `_git` and `_init_repo` helpers — reuse them.

```python
async def test_unmerged_count_sees_commits_only_on_this_branch(tmp_path):
    """The branch is checked out in a linked worktree, as a real one always is."""
    from bytebarn.engine.worktree import unmerged_count

    repo = tmp_path / "repo"
    _init_repo(repo)
    wt_path = tmp_path / "wt"
    _git(repo, "worktree", "add", "-b", "bytebarn/session-aaa", str(wt_path), "HEAD")
    (wt_path / "b.txt").write_text("b\n")
    _git(wt_path, "add", ".")
    _git(wt_path, "commit", "-m", "w1")
    (wt_path / "c.txt").write_text("c\n")
    _git(wt_path, "add", ".")
    _git(wt_path, "commit", "-m", "w2")

    assert await unmerged_count(repo, "bytebarn/session-aaa") == 2


async def test_unmerged_count_is_zero_once_merged(tmp_path):
    from bytebarn.engine.worktree import unmerged_count

    repo = tmp_path / "repo"
    _init_repo(repo)
    wt_path = tmp_path / "wt"
    _git(repo, "worktree", "add", "-b", "bytebarn/session-bbb", str(wt_path), "HEAD")
    (wt_path / "b.txt").write_text("b\n")
    _git(wt_path, "add", ".")
    _git(wt_path, "commit", "-m", "w1")

    assert await unmerged_count(repo, "bytebarn/session-bbb") == 1
    _git(repo, "merge", "--no-ff", "-m", "merge", "bytebarn/session-bbb")
    assert await unmerged_count(repo, "bytebarn/session-bbb") == 0


async def test_unmerged_count_is_zero_for_a_missing_branch(tmp_path):
    """A warning we cannot substantiate is noise — never raise here."""
    from bytebarn.engine.worktree import unmerged_count

    repo = tmp_path / "repo"
    _init_repo(repo)
    assert await unmerged_count(repo, "bytebarn/session-nope") == 0


async def test_discard_removes_worktree_and_branch(tmp_path):
    from bytebarn.engine.worktree import discard

    repo = tmp_path / "repo"
    _init_repo(repo)
    wt_path = tmp_path / "wt"
    _git(repo, "worktree", "add", "-b", "bytebarn/session-ccc", str(wt_path), "HEAD")
    assert wt_path.is_dir()

    await discard(repo, wt_path, "bytebarn/session-ccc")

    assert not wt_path.exists()
    branches = _git(repo, "branch", "--list", "bytebarn/session-ccc")
    assert branches.strip() == ""


async def test_discard_keeps_a_branch_it_did_not_create(tmp_path):
    """Only throwaway bytebarn/ branches are deleted — same guard as before."""
    from bytebarn.engine.worktree import discard

    repo = tmp_path / "repo"
    _init_repo(repo)
    wt_path = tmp_path / "wt"
    _git(repo, "worktree", "add", "-b", "feature/mine", str(wt_path), "HEAD")

    await discard(repo, wt_path, "feature/mine")

    assert not wt_path.exists()
    assert "feature/mine" in _git(repo, "branch", "--list", "feature/mine")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/engine/test_worktree.py -k "unmerged_count or discard" -v`
Expected: FAIL — `ImportError: cannot import name 'unmerged_count'` and `'discard'`.

- [ ] **Step 3: Implement `unmerged_count`**

Add after `head_commit` in `bytebarn/engine/worktree.py`:

```python
async def unmerged_count(git_root: Path, branch: str) -> int:
    """Commits reachable only from ``branch`` — what deleting it would lose.

    Reachability from every other ref, not a comparison against a default
    branch: the work may have landed by merge, rebase, squash, or cherry-pick.

    Deliberately not ``--exclude=<ref> --all``: ``--all`` includes HEAD, which
    ``--exclude`` does not filter, and a session branch is checked out in its
    own worktree — so that spelling reports 0 every time.

    Returns 0 when git fails or the branch is gone. The caller is deciding
    whether to warn, and a warning we cannot substantiate is noise.
    """
    ref = f"refs/heads/{branch}"
    code, out = await _git(git_root, "for-each-ref", "--format=%(refname)")
    if code != 0:
        return 0
    others = [line for line in out.splitlines() if line.strip() and line.strip() != ref]
    code, out = await _git(git_root, "rev-list", "--count", ref, "--not", *others)
    if code != 0:
        return 0
    try:
        return int(out.strip())
    except ValueError:
        return 0
```

- [ ] **Step 4: Turn `_force_remove` into a public `discard`**

`WorktreeManager._force_remove` becomes a module-level function so the facade can call it without reaching into a private. Add next to `unmerged_count`:

```python
async def discard(git_root: Path, path: Path, branch: str) -> None:
    """Remove a worktree and the throwaway branch that came with it.

    Falls back to a plain tree delete plus ``worktree prune`` when git refuses,
    so a half-removed worktree cannot wedge the caller.
    """
    if path.exists():
        code, _ = await _git(git_root, "worktree", "remove", "--force", str(path), timeout=60.0)
        if code != 0 and path.exists():
            shutil.rmtree(path, ignore_errors=True)
            await _git(git_root, "worktree", "prune")
    # drop the throwaway branch if we created one
    if branch.startswith("bytebarn/"):
        await _git(git_root, "branch", "-D", branch)
```

Then delete the `_force_remove` method from `WorktreeManager` and point its two internal callers at the new function — the leftover-cleanup line inside `create` and the body of `remove`:

```python
    async def remove(self, session_id: str) -> None:
        wt = self._active.pop(session_id, None)
        if wt is None:
            return
        await discard(wt.git_root, wt.path, wt.branch)
```

Find the other call inside `create` (the `# leftover from a crashed run — drop it` line) and change it the same way.

- [ ] **Step 5: Update the facade's call site**

`bytebarn/engine/facade.py` calls `self.worktrees._force_remove(...)` in `_isolate_session`'s store-failure teardown. Change it to the module function, importing it locally the way the file already imports `dirty_files`:

```python
            try:
                from .worktree import discard

                await discard(wt.git_root, wt.path, wt.branch)
            except Exception:
```

Leave the surrounding try/except and the comments explaining the swallow exactly as they are — that structure was the subject of a prior review round.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/engine/test_worktree.py -v`
Expected: PASS, including every pre-existing test in the file — those cover subagent worktree create/apply/remove, which now route through `discard`.

- [ ] **Step 7: Check for stragglers and lint**

```bash
rg -n "_force_remove" bytebarn/ tests/
pipx run ruff check .
```
Expected: no matches for `_force_remove`; ruff clean.

- [ ] **Step 8: Run the full suite**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add bytebarn/engine/worktree.py bytebarn/engine/facade.py tests/engine/test_worktree.py
git commit -m "Ask git what deleting a session branch would cost"
```

---

### Task 2: Expose worktree cost and removal on the facade

**Files:**
- Modify: `bytebarn/engine/facade.py` — add `session_worktree_info` next to `delete_session` (~line 243), extend `delete_session`
- Test: `tests/engine/test_worktree.py`

**Interfaces:**
- Consumes: `unmerged_count(git_root, branch)` and `discard(git_root, path, branch)` from Task 1. Also `git_root(cwd) -> Path | None`, already in `worktree.py`.
- Produces:
  - `async def session_worktree_info(session_id: str) -> dict | None` — `None` when the session has no `worktree_branch`. Otherwise `{"branch": str, "path": str, "unmerged": int, "exists": bool}`.
  - `async def delete_session(session_id: str, discard_worktree: bool = False) -> None`

- [ ] **Step 1: Write the failing tests**

Add to `tests/engine/test_worktree.py`. The `engine` fixture in that file provides an Engine rooted in an initialised git repo.

```python
async def test_session_worktree_info_describes_the_cost(engine):
    session = await engine.new_session(isolated=True)
    assert session.worktree_branch
    worktree = Path(session.directory)

    info = await engine.session_worktree_info(session.id)
    assert info["branch"] == session.worktree_branch
    assert info["path"] == session.directory
    assert info["exists"] is True
    assert info["unmerged"] == 0          # nothing committed in it yet

    (worktree / "work.txt").write_text("agent output\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "agent work")

    info = await engine.session_worktree_info(session.id)
    assert info["unmerged"] == 1


async def test_session_worktree_info_is_none_for_a_plain_session(engine):
    session = await engine.new_session()
    assert await engine.session_worktree_info(session.id) is None


async def test_session_worktree_info_reports_a_deleted_directory(engine):
    session = await engine.new_session(isolated=True)
    shutil.rmtree(session.directory)

    info = await engine.session_worktree_info(session.id)
    assert info["exists"] is False
    assert info["branch"] == session.worktree_branch


async def test_delete_session_keeps_the_worktree_by_default(engine):
    session = await engine.new_session(isolated=True)
    worktree = Path(session.directory)

    await engine.delete_session(session.id)

    assert worktree.is_dir()
    assert await engine.store.get_session(session.id) is None


async def test_delete_session_can_discard_the_worktree(engine):
    session = await engine.new_session(isolated=True)
    worktree = Path(session.directory)
    branch = session.worktree_branch

    await engine.delete_session(session.id, discard_worktree=True)

    assert not worktree.exists()
    assert branch not in _git(engine.project_dir, "branch", "--list", branch)
    assert await engine.store.get_session(session.id) is None


async def test_delete_session_survives_a_failing_discard(engine, monkeypatch):
    """A failed cleanup must never cost the user their session row."""
    session = await engine.new_session(isolated=True)

    async def _boom(*args, **kwargs):
        raise OSError("worktree removal exploded")

    monkeypatch.setattr("bytebarn.engine.worktree.discard", _boom)

    await engine.delete_session(session.id, discard_worktree=True)

    assert await engine.store.get_session(session.id) is None
```

`Path` is already imported at the top of that test file. **`shutil` is not** — add `import shutil` to the existing stdlib import block (`asyncio`, `json`, `subprocess`, then `from pathlib import Path`), keeping it alphabetical.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/engine/test_worktree.py -k "worktree_info or delete_session" -v`
Expected: FAIL — `AttributeError: 'Engine' object has no attribute 'session_worktree_info'`, and `TypeError` for the unexpected `discard_worktree` keyword.

- [ ] **Step 3: Implement `session_worktree_info`**

Add above `delete_session` in `bytebarn/engine/facade.py`:

```python
    async def session_worktree_info(self, session_id: str) -> dict | None:
        """What deleting this session's worktree would cost, or None.

        ``None`` means the session is not isolated and there is nothing to ask
        the user about. ``unmerged`` is the number of commits that exist only
        on this branch; ``exists`` is False when the directory has already been
        removed by hand, in which case only the branch is left to clean up.
        """
        from .worktree import git_root, unmerged_count

        session = await self.store.get_session(session_id)
        if session is None or not session.worktree_branch:
            return None
        path = Path(session.directory) if session.directory else None
        root = await git_root(self.project_dir)
        unmerged = 0
        if root is not None:
            unmerged = await unmerged_count(root, session.worktree_branch)
        return {
            "branch": session.worktree_branch,
            "path": str(path) if path else "",
            "unmerged": unmerged,
            "exists": bool(path and path.is_dir()),
        }
```

- [ ] **Step 4: Extend `delete_session`**

```python
    async def delete_session(
        self, session_id: str, discard_worktree: bool = False
    ) -> None:
        """Permanently remove a session, its children, and their history.

        ``discard_worktree`` also removes an isolated session's worktree and
        branch. Opt-in, because that destroys any commits living only there.
        Cleanup failure never blocks the delete: a worktree left behind can be
        removed by hand, a session row cannot be brought back.
        """
        await self.abort(session_id)
        if discard_worktree:
            await self._discard_session_worktree(session_id)
        await self.store.delete_session(session_id)
        self._files_read.pop(session_id, None)
        self._runs.pop(session_id, None)
        self.bus.emit(SessionUpdated(session_id=session_id))

    async def _discard_session_worktree(self, session_id: str) -> None:
        """Best-effort worktree teardown — never raises into the delete path."""
        from . import worktree as worktree_mod

        try:
            session = await self.store.get_session(session_id)
            if session is None or not session.worktree_branch:
                return
            root = await worktree_mod.git_root(self.project_dir)
            if root is None:
                return
            await worktree_mod.discard(
                root, Path(session.directory), session.worktree_branch)
        except Exception:
            # reported by the caller from session_worktree_info; a cleanup
            # failure must not cost the user the session row
            pass
```

Note the module-style import (`from . import worktree as worktree_mod`, then `worktree_mod.discard`) rather than `from .worktree import discard`. The failing-discard test monkeypatches `bytebarn.engine.worktree.discard`, and a name imported directly into the facade would not see the patch.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/engine/test_worktree.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite and lint**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/pytest
pipx run ruff check .
```
Expected: PASS, ruff clean. Every existing `delete_session` caller omits the new argument and keeps today's behaviour.

- [ ] **Step 7: Commit**

```bash
git add bytebarn/engine/facade.py tests/engine/test_worktree.py
git commit -m "Expose worktree cost and opt-in removal on the facade"
```

---

### Task 3: Ask before destroying agent output

**Files:**
- Modify: `bytebarn/app/main_window.py` — add `_ask_three_way` next to `_ask_yes_no` (~line 845); rewrite `_delete_session` and `_delete_sessions` (~line 1018)
- Modify: `CLAUDE.md` — the **isolated session** glossary entry
- Test: `tests/app/test_ui_smoke.py`

**Interfaces:**
- Consumes: `Engine.session_worktree_info(session_id) -> dict | None` and `Engine.delete_session(session_id, discard_worktree=False)` from Task 2.
- Produces: `MainWindow._ask_three_way(title, text, keep_label, remove_label) -> asyncio.Future[str | None]`, resolving to `"remove"`, `"keep"`, or `None` for cancel.

Context the implementer needs:

- `SessionList._confirm_delete` already asks "permanently delete?" **synchronously**, from the context-menu handler, before emitting the signal that reaches `MainWindow`. It has no engine access and cannot learn anything about worktrees. So an isolated delete shows **two dialogs in sequence** — that is intended and recorded in the spec, not an oversight. Do not try to merge them.
- `_delete_session` and `_delete_sessions` run as asyncio tasks. Opening a modal directly inside one makes qasync warn about task re-entry, which is why `_ask_yes_no` bounces through `QTimer.singleShot(0, ...)` and resolves a future. `_ask_three_way` follows that pattern exactly; it exists separately because three buttons cannot come from `_ask_yes_no`'s Ok/Cancel pair.
- `tests/app/test_ui_smoke.py` has a `_iso_window(tmp_path, qapp, git=True)` helper returning `(window, engine)`, and an `_auto_answer(window, answer, seen)` helper that replaces the modal with a recorded instant verdict. Read both before writing tests and follow their shape.

- [ ] **Step 1: Write the failing tests**

```python
async def test_deleting_an_isolated_session_offers_to_remove_the_worktree(
    qapp, tmp_path
):
    window, engine = await _iso_window(tmp_path, qapp)
    try:
        session = await engine.new_session(isolated=True)
        worktree = Path(session.directory)
        asked: list = []

        def _fake(title, text, keep_label, remove_label):
            asked.append(text)
            fut = asyncio.get_event_loop().create_future()
            fut.set_result("remove")
            return fut

        window._ask_three_way = _fake
        await window._delete_session(session.id)

        assert asked, "no worktree dialog was shown"
        assert session.worktree_branch in asked[0]
        assert not worktree.exists()
        assert await engine.store.get_session(session.id) is None
    finally:
        await engine.stop()


async def test_keeping_the_worktree_still_deletes_the_session(qapp, tmp_path):
    window, engine = await _iso_window(tmp_path, qapp)
    try:
        session = await engine.new_session(isolated=True)
        worktree = Path(session.directory)

        def _fake(title, text, keep_label, remove_label):
            fut = asyncio.get_event_loop().create_future()
            fut.set_result("keep")
            return fut

        window._ask_three_way = _fake
        await window._delete_session(session.id)

        assert worktree.is_dir()
        assert await engine.store.get_session(session.id) is None
    finally:
        await engine.stop()


async def test_cancelling_the_worktree_dialog_deletes_nothing(qapp, tmp_path):
    window, engine = await _iso_window(tmp_path, qapp)
    try:
        session = await engine.new_session(isolated=True)
        worktree = Path(session.directory)

        def _fake(title, text, keep_label, remove_label):
            fut = asyncio.get_event_loop().create_future()
            fut.set_result(None)
            return fut

        window._ask_three_way = _fake
        await window._delete_session(session.id)

        assert worktree.is_dir()
        assert await engine.store.get_session(session.id) is not None
    finally:
        await engine.stop()


async def test_deleting_a_plain_session_shows_no_worktree_dialog(qapp, tmp_path):
    window, engine = await _iso_window(tmp_path, qapp)
    try:
        session = await engine.new_session()
        asked: list = []

        def _fake(title, text, keep_label, remove_label):
            asked.append(text)
            fut = asyncio.get_event_loop().create_future()
            fut.set_result("keep")
            return fut

        window._ask_three_way = _fake
        await window._delete_session(session.id)

        assert not asked
        assert await engine.store.get_session(session.id) is None
    finally:
        await engine.stop()


async def test_multi_delete_asks_once_and_lists_every_worktree(qapp, tmp_path):
    """One dialog for the whole selection — a prompt per session is worse."""
    window, engine = await _iso_window(tmp_path, qapp)
    try:
        first = await engine.new_session(isolated=True)
        second = await engine.new_session(isolated=True)
        plain = await engine.new_session()
        asked: list = []

        def _fake(title, text, keep_label, remove_label):
            asked.append(text)
            fut = asyncio.get_event_loop().create_future()
            fut.set_result("remove")
            return fut

        window._ask_three_way = _fake
        await window._delete_sessions([first.id, second.id, plain.id])

        assert len(asked) == 1
        assert first.worktree_branch in asked[0]
        assert second.worktree_branch in asked[0]
        assert not Path(first.directory).exists()
        assert not Path(second.directory).exists()
        for sid in (first.id, second.id, plain.id):
            assert await engine.store.get_session(sid) is None
    finally:
        await engine.stop()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/app/test_ui_smoke.py -k "worktree_dialog or isolated_session_offers or keeping_the_worktree or cancelling_the_worktree or multi_delete_asks" -v`
Expected: FAIL — `_delete_session` ignores `_ask_three_way` entirely, so `asked` stays empty and the worktree survives a `"remove"` answer.

- [ ] **Step 3: Add the three-way modal helper**

Add directly below `_ask_yes_no` in `bytebarn/app/main_window.py`:

```python
    def _ask_three_way(
        self, title: str, text: str, keep_label: str, remove_label: str
    ):
        """Await a three-button modal from an asyncio task.

        Same qasync task re-entry dodge as `_ask_yes_no` — the dialog opens on
        the next Qt loop turn and resolves a future. Resolves to "remove",
        "keep", or None for cancel.
        """
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QMessageBox

        fut = asyncio.get_event_loop().create_future()

        def _show() -> None:
            box = QMessageBox(QMessageBox.Warning, title, text, parent=self)
            remove = box.addButton(remove_label, QMessageBox.DestructiveRole)
            keep = box.addButton(keep_label, QMessageBox.AcceptRole)
            box.addButton(QMessageBox.Cancel)
            box.setDefaultButton(keep)
            box.exec()
            clicked = box.clickedButton()
            answer = "remove" if clicked is remove else "keep" if clicked is keep else None
            if not fut.done():
                fut.set_result(answer)

        QTimer.singleShot(0, _show)
        return fut
```

Keep is the default button: the dialog fires when work may be at stake, and Enter should not destroy commits.

- [ ] **Step 4: Wire the delete paths**

Replace `_delete_session` and `_delete_sessions`:

```python
    async def _delete_session(self, session_id: str) -> None:
        answer = await self._worktree_verdict([session_id])
        if answer is None:
            return
        await self.engine.delete_session(
            session_id, discard_worktree=answer == "remove")
        await self._after_session_removed(session_id)

    async def _delete_sessions(self, session_ids: list[str]) -> None:
        answer = await self._worktree_verdict(session_ids)
        if answer is None:
            return
        for sid in session_ids:
            await self.engine.delete_session(
                sid, discard_worktree=answer == "remove")
        for sid in session_ids:
            self._session_stack = [s for s in self._session_stack if s != sid]
        if self.current_session_id in session_ids:
            await self._after_session_removed(self.current_session_id or "")
        else:
            await self._refresh_sessions()

    async def _worktree_verdict(self, session_ids: list[str]) -> str | None:
        """Ask what to do with any worktrees in this selection.

        Returns "remove", "keep", or None to cancel the delete entirely. A
        selection with no isolated sessions answers "keep" without asking —
        SessionList already took the "permanently delete?" confirmation.
        """
        infos = []
        for sid in session_ids:
            info = await self.engine.session_worktree_info(sid)
            if info is not None:
                infos.append(info)
        if not infos:
            return "keep"

        lines = []
        for info in infos:
            if info["unmerged"]:
                plural = "" if info["unmerged"] == 1 else "s"
                detail = (f"{info['unmerged']} commit{plural} not on any"
                          f" other branch")
            else:
                detail = "fully merged"
            if not info["exists"]:
                detail += ", directory already gone"
            lines.append(f"  {info['branch']} — {detail}")

        what = "this session" if len(session_ids) == 1 else "these sessions"
        body = (f"Remove the git worktree{'s' if len(infos) > 1 else ''} for"
                f" {what} too?\n\n" + "\n".join(lines))
        return await self._ask_three_way(
            "Delete worktrees", body, "Keep worktrees", "Remove worktrees")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/app/test_ui_smoke.py -v`
Expected: PASS, including the pre-existing delete tests in that file.

- [ ] **Step 6: Update the glossary**

In `CLAUDE.md`, the **isolated session** entry ends "never auto-applied or auto-removed, merge it in git." Extend it:

```markdown
  `session.worktree_branch`; never auto-applied or auto-removed, merge it in
  git. Deleting the session offers to remove the worktree and branch, saying
  first how many commits exist only there.
```

- [ ] **Step 7: Run the full suite and lint**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/pytest
pipx run ruff check .
```
Expected: PASS, ruff clean.

- [ ] **Step 8: Verify in the real app**

Use the project's `verify` skill (offscreen GUI driving) against a **disposable temp git repo**, never this checkout. Create an isolated session, commit something inside its worktree, then delete the session and confirm the dialog names the branch and the commit count, that "Keep" leaves `git worktree list` unchanged, and that "Remove" clears both worktree and branch.

- [ ] **Step 9: Commit**

```bash
git add bytebarn/app/main_window.py tests/app/test_ui_smoke.py CLAUDE.md
git commit -m "Ask before destroying an isolated session's worktree"
```

---

## Self-Review Notes

Spec coverage against `docs/superpowers/specs/2026-08-04-worktree-housekeeping-design.md`:

| Spec requirement | Task |
|---|---|
| `unmerged_count` by ref reachability, not vs `main` | 1 |
| Explicit ref enumeration, not `--exclude ... --all` | 1 (with the failing-command rationale) |
| `unmerged_count` returns 0 on failure / missing branch | 1 |
| Public `discard`, retiring `_force_remove` private access | 1 |
| `session_worktree_info` returning branch/path/unmerged/exists | 2 |
| `delete_session(discard_worktree=False)` opt-in | 2 |
| Cleanup never blocks the delete | 2 (`_discard_session_worktree` swallows) |
| One dialog for any selection size | 3 |
| Three-way Keep / Remove / Cancel | 3 |
| Non-isolated deletes unchanged | 3 (`_worktree_verdict` returns "keep" silently) |
| `exists: False` offers branch-only cleanup | 2 + 3 (reported in the dialog line) |
| Archive untouched | all — no task modifies `close_session` |
| Disk size deliberately omitted | all — no task computes it |

Two things a reviewer should know rather than flag:

1. **Two dialogs for an isolated delete.** `SessionList._confirm_delete` is synchronous and engine-free; merging it with the worktree question would mean moving delete confirmation into `MainWindow`. Recorded in the spec, out of scope here.
2. **The module-style import in `_discard_session_worktree`.** `from . import worktree as worktree_mod` rather than `from .worktree import discard`, so the failing-discard test's monkeypatch of `bytebarn.engine.worktree.discard` is visible. A direct name import would silently bypass the patch and make that test vacuous.
