# Isolated Sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a top-level ByteBarn session run inside its own git worktree, so `/goal` can work on the ByteBarn repo without the live working tree changing while the app is running.

**Architecture:** Reuse the existing `WorktreeManager` that already isolates subagents. Give a top-level session a worktree and store its path in the existing `session.directory` column plus a new `session.worktree_branch` column. Because `Engine.run_subagent` already derives `parent_cwd` from `parent.directory`, every subagent of an isolated session nests inside that worktree and merges back into it — the live repo is never a merge target, and `run_subagent` needs no changes. Session worktrees are never auto-applied and never auto-removed; the user merges the branch in git.

**Tech Stack:** Python 3.12, asyncio, aiosqlite (SQLite WAL), PySide6 + qasync, pytest with `asyncio_mode = "auto"`.

**Spec:** `docs/superpowers/specs/2026-08-04-isolated-sessions-design.md`

## Global Constraints

- **Zero Qt in the engine.** Nothing under `bytebarn/engine/` may import PySide6. Enforced by `tests/engine/test_no_qt_in_engine.py`.
- **Tests run offscreen, no network, no API keys.** Every test command in this plan is prefixed `QT_QPA_PLATFORM=offscreen`.
- Full suite: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest`
- Engine tests use `FakeProvider` (`bytebarn/engine/providers/fake.py`) and `tmp_path` for the store and global dir. `tests/engine/test_worktree.py` already has the git-repo fixture pattern this plan builds on.
- `asyncio_mode = "auto"` — write `async def test_...` directly, no `@pytest.mark.asyncio`.
- No linter is configured. Match surrounding style: `from __future__ import annotations`, docstrings that cite intent.
- Never rewrite config files wholesale; programmatic config writes go through `patch_config_file`. (No task here writes config, but the rule stands.)
- Existing behaviour that must not regress: subagent worktrees are still created, applied back, and removed exactly as they are today.

---

### Task 1: Persist the worktree branch on a session

The session row needs to remember that it is isolated and on which branch, so the fact survives an app restart and the sidebar can render it without sniffing filesystem paths.

**Files:**
- Modify: `bytebarn/engine/store.py` — `_SCHEMA` session table (~line 40), `Session` dataclass (~line 132), `open()` migration block (~line 198), `create_session` (~line 365), `_session` row hydration (~line 770)
- Test: `tests/engine/test_store.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `Session.worktree_branch: str` — `""` for a normal session, e.g. `"bytebarn/session-a1b2c3d4"` for an isolated one.
  - `Store.create_session(..., worktree_branch: str = "")` keyword.
  - `Store.update_session(session_id, worktree_branch=...)` already works — it takes `**fields` and builds the `SET` clause dynamically, so no change is needed there.

- [ ] **Step 1: Write the failing test**

Add to `tests/engine/test_store.py`:

`tests/engine/test_store.py` already has an `async def store(tmp_path)` fixture at line 7 that opens (and tears down) a `Store` — the first test uses it. The migration test needs a hand-built legacy DB, so it opens its own.

```python
async def test_session_worktree_branch_roundtrip(store, tmp_path):
    project = await store.open_project(str(tmp_path))

    plain = await store.create_session(project.id)
    assert plain.worktree_branch == ""

    isolated = await store.create_session(
        project.id, worktree_branch="bytebarn/session-a1b2c3d4")
    assert isolated.worktree_branch == "bytebarn/session-a1b2c3d4"

    fetched = await store.get_session(isolated.id)
    assert fetched.worktree_branch == "bytebarn/session-a1b2c3d4"

    await store.update_session(plain.id, worktree_branch="bytebarn/session-deadbeef")
    again = await store.get_session(plain.id)
    assert again.worktree_branch == "bytebarn/session-deadbeef"


async def test_session_worktree_branch_migrates_old_db(tmp_path):
    """A DB created before the column exists must gain it on open()."""
    import sqlite3

    from bytebarn.engine.store import Store

    db_path = tmp_path / "old.db"
    con = sqlite3.connect(db_path)
    con.executescript(
        """
        CREATE TABLE project (
            id TEXT PRIMARY KEY, path TEXT NOT NULL, name TEXT NOT NULL,
            last_opened_at REAL NOT NULL
        );
        CREATE TABLE session (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES project(id),
            parent_session_id TEXT REFERENCES session(id),
            title TEXT NOT NULL DEFAULT '',
            agent TEXT NOT NULL DEFAULT 'build',
            model TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            archived INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO project VALUES ('p1', '/tmp/x', 'x', 0);
        INSERT INTO session (id, project_id, created_at, updated_at)
            VALUES ('s1', 'p1', 0, 0);
        """
    )
    con.commit()
    con.close()

    store = Store(db_path)
    await store.open()
    session = await store.get_session("s1")
    assert session is not None
    assert session.worktree_branch == ""
    await store.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/engine/test_store.py -k worktree_branch -v`
Expected: FAIL — `AttributeError: 'Session' object has no attribute 'worktree_branch'`, and a `TypeError` for the unexpected `worktree_branch` keyword.

- [ ] **Step 3: Add the column to the schema**

In `bytebarn/engine/store.py`, in the `session` table inside `_SCHEMA`, add the column immediately after `permission_mode TEXT`:

```sql
CREATE TABLE IF NOT EXISTS session (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project(id),
    parent_session_id TEXT REFERENCES session(id),
    title TEXT NOT NULL DEFAULT '',
    agent TEXT NOT NULL DEFAULT 'build',
    model TEXT NOT NULL DEFAULT '',
    directory TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0,
    permission_mode TEXT,
    worktree_branch TEXT NOT NULL DEFAULT ''
);
```

- [ ] **Step 4: Add the dataclass field**

`worktree_branch` goes last so every existing positional `Session(...)` construction keeps working:

```python
@dataclass
class Session:
    id: str
    project_id: str
    parent_session_id: str | None
    title: str
    agent: str
    model: str
    created_at: float
    updated_at: float
    archived: bool
    directory: str = ""   # per-session working dir ('' = project default)
    permission_mode: str | None = None
    worktree_branch: str = ""   # git branch when the session owns a worktree
```

- [ ] **Step 5: Add the migration**

In `Store.open()`, extend the existing session-column migration block (the one that already backfills `directory` and `permission_mode`):

```python
        if "permission_mode" not in cols:
            await self._db.execute(
                "ALTER TABLE session ADD COLUMN permission_mode TEXT")
        # migration: sessions gained a worktree branch (isolated sessions)
        if "worktree_branch" not in cols:
            await self._db.execute(
                "ALTER TABLE session ADD COLUMN worktree_branch TEXT NOT NULL DEFAULT ''")
```

- [ ] **Step 6: Thread it through create_session and row hydration**

```python
    async def create_session(
        self,
        project_id: str,
        agent: str = "build",
        model: str = "",
        parent_session_id: str | None = None,
        title: str = "",
        directory: str = "",
        permission_mode: str | None = None,
        worktree_branch: str = "",
    ) -> Session:
        now = time.time()
        sid = _id()
        await self.db.execute(
            "INSERT INTO session (id, project_id, parent_session_id, title, agent, model,"
            " directory, created_at, updated_at, archived, permission_mode, worktree_branch)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, project_id, parent_session_id, title, agent, model, directory, now, now,
             0, permission_mode, worktree_branch),
        )
        await self.db.commit()
        return Session(sid, project_id, parent_session_id, title, agent, model,
                       now, now, False, directory, permission_mode, worktree_branch)
```

And in `_session`, mirroring how `directory` and `permission_mode` already guard against older rows:

```python
    @staticmethod
    def _session(r: aiosqlite.Row) -> Session:
        directory = r["directory"] if "directory" in r.keys() else ""
        pmode = r["permission_mode"] if "permission_mode" in r.keys() else None
        branch = r["worktree_branch"] if "worktree_branch" in r.keys() else ""
        return Session(r["id"], r["project_id"], r["parent_session_id"], r["title"], r["agent"],
                       r["model"], r["created_at"], r["updated_at"], bool(r["archived"]),
                       directory, pmode, branch)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/engine/test_store.py -v`
Expected: PASS, including the two new tests and every pre-existing store test.

- [ ] **Step 8: Run the full suite**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest`
Expected: PASS. The schema change touches every session read, so a green full suite is the gate here.

- [ ] **Step 9: Commit**

```bash
git add bytebarn/engine/store.py tests/engine/test_store.py
git commit -m "Persist a worktree branch on sessions"
```

---

### Task 2: Untracked worktrees and a dirty-repo probe

`WorktreeManager` currently owns every worktree it creates and can remove it. A session worktree must be creatable *without* being owned, so no subagent-cleanup path can ever delete it. The UI also needs to know whether the live repo has uncommitted work before offering isolation.

**Files:**
- Modify: `bytebarn/engine/worktree.py` — `WorktreeManager.create` (~line 91), plus a new module-level `dirty_files`
- Test: `tests/engine/test_worktree.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `WorktreeManager.create(session_id, parent_cwd, project_key="default", *, track: bool = True, branch: str | None = None) -> Worktree | None` — `track=False` returns the worktree without registering it in `_active`; `branch` overrides the default `f"bytebarn/{session_id[:12]}"`.
  - `async def dirty_files(root: Path) -> list[str]` — module-level in `bytebarn/engine/worktree.py`. Uncommitted paths (modified, staged, untracked) relative to the git root. `[]` when `root` is not a git repo or git fails.

- [ ] **Step 1: Write the failing tests**

Add to `tests/engine/test_worktree.py` (the file already defines `_git` and `_init_repo` helpers — reuse them):

```python
async def test_create_untracked_is_not_removable(tmp_path):
    """track=False keeps the manager from ever owning (and deleting) it."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    mgr = WorktreeManager(tmp_path / "worktrees")

    wt = await mgr.create(
        "sess1234abcd", repo, project_key="p",
        track=False, branch="bytebarn/session-sess1234",
    )
    assert wt is not None
    assert wt.branch == "bytebarn/session-sess1234"
    assert wt.path.is_dir()
    assert (wt.path / "README.md").is_file()

    # the manager does not know about it, so cleanup calls are no-ops
    assert mgr.get("sess1234abcd") is None
    await mgr.remove("sess1234abcd")
    assert await mgr.apply_and_remove("sess1234abcd") is None
    assert wt.path.is_dir()


async def test_create_tracked_still_default(tmp_path):
    """Subagent behaviour is unchanged: tracked, default branch name."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    mgr = WorktreeManager(tmp_path / "worktrees")

    wt = await mgr.create("abcdefghijklmnop", repo, project_key="p")
    assert wt is not None
    assert wt.branch == "bytebarn/abcdefghijkl"
    assert mgr.get("abcdefghijklmnop") is wt

    await mgr.remove("abcdefghijklmnop")
    assert not wt.path.exists()


async def test_dirty_files_reports_modified_and_untracked(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    from bytebarn.engine.worktree import dirty_files

    assert await dirty_files(repo) == []

    (repo / "README.md").write_text("changed\n")
    (repo / "brand_new.txt").write_text("new\n")
    found = set(await dirty_files(repo))
    assert found == {"README.md", "brand_new.txt"}


async def test_dirty_files_on_non_git_dir(tmp_path):
    from bytebarn.engine.worktree import dirty_files

    plain = tmp_path / "plain"
    plain.mkdir()
    assert await dirty_files(plain) == []
```

Note: `test_dirty_files_on_non_git_dir` assumes `tmp_path` is not inside a git repo. On macOS `tmp_path` lives under `/private/var/folders/...`, which is not a repo, so this holds. If the assertion ever fails because the temp root is inside one, keep the test and set `GIT_CEILING_DIRECTORIES` to `str(tmp_path)` in the subprocess environment rather than deleting the test.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/engine/test_worktree.py -k "untracked or tracked_still or dirty_files" -v`
Expected: FAIL — `TypeError: create() got an unexpected keyword argument 'track'` and `ImportError: cannot import name 'dirty_files'`.

- [ ] **Step 3: Add the `track` and `branch` parameters**

In `bytebarn/engine/worktree.py`, change the signature and the two places that use `branch` / `_active`:

```python
    async def create(
        self,
        session_id: str,
        parent_cwd: Path,
        project_key: str = "default",
        *,
        track: bool = True,
        branch: str | None = None,
    ) -> Worktree | None:
        """Add a worktree for session_id. Returns None if isolation is unavailable.

        ``track=False`` hands back the worktree without registering it, so
        ``remove`` / ``apply_and_remove`` can never reach it — that is how
        session worktrees (spec: isolated sessions) outlive their run.
        """
        root = await git_root(parent_cwd)
        if root is None:
            return None
        base = await head_commit(root)
        if not base:
            return None  # empty repo / no commits

        branch = branch or f"bytebarn/{session_id[:12]}"
        path = self.store_root / project_key / session_id
```

The rest of the method body is unchanged, except the registration at the end:

```python
        wt = Worktree(
            session_id=session_id,
            path=path.resolve(),
            branch=branch,
            base_commit=base,
            parent_cwd=parent_cwd.resolve(),
            git_root=root,
        )
        if track:
            self._active[session_id] = wt
        return wt
```

Leave the `if path.exists(): await self._force_remove(...)` leftover-cleanup line as it is — it fires before the `Worktree` exists and is keyed on the path, not on `_active`.

- [ ] **Step 4: Add `dirty_files`**

Add next to the other module-level helpers, after `head_commit`:

```python
async def dirty_files(root: Path) -> list[str]:
    """Uncommitted paths (modified, staged, untracked) relative to the git root.

    Empty when ``root`` is not a git repo — callers treat that as "nothing to
    warn about" rather than an error.
    """
    top = await git_root(root)
    if top is None:
        return []
    code, out = await _git(top, "status", "--porcelain")
    if code != 0:
        return []
    names: list[str] = []
    for line in out.splitlines():
        if len(line) <= 3:
            continue
        path = line[3:].strip()
        # renames render as "old -> new"; report the destination
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        names.append(path.strip('"'))
    return names
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/engine/test_worktree.py -v`
Expected: PASS, including every pre-existing subagent-worktree test in that file — those exercise the `track=True` default path.

- [ ] **Step 6: Commit**

```bash
git add bytebarn/engine/worktree.py tests/engine/test_worktree.py
git commit -m "Support untracked worktrees and a dirty-repo probe"
```

---

### Task 3: `Engine.new_session(isolated=True)`

**Files:**
- Modify: `bytebarn/engine/facade.py` — `new_session` (~line 116), plus two new methods next to it
- Test: `tests/engine/test_worktree.py`

**Interfaces:**
- Consumes:
  - `Session.worktree_branch` and `Store.create_session(..., worktree_branch=...)` from Task 1.
  - `WorktreeManager.create(..., track=False, branch=...)` and `dirty_files` from Task 2.
- Produces:
  - `Engine.new_session(agent="", model="", directory="", project_id=None, isolated: bool = False) -> Session`
  - `Engine.repo_dirty() -> list[str]`
  - `Engine._isolate_session(session: Session, directory: str) -> Session` (internal)

Note on `directory`: top-level sessions created from the GUI already get a non-empty `directory` (the project root, via `MainWindow._default_new_session_dir`). So "isolated" is *not* `directory == ""` — it is `worktree_branch != ""`. Test accordingly.

- [ ] **Step 1: Write the failing tests**

Add to `tests/engine/test_worktree.py`. The module already has an `engine` fixture whose project dir is an initialised git repo:

```python
async def test_isolated_session_gets_its_own_worktree(engine):
    session = await engine.new_session(isolated=True)

    assert session.worktree_branch == f"bytebarn/session-{session.id[:8]}"
    assert session.directory
    wt_path = Path(session.directory)
    assert wt_path.is_dir()
    assert wt_path != engine.project_dir
    assert (wt_path / "README.md").is_file()

    # persisted, so isolation survives a restart
    again = await engine.store.get_session(session.id)
    assert again.directory == session.directory
    assert again.worktree_branch == session.worktree_branch


async def test_isolated_sessions_never_reuse_an_empty_session(engine):
    first = await engine.new_session(isolated=True)
    second = await engine.new_session(isolated=True)

    assert first.id != second.id
    assert first.directory != second.directory


async def test_plain_session_is_not_isolated(engine):
    session = await engine.new_session()
    assert session.worktree_branch == ""


async def test_isolation_is_a_no_op_outside_git(tmp_path):
    """A non-git project still yields a usable session, just not isolated."""
    import json

    from bytebarn.engine.facade import Engine

    proj = tmp_path / "plain"
    proj.mkdir()
    gdir = tmp_path / "global"
    gdir.mkdir()
    (gdir / "config.json").write_text(json.dumps({"model": "fake/model"}))
    eng = Engine(proj, db_path=tmp_path / "crew.db", global_dir=gdir)
    await eng.start()
    try:
        session = await eng.new_session(isolated=True, directory=str(proj))
        assert session.worktree_branch == ""
        assert session.directory == str(proj)
    finally:
        await eng.stop()


async def test_isolation_respects_worktree_disabled(engine):
    engine.config.model_extra["worktree"] = {"enabled": False}
    try:
        session = await engine.new_session(isolated=True)
        assert session.worktree_branch == ""
    finally:
        engine.config.model_extra.pop("worktree", None)


async def test_repo_dirty(engine):
    assert await engine.repo_dirty() == []
    (engine.project_dir / "scratch.txt").write_text("wip\n")
    assert await engine.repo_dirty() == ["scratch.txt"]
```

If `engine.config.model_extra` is `None` rather than a dict on a fresh `Config`, set it with `engine.config.__pydantic_extra__ = {"worktree": {"enabled": False}}` instead — check which one `_worktree_enabled` actually reads at runtime before writing the test, and match it.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/engine/test_worktree.py -k "isolat or repo_dirty" -v`
Expected: FAIL — `TypeError: new_session() got an unexpected keyword argument 'isolated'`.

- [ ] **Step 3: Implement `new_session(isolated=...)`**

In `bytebarn/engine/facade.py`, replace the existing `new_session` with:

```python
    async def new_session(
        self, agent: str = "", model: str = "", directory: str = "",
        project_id: str | None = None, isolated: bool = False,
    ) -> Session:
        """Create a session (optionally rooted in its own working directory).

        Reuses an existing empty untitled session in the same place instead of
        stacking "(untitled)" rows — mirrors Claude Desktop's lazy-chat feel.
        The lock keeps concurrent calls (e.g. startup auto-create racing a
        user's ⌘N) from both passing the reuse check before either inserts.

        ``isolated`` gives the session its own git worktree so its whole
        delegation tree writes there instead of the live checkout (spec:
        isolated sessions). Isolated sessions never reuse an existing row —
        each one needs its own worktree.
        """
        pid = project_id or self.project.id
        async with self._new_session_lock:
            if not isolated:
                for existing in await self.store.list_sessions(pid):
                    if (not existing.title and existing.directory == directory
                            and not await self.store.message_count(existing.id)):
                        return existing
            # per-project defaults fill in whatever the caller didn't pin
            project = await self.store.get_project(pid)
            if project and not agent:
                agent = project.default_agent
            if project and not model:
                model = project.default_model
            session = await self.store.create_session(
                pid, agent=agent or "build", model=model, directory=directory)
            if isolated:
                session = await self._isolate_session(session, directory)
        self.bus.emit(SessionUpdated(session_id=session.id))
        return session
```

The isolation call sits **inside** the lock so two concurrent isolated creations cannot race on the same worktree root.

- [ ] **Step 4: Implement `_isolate_session` and `repo_dirty`**

Add directly below `new_session`:

```python
    async def _isolate_session(self, session: Session, directory: str) -> Session:
        """Give a top-level session its own git worktree.

        Returns the session unchanged when isolation is unavailable — a non-git
        directory, a repo with no commits, or ``worktree.enabled=false``. The
        worktree is created untracked so no subagent cleanup path can remove
        it; the user merges the branch in git when the run is done.
        """
        if not self._worktree_enabled():
            return session
        base = Path(directory) if directory else self.project_dir
        project_key = (self.project.id if self.project else "p")[:16]
        wt = await self.worktrees.create(
            session.id, base, project_key=project_key,
            track=False, branch=f"bytebarn/session-{session.id[:8]}",
        )
        if wt is None:
            return session
        await self.store.update_session(
            session.id, directory=str(wt.path), worktree_branch=wt.branch)
        return await self.store.get_session(session.id) or session

    async def repo_dirty(self) -> list[str]:
        """Uncommitted paths in the live project checkout ([] when not a repo).

        The UI warns with this before starting an isolated session, because the
        worktree is checked out from HEAD and will not contain them.
        """
        from .worktree import dirty_files

        return await dirty_files(self.project_dir)
```

Confirm `Session` and `Path` are already imported at the top of `facade.py`; add whichever is missing.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/engine/test_worktree.py tests/engine/test_sessions.py -v`
Expected: PASS. `test_sessions.py` covers the untitled-session reuse behaviour that step 3 rewrapped in `if not isolated:` — it must stay green.

- [ ] **Step 6: Commit**

```bash
git add bytebarn/engine/facade.py tests/engine/test_worktree.py
git commit -m "Add isolated sessions to the engine facade"
```

---

### Task 4: Prove the live checkout is untouched

This is the load-bearing guarantee of the whole feature and it is worth its own test and its own review gate. No production code changes here — if this task needs any, something in Tasks 2–3 is wrong.

**Files:**
- Test: `tests/engine/test_worktree.py`

**Interfaces:**
- Consumes: `Engine.new_session(isolated=True)` from Task 3; the `engine` fixture, `_install`, `_run_and_wait`, `_init_repo` and `_git` helpers already in `tests/engine/test_worktree.py`.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Read the existing subagent worktree test**

Open `tests/engine/test_worktree.py` and read the existing end-to-end test that spawns a subagent through the task tool. Copy its `FakeProvider` script shape exactly — the `tool_turn` / `text_turn` call signature and how the task tool's arguments are spelled. Do not invent a script shape; the harness is fussy and the existing test is the reference.

- [ ] **Step 2: Write the failing test**

```python
def _tree_snapshot(root: Path) -> dict[str, bytes]:
    """Every tracked-ish file under root, excluding .git, for byte comparison."""
    out: dict[str, bytes] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and ".git" not in p.parts:
            out[str(p.relative_to(root))] = p.read_bytes()
    return out


async def test_subagent_of_isolated_session_never_touches_live_tree(engine):
    """The whole point: a /goal-style delegation writes only in the worktree."""
    before = _tree_snapshot(engine.project_dir)

    session = await engine.new_session(isolated=True)
    assert session.worktree_branch  # isolation actually happened
    worktree = Path(session.directory)

    # Script mirrors the existing subagent test: the parent delegates once,
    # the child writes a file, both then answer with text.
    calls = {"n": 0}

    def script(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return tool_turn("task", {
                "subagent_type": "build",
                "description": "write a file",
                "prompt": "create hello.txt",
            })
        if calls["n"] == 2:
            return tool_turn("write", {"path": "hello.txt", "content": "hi\n"})
        return text_turn("done")

    _install(engine, script)
    await _run_and_wait(engine, session, "delegate this")

    # the subagent's file reached the session worktree...
    assert (worktree / "hello.txt").read_text() == "hi\n"
    # ...and the live checkout is byte-identical to before the run
    assert _tree_snapshot(engine.project_dir) == before
    assert not (engine.project_dir / "hello.txt").exists()

    # the session worktree itself survived the child's apply_and_remove
    assert worktree.is_dir()
    still = await engine.store.get_session(session.id)
    assert still.directory == str(worktree)
```

The `script` closure above uses a call counter because the same `FakeProvider` serves both the parent and the child session. If the existing subagent test in this file drives `FakeProvider` a different way (for example a list of turns, or dispatching on the request's system prompt), use that mechanism instead — the assertions after `_run_and_wait` are what matter, not how the fake is scripted.

- [ ] **Step 3: Run the test**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/engine/test_worktree.py::test_subagent_of_isolated_session_never_touches_live_tree -v`
Expected: PASS on the first run if Tasks 2 and 3 are correct.

If it FAILS with `hello.txt` present in `engine.project_dir`, the bug is that the child worktree's `parent_cwd` resolved to the project dir instead of the session worktree. Check `Engine.run_subagent` (`bytebarn/engine/facade.py`, the `parent_cwd = Path(parent.directory) if parent.directory else self.project_dir` line) and confirm the `parent` session it receives is the one carrying the worktree `directory`.

If it FAILS because the session worktree is gone, `track=False` is not being honoured in Task 2's `create`.

- [ ] **Step 4: Run the full suite**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/engine/test_worktree.py
git commit -m "Test that isolated sessions leave the live checkout untouched"
```

---

### Task 5: Isolated toggle in the sidebar

**Files:**
- Modify: `bytebarn/app/session_list.py` — `SessionList.__init__` (~line 154), `_session_item` (~line 364)
- Modify: `bytebarn/app/main_window.py` — `_prompt_new_session` (~line 773), `_new_session` (~line 787), plus a new `_ask_yes_no` helper
- Test: `tests/app/test_ui_smoke.py`

**Interfaces:**
- Consumes: `Engine.new_session(isolated=...)` and `Engine.repo_dirty()` from Task 3; `Session.worktree_branch` from Task 1.
- Produces:
  - `SessionList.isolate_check: QCheckBox` and `SessionList.isolate_requested() -> bool`
  - `MainWindow._new_session(directory=None, project_id=None, isolated=False)`
  - `MainWindow._ask_yes_no(title: str, text: str) -> asyncio.Future[bool]`

- [ ] **Step 1: Write the failing UI tests**

Add to `tests/app/test_ui_smoke.py`. Match the file's existing fixture names and widget-construction style — read a nearby `SessionList` test first and follow it.

```python
def test_session_list_isolate_toggle(qapp):
    from bytebarn.app.session_list import SessionList

    widget = SessionList()
    assert widget.isolate_requested() is False
    widget.isolate_check.setChecked(True)
    assert widget.isolate_requested() is True


def test_session_item_marks_isolated_sessions(qapp):
    from types import SimpleNamespace

    from bytebarn.app.session_list import SessionList

    plain = SimpleNamespace(
        id="s1", title="plain", agent="build", model="fake/model",
        directory="/repo", updated_at=0.0, worktree_branch="",
    )
    isolated = SimpleNamespace(
        id="s2", title="isolated", agent="build", model="fake/model",
        directory="/wt/s2", updated_at=0.0,
        worktree_branch="bytebarn/session-abcd1234",
    )

    plain_item = SessionList._session_item(plain, set(), None)
    iso_item = SessionList._session_item(isolated, set(), None)

    assert "⑂" not in plain_item.text(0)
    assert "⑂" in iso_item.text(0)
    assert "session-abcd1234" in iso_item.text(0)
    assert "bytebarn/session-abcd1234" in iso_item.toolTip(0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/app/test_ui_smoke.py -k "isolate or isolated" -v`
Expected: FAIL — `AttributeError: 'SessionList' object has no attribute 'isolate_check'`.

- [ ] **Step 3: Add the checkbox to SessionList**

In `bytebarn/app/session_list.py`, next to where `self.new_button` is built:

```python
        self.new_button = QPushButton("+ New session")
        self.new_button.clicked.connect(self.new_session)
        from PySide6.QtWidgets import QCheckBox

        self.isolate_check = QCheckBox("Isolated")
        self.isolate_check.setToolTip(
            "Run the session in its own git worktree checked out from HEAD.\n"
            "Your working tree is never modified; merge the branch when done.")
```

Then place `self.isolate_check` in whatever layout already holds `self.new_button` — find that layout in `__init__` and add the checkbox right after the button. And add the accessor:

```python
    def isolate_requested(self) -> bool:
        """Whether the next new session should get its own worktree."""
        return self.isolate_check.isChecked()
```

- [ ] **Step 4: Mark isolated sessions in the tree**

In `_session_item`, replace the `dir_name` / `label` / `tooltip` lines:

```python
        directory = getattr(session, "directory", "") or ""
        branch = getattr(session, "worktree_branch", "") or ""
        # an isolated session's directory is an opaque worktree path — its
        # branch name is the useful thing to show
        dir_name = branch.split("/")[-1] if branch else (
            Path(directory).name if directory else "")
        parts = [title]
        if dir_name:
            parts.append(dir_name)
        parts.append(relative_time(session.updated_at))
        mark = "⑂ " if branch else ""
        label = f"{'● ' if is_running else ''}{mark}{' · '.join(parts)}"
```

and extend the tooltip after the existing `if directory:` block:

```python
        if branch:
            tooltip += f"\nisolated on {branch}"
        item.setToolTip(0, tooltip)
```

- [ ] **Step 5: Run the UI tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/app/test_ui_smoke.py -k "isolate or isolated" -v`
Expected: PASS.

- [ ] **Step 6: Thread the flag through MainWindow**

In `bytebarn/app/main_window.py`:

```python
    def _prompt_new_session(self, target_project_id: str | None = None) -> None:
        """Instant new session (Claude-Desktop style): inherit the working
        directory from context instead of forcing a picker. "New Session in
        Folder…" keeps the explicit choice available."""
        self._fire(self._new_session(
            project_id=target_project_id,
            isolated=self.session_list.isolate_requested(),
        ))
```

```python
    async def _new_session(
        self, directory: str | None = None, project_id: str | None = None,
        isolated: bool = False,
    ) -> None:
        """Create a session; with no explicit directory, inherit one from
        context: target project → current session → last used → project root.

        ``isolated`` runs it in its own git worktree checked out from HEAD, so
        uncommitted work in the live tree will not be visible to it — warn
        before that surprises anyone.
        """
        if directory is None:
            directory = await self._default_new_session_dir(project_id)
        if isolated:
            dirty = await self.engine.repo_dirty()
            if dirty:
                shown = "\n".join(f"  {p}" for p in dirty[:10])
                if len(dirty) > 10:
                    shown += f"\n  …and {len(dirty) - 10} more"
                ok = await self._ask_yes_no(
                    "Uncommitted changes",
                    "The isolated worktree is checked out from HEAD, so these "
                    f"uncommitted files will not be in it:\n\n{shown}\n\n"
                    "Start the isolated session anyway?",
                )
                if not ok:
                    return
        self._remember_project(directory)
        session = await self.engine.new_session(
            model=self._default_model(), directory=directory,
            project_id=project_id, isolated=isolated)
        if isolated:
            if session.worktree_branch:
                self.statusBar().showMessage(
                    f"Isolated on {session.worktree_branch} — {session.directory}",
                    8000)
            else:
                self.statusBar().showMessage(
                    "Isolation unavailable — not a git repo, or no commits yet",
                    6000)
        await self._load_session(session.id)
        await self._refresh_sessions()
```

`_prompt_new_session_in_folder` keeps calling `self._new_session(directory=directory)` unchanged — the explicit-folder path stays non-isolated.

- [ ] **Step 7: Add the modal helper**

`_new_session` runs inside an asyncio task, and the file's own comment on `_prompt_new_session_in_folder` notes that opening a modal directly from a task makes qasync warn about task re-entry. Bounce through the Qt loop instead. Add near `_prompt_directory`:

```python
    def _ask_yes_no(self, title: str, text: str):
        """Await a modal from an asyncio task without qasync task re-entry:
        the dialog opens on the next Qt loop turn and resolves a future."""
        import asyncio

        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QMessageBox

        fut = asyncio.get_event_loop().create_future()

        def _show() -> None:
            box = QMessageBox(QMessageBox.Warning, title, text, parent=self)
            box.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
            answered = box.exec() == QMessageBox.Ok
            if not fut.done():
                fut.set_result(answered)

        QTimer.singleShot(0, _show)
        return fut
```

Check whether `asyncio` is already imported at module level in `main_window.py`; if so, drop the local import.

- [ ] **Step 8: Run the full suite**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest`
Expected: PASS.

- [ ] **Step 9: Verify in the real app**

Use the project's `verify` skill (offscreen GUI driving) to open the app on this repo, tick **Isolated**, create a session, and confirm: the sidebar row shows `⑂ session-…`, the status bar names the branch, and `git worktree list` shows the new worktree.

```bash
git worktree list
git branch --list 'bytebarn/session-*'
```

- [ ] **Step 10: Commit**

```bash
git add bytebarn/app/session_list.py bytebarn/app/main_window.py tests/app/test_ui_smoke.py
git commit -m "Add an Isolated toggle for new sessions"
```

---

### Task 6: Document isolated sessions

**Files:**
- Modify: `CLAUDE.md` — the Glossary section
- Modify: `README.md` — only if it documents session creation; skip if it does not

**Interfaces:**
- Consumes: the finished feature from Tasks 1–5.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Add the glossary entry**

In `CLAUDE.md`, next to the existing **worktree isolation** bullet, add:

```markdown
- **isolated session** — a top-level session that owns a `git worktree` checked
  out from HEAD (`Engine.new_session(isolated=True)`). Its whole delegation tree
  writes there, so the live checkout never changes mid-run — this is how you run
  ByteBarn on ByteBarn. Branch `bytebarn/session-<id8>`, recorded in
  `session.worktree_branch`; never auto-applied or auto-removed, merge it in git.
```

- [ ] **Step 2: Check the README**

```bash
rg -n "new session|New session|worktree" README.md
```

If the README documents session creation, add one sentence pointing at the Isolated toggle. If it does not, change nothing and move on.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "Document isolated sessions"
```

---

## Self-Review Notes

Spec coverage check against `docs/superpowers/specs/2026-08-04-isolated-sessions-design.md`:

| Spec requirement | Task |
|---|---|
| `create(track=False, branch=...)` | 2 |
| `dirty_files` | 2 |
| `new_session(isolated=...)` | 3 |
| `repo_dirty()` | 3 |
| `run_subagent` unchanged | 4 (proves it) |
| Isolated toggle | 5 |
| Dirty pre-flight warning | 5 |
| Sidebar badge | 5 |
| Branch + path surfaced to the user | 5 (status bar + tooltip) |
| HEAD-only seeding | 2–3 (`create` uses `head_commit`; 5 warns) |
| No merge-back | 2 (`track=False`), 4 (asserts survival) |
| Non-git / empty repo fallback | 3 |
| Load-bearing isolation test | 4 |

Two deliberate deviations from the spec, both simplifications:

1. **`session.worktree_branch` column (Task 1)** — the spec inferred isolation from the directory path. A persisted column makes it a fact rather than a path-sniffing heuristic, and removes all UI plumbing since `_session_item` is a `@staticmethod` with no access to `engine.global_dir`.
2. **Status bar + tooltip instead of a transcript system note** — same information (branch name, worktree path), no new part type, and the tooltip is durable where a transcript note would need renderer support.

Update the spec to match after Task 6 if these hold up in review.
