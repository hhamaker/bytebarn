"""Git worktree isolation for parallel subagents."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from bytebarn.engine.facade import Engine
from bytebarn.engine.providers.fake import FakeProvider, text_turn, tool_turn
from bytebarn.engine.worktree import WorktreeManager, git_root


def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
    )
    return r.stdout.strip()


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init")
    _git(path, "config", "user.email", "test@bytebarn.local")
    _git(path, "config", "user.name", "ByteBarn Test")
    (path / "README.md").write_text("hi\n")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "init")


@pytest.fixture
async def engine(tmp_path):
    proj = tmp_path / "proj"
    _init_repo(proj)
    gdir = tmp_path / "global"
    gdir.mkdir()
    (gdir / "config.json").write_text(json.dumps({
        "model": "fake/model",
        "small_model": "small/model",
        "permission": {"bash": "allow", "edit": "allow", "write": "allow"},
    }))
    eng = Engine(proj, db_path=tmp_path / "crew.db", global_dir=gdir)
    await eng.start()
    eng.providers.register("small", FakeProvider(lambda req: text_turn("Title")))
    yield eng
    await eng.stop()


@pytest.fixture
async def nongit_engine(tmp_path):
    """Engine rooted in a plain (non-git) directory — isolation unavailable."""
    proj = tmp_path / "plain"
    proj.mkdir()
    gdir = tmp_path / "global"
    gdir.mkdir()
    (gdir / "config.json").write_text(json.dumps({"model": "fake/model"}))
    eng = Engine(proj, db_path=tmp_path / "crew.db", global_dir=gdir)
    await eng.start()
    try:
        yield eng
    finally:
        await eng.stop()


def _install(engine, script):
    provider = FakeProvider(script)
    engine.providers.register("fake", provider)
    return provider


async def _run_and_wait(engine, session, text):
    await engine.submit_prompt(session.id, text)
    await engine._runs[session.id].task
    await asyncio.sleep(0.05)


async def test_git_root_detects_repo(tmp_path):
    proj = tmp_path / "r"
    _init_repo(proj)
    root = await git_root(proj)
    assert root == proj.resolve()
    assert await git_root(tmp_path / "nope") is None


async def test_worktree_manager_create_apply_remove(tmp_path):
    proj = tmp_path / "r"
    _init_repo(proj)
    store = tmp_path / "wt"
    mgr = WorktreeManager(store)
    wt = await mgr.create("sess1", proj, project_key="p")
    assert wt is not None
    assert wt.path.is_dir()
    assert (wt.path / "README.md").read_text() == "hi\n"

    (wt.path / "new.py").write_text("x = 1\n")
    (wt.path / "README.md").write_text("hi\nedited\n")

    result = await mgr.apply(wt)
    assert "new.py" in result.applied
    assert "README.md" in result.applied
    assert not result.conflicts
    assert (proj / "new.py").read_text() == "x = 1\n"
    assert (proj / "README.md").read_text() == "hi\nedited\n"

    await mgr.remove("sess1")
    assert not wt.path.exists()
    assert mgr.get("sess1") is None


async def test_worktree_conflict_when_parent_diverged(tmp_path):
    proj = tmp_path / "r"
    _init_repo(proj)
    mgr = WorktreeManager(tmp_path / "wt")
    wt = await mgr.create("s", proj, project_key="p")
    assert wt is not None

    # parent and worktree both edit the same base file differently
    (proj / "README.md").write_text("parent change\n")
    (wt.path / "README.md").write_text("worktree change\n")

    result = await mgr.apply(wt)
    assert "README.md" in result.conflicts
    assert (proj / "README.md").read_text() == "parent change\n"
    await mgr.remove("s")


async def test_subagent_writes_merge_back_via_worktree(engine):
    """Subagent write lands in a worktree then is applied to the project."""
    _install(engine, [
        tool_turn("c1", "write", {"path": "mod.py", "content": "M = 1\n"}),
        text_turn("wrote mod.py"),
    ])
    parent = await engine.new_session(agent="build")
    # drive a real subagent spawn
    out = await engine.run_subagent(
        parent, "general", "Create mod.py with M=1", "write mod", None,
    )
    assert (engine.project_dir / "mod.py").read_text() == "M = 1\n"
    assert "worktree: applied" in out
    # worktree cleaned up
    assert engine.worktrees.get  # still exists
    # no leftover active worktree for the child
    children = await engine.store.child_sessions(parent.id)
    assert len(children) == 1
    assert engine.worktrees.get(children[0].id) is None


async def test_parallel_subagents_isolated_then_merged(engine):
    """Two concurrent writers each get a worktree; both files end up in parent."""
    concurrency = {"active": 0, "peak": 0}

    class Tracking(FakeProvider):
        async def stream(self, req):
            concurrency["active"] += 1
            concurrency["peak"] = max(concurrency["peak"], concurrency["active"])
            try:
                await asyncio.sleep(0.08)
                async for event in super().stream(req):
                    yield event
            finally:
                concurrency["active"] -= 1

    def script(req):
        flat = json.dumps([m.content for m in req.messages])
        tools = [t.name for t in req.tools]
        if "task" in tools:
            if "tool_result" not in flat:
                from bytebarn.engine.providers.base import (
                    Done, TextDelta, ToolCallDelta, ToolCallEnd, ToolCallStart, Usage,
                )
                events = [TextDelta("delegating")]
                for cid, desc, agent, prompt in (
                    ("t1", "write a", "general", "Create a.py with A=1"),
                    ("t2", "write b", "general", "Create b.py with B=2"),
                ):
                    events += [
                        ToolCallStart(cid, "task"),
                        ToolCallDelta(cid, json.dumps({
                            "description": desc, "agent": agent, "prompt": prompt,
                        })),
                        ToolCallEnd(cid),
                    ]
                events += [Usage(1, 1), Done("tool_use")]
                return events
            return text_turn("both done")
        # subagent
        target = "a.py" if "a.py" in flat else "b.py"
        content = "A = 1\n" if target == "a.py" else "B = 2\n"
        if "tool_result" not in flat:
            return tool_turn("w1", "write", {"path": target, "content": content})
        return text_turn(f"wrote {target}")

    engine.providers.register("fake", Tracking(script))
    session = await engine.new_session(agent="orchestrator")
    await _run_and_wait(engine, session, "make a and b")

    assert (engine.project_dir / "a.py").read_text() == "A = 1\n"
    assert (engine.project_dir / "b.py").read_text() == "B = 2\n"
    assert concurrency["peak"] >= 2  # ran in parallel


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


async def test_isolated_sessions_never_reuse_an_empty_session(nongit_engine):
    """Guards the ``if not isolated:`` skip in the reuse loop.

    Must run against a non-git project: in a git project, ``_isolate_session``
    rewrites ``directory`` to the fresh worktree path on every call, so the
    reuse loop's ``existing.directory == directory`` check already fails on
    its own — the test would pass whether or not the isolated guard exists.
    Only when isolation is unavailable does ``directory`` stay constant
    (the caller-supplied value) across both calls, making this the one setup
    where removing the guard actually changes the outcome.
    """
    proj = str(nongit_engine.project_dir)
    first = await nongit_engine.new_session(isolated=True, directory=proj)
    second = await nongit_engine.new_session(isolated=True, directory=proj)

    assert first.id != second.id


async def test_plain_session_is_not_isolated(engine):
    session = await engine.new_session()
    assert session.worktree_branch == ""


async def test_isolation_is_a_no_op_outside_git(nongit_engine):
    """A non-git project still yields a usable session, just not isolated."""
    proj = str(nongit_engine.project_dir)
    session = await nongit_engine.new_session(isolated=True, directory=proj)
    assert session.worktree_branch == ""
    assert session.directory == proj


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


async def test_isolate_session_cleans_up_worktree_on_store_failure(engine, monkeypatch):
    """A store write failure after the worktree exists must not orphan it.

    ``track=False`` worktrees are never registered with the manager, so if
    ``_isolate_session`` didn't clean up on its own error path, nothing else
    would ever remove the directory or its ``bytebarn/session-*`` branch.
    """
    calls: list[str] = []

    async def boom(session_id, **fields):
        calls.append(session_id)
        raise RuntimeError("store exploded")

    monkeypatch.setattr(engine.store, "update_session", boom)

    with pytest.raises(RuntimeError, match="store exploded"):
        await engine.new_session(isolated=True)

    assert calls, "update_session should have been attempted"
    session_id = calls[0]
    project_key = (engine.project.id if engine.project else "p")[:16]
    leftover = engine.worktrees.store_root / project_key / session_id
    assert not leftover.exists()


async def test_isolate_session_store_failure_survives_cleanup_failure(engine, monkeypatch):
    """Cleanup is best-effort: if teardown itself blows up, the caller must
    still see the original store exception, not the teardown's.
    """
    async def boom_store(session_id, **fields):
        raise RuntimeError("store exploded")

    async def boom_cleanup(*args, **kwargs):
        raise ValueError("cleanup exploded")

    monkeypatch.setattr(engine.store, "update_session", boom_store)
    monkeypatch.setattr("bytebarn.engine.worktree.discard", boom_cleanup)

    with pytest.raises(RuntimeError, match="store exploded"):
        await engine.new_session(isolated=True)


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
    assert before  # guard against a broken glob making this comparison vacuous

    session = await engine.new_session(isolated=True)
    assert session.worktree_branch  # isolation actually happened
    worktree = Path(session.directory)

    # Script mirrors the existing subagent tests in this file: it dispatches
    # on a call counter, since the same FakeProvider serves both the parent
    # and the child session. tool_turn's signature is (call_id, name, input).
    calls = {"n": 0}

    def script(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return tool_turn("c1", "task", {
                "agent": "general",
                "description": "write a file",
                "prompt": "create hello.txt",
            })
        if calls["n"] == 2:
            return tool_turn("c2", "write", {"path": "hello.txt", "content": "hi\n"})
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


async def test_worktree_can_be_disabled(engine):
    engine.config.model_extra["worktree"] = {"enabled": False}
    _install(engine, [
        tool_turn("c1", "write", {"path": "x.py", "content": "1\n"}),
        text_turn("ok"),
    ])
    parent = await engine.new_session()
    out = await engine.run_subagent(
        parent, "general", "write x.py", "write x", None,
    )
    assert (engine.project_dir / "x.py").read_text() == "1\n"
    assert "worktree:" not in out


# --- seams between isolation and adjacent behaviour -------------------------


async def test_plain_session_never_reuses_an_isolated_row(engine):
    """A plain ``+ New session`` must not hand back an isolated session.

    The reuse loop matches on (no title, same directory, no messages) — an
    untouched isolated session satisfies all three the moment the user unticks
    Isolated and clicks again with its worktree still inherited as the default
    directory. Returning it would silently drop the user into another
    session's worktree.
    """
    isolated = await engine.new_session(isolated=True)
    assert isolated.worktree_branch

    plain = await engine.new_session(isolated=False, directory=isolated.directory)

    assert plain.id != isolated.id
    assert plain.worktree_branch == ""


async def test_repo_dirty_inspects_the_directory_it_is_given(engine, tmp_path):
    """The dirty pre-flight must probe the repo actually being branched."""
    other = tmp_path / "other"
    _init_repo(other)
    (other / "wip.txt").write_text("unsaved\n")

    # the engine's own project is clean; the other repo is not
    assert await engine.repo_dirty() == []
    assert await engine.repo_dirty(other) == ["wip.txt"]
    assert await engine.repo_dirty(engine.project_dir) == []


async def test_isolated_session_below_git_root_keeps_its_depth(tmp_path):
    """Opening ``/repo/frontend`` must not relocate the session to the repo root."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "frontend" / "src").mkdir(parents=True)
    (repo / "frontend" / "src" / "App.tsx").write_text("export default 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "frontend")

    gdir = tmp_path / "global"
    gdir.mkdir()
    (gdir / "config.json").write_text(json.dumps({"model": "fake/model"}))
    eng = Engine(repo / "frontend", db_path=tmp_path / "crew.db", global_dir=gdir)
    await eng.start()
    try:
        session = await eng.new_session(isolated=True)
        assert session.worktree_branch
        cwd = Path(session.directory)
        assert cwd.is_dir()
        assert cwd.name == "frontend"
        # the checked-out frontend/, not the repo root
        assert (cwd / "src" / "App.tsx").is_file()
        assert not (cwd / "frontend").exists()
    finally:
        await eng.stop()


async def test_isolated_session_in_untracked_subdir_still_has_a_cwd(tmp_path):
    """A project dir git has never seen still needs an existing cwd.

    ``worktree add`` checks out HEAD, which has no such path — mirror the
    depth anyway rather than hand the run a directory that does not exist.
    """
    repo = tmp_path / "repo2"
    _init_repo(repo)
    (repo / "scratch").mkdir()  # never committed

    gdir = tmp_path / "global"
    gdir.mkdir()
    (gdir / "config.json").write_text(json.dumps({"model": "fake/model"}))
    eng = Engine(repo / "scratch", db_path=tmp_path / "crew.db", global_dir=gdir)
    await eng.start()
    try:
        session = await eng.new_session(isolated=True)
        assert session.worktree_branch
        cwd = Path(session.directory)
        assert cwd.is_dir()
        assert cwd.name == "scratch"
    finally:
        await eng.stop()


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


async def test_missing_worktree_falls_back_to_project_dir_with_one_warning(engine):
    """The documented cleanup (`git worktree remove`) must not brick the session.

    Without a fallback every bash call raises FileNotFoundError out of the
    subprocess spawn and ``write`` recreates the path as a plain untracked
    directory — writes appear to succeed into a tree git no longer knows.
    """
    import shutil

    session = await engine.new_session(isolated=True)
    gone = Path(session.directory)
    assert gone.is_dir()
    shutil.rmtree(gone)  # what `git worktree remove` leaves behind

    _install(engine, [
        tool_turn("c1", "write", {"path": "recovered.py", "content": "R = 1\n"}),
        text_turn("done"),
        text_turn("second turn"),
    ])
    await _run_and_wait(engine, session, "write recovered.py")

    # the write landed in the live checkout, not a resurrected phantom dir
    assert (engine.project_dir / "recovered.py").read_text() == "R = 1\n"
    assert not gone.exists()

    texts = [
        p.data.get("text", "")
        for _m, parts in await engine.store.session_parts(session.id)
        for p in parts if p.type == "text"
    ]
    warnings = [t for t in texts if "no longer exists" in t]
    assert len(warnings) == 1, texts
    assert str(engine.project_dir) in warnings[0]

    # a second run warns again (once), never silently
    await _run_and_wait(engine, session, "anything else")
    texts2 = [
        p.data.get("text", "")
        for _m, parts in await engine.store.session_parts(session.id)
        for p in parts if p.type == "text"
    ]
    assert len([t for t in texts2 if "no longer exists" in t]) == 2


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
