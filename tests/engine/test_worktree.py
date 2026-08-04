"""Git worktree isolation for parallel subagents."""

from __future__ import annotations

import asyncio
import json
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
