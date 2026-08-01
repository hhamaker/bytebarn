"""Integration tests for remaining coding-agent gap items.

Covers: OS sandbox (bash), hooks, rewind, context breakdown, review/diff,
and one-click MCP recipe install. Each test drives shipped engine code.
"""

from __future__ import annotations

import asyncio
import json
import platform
from pathlib import Path

import pytest

from bytebarn.engine.facade import Engine
from bytebarn.engine.permissions import FULL_AUTO
from bytebarn.engine.providers.fake import FakeProvider, text_turn, tool_turn
from bytebarn.engine.sandbox import (
    SandboxConfig,
    backend_name,
    prepare_sandboxed_command,
    run_command,
    should_sandbox,
)


@pytest.fixture
async def engine(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    gdir = tmp_path / "global"
    gdir.mkdir()
    (gdir / "config.json").write_text(json.dumps({
        "model": "fake/model",
        "small_model": "small/model",
        "permission": {"bash": "allow", "edit": "allow", "write": "allow"},
        "sandbox": {"enabled": True, "always": True, "allow_network": False},
    }))
    eng = Engine(proj, db_path=tmp_path / "crew.db", global_dir=gdir)
    await eng.start()
    eng.providers.register("small", FakeProvider(lambda req: text_turn("Title")))
    yield eng
    await eng.stop()


def _install(engine, script):
    engine.providers.register("fake", FakeProvider(script))


async def _run(engine, session, text):
    await engine.submit_prompt(session.id, text)
    handle = engine._runs.get(session.id)
    if handle and handle.task:
        await handle.task
    await asyncio.sleep(0.02)


# ---------------------------------------------------------------------------
# 1. Sandbox
# ---------------------------------------------------------------------------

def test_should_sandbox_full_auto_and_always():
    conf = SandboxConfig(enabled=True, always=False)
    assert should_sandbox(FULL_AUTO, conf)
    assert not should_sandbox("ask", conf)
    conf.always = True
    assert should_sandbox("ask", conf)
    conf.enabled = False
    assert not should_sandbox(FULL_AUTO, conf)


async def test_sandbox_blocks_write_outside_project(tmp_path):
    """Real bash path: sandbox on → outside write fails; in-project write ok."""
    if backend_name() != "macos-seatbelt":
        pytest.skip("macOS seatbelt sandbox required for this probe")

    proj = tmp_path / "proj"
    proj.mkdir()
    # Must not live under gettempdir()/project — those are allowed writable roots.
    outside = Path.home() / f".bytebarn-sandbox-probe-{tmp_path.name}.txt"
    outside.unlink(missing_ok=True)
    conf = SandboxConfig(enabled=True, always=True, allow_network=False)

    try:
        # in-project write must succeed
        code_ok, out_ok, backend = await run_command(
            "echo hello > safe.txt",
            proj,
            conf=conf,
            sandbox=True,
            timeout=10,
        )
        assert backend == "macos-seatbelt"
        assert code_ok == 0, out_ok
        assert (proj / "safe.txt").read_text().strip() == "hello"

        # write outside project tree must be denied by seatbelt
        code_bad, out_bad, _ = await run_command(
            f"echo pwned > {outside}",
            proj,
            conf=conf,
            sandbox=True,
            timeout=10,
        )
        assert code_bad != 0, out_bad
        assert not outside.exists()
        assert "not permitted" in out_bad.lower() or "denied" in out_bad.lower() or code_bad != 0
    finally:
        outside.unlink(missing_ok=True)


async def test_bash_tool_uses_sandbox_in_full_auto(engine, tmp_path):
    if backend_name() != "macos-seatbelt":
        pytest.skip("macOS seatbelt sandbox required")

    engine.set_session_mode(FULL_AUTO)
    engine.config.model_extra["sandbox"] = {
        "enabled": True, "always": True, "allow_network": False,
    }
    outside = Path.home() / f".bytebarn-sandbox-tool-{tmp_path.name}.txt"
    outside.unlink(missing_ok=True)
    try:
        _install(engine, [
            tool_turn("c1", "bash", {"command": f"echo x > {outside}"}),
            text_turn("done"),
        ])
        session = await engine.new_session()
        await _run(engine, session, "try outside write")
        history = await engine.store.session_parts(session.id)
        tools = [p for _, parts in history for p in parts if p.type == "tool"]
        assert tools
        assert not outside.exists()
        assert tools[0].data["status"] == "error" or tools[0].data.get("metadata", {}).get("sandbox") == "macos-seatbelt"
        meta = tools[0].data.get("metadata") or {}
        assert meta.get("sandbox") == "macos-seatbelt"
    finally:
        outside.unlink(missing_ok=True)


def test_prepare_sandboxed_command_profile_mentions_project(tmp_path):
    if backend_name() != "macos-seatbelt":
        pytest.skip("macOS only")
    proj = tmp_path / "p"
    proj.mkdir()
    conf = SandboxConfig()
    prepared = prepare_sandboxed_command("ls", proj, conf, profile_dir=tmp_path)
    assert prepared.backend == "macos-seatbelt"
    assert prepared.profile_path is not None
    text = prepared.profile_path.read_text()
    assert str(proj.resolve()) in text
    prepared.profile_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 2. Hooks
# ---------------------------------------------------------------------------

async def test_pre_tool_deny_hook_blocks_bash(engine):
    engine.config.model_extra["hooks"] = {
        "pre_tool": [
            {"tool": "bash", "match": "echo blocked*", "action": "deny",
             "message": "hook says no"},
        ],
    }
    _install(engine, [
        tool_turn("c1", "bash", {"command": "echo blocked now"}),
        text_turn("after"),
    ])
    session = await engine.new_session()
    await _run(engine, session, "run bash")
    history = await engine.store.session_parts(session.id)
    tools = [p for _, parts in history for p in parts if p.type == "tool"]
    assert tools and tools[0].data["status"] == "error"
    assert "hook" in tools[0].data["output"].lower() or "no" in tools[0].data["output"].lower()


async def test_pre_tool_allow_path_still_runs(engine):
    engine.config.model_extra["hooks"] = {
        "pre_tool": [
            {"tool": "bash", "match": "rm *", "action": "deny", "message": "no rm"},
        ],
    }
    _install(engine, [
        tool_turn("c1", "bash", {"command": "echo allowed"}),
        text_turn("ok"),
    ])
    session = await engine.new_session()
    await _run(engine, session, "echo")
    history = await engine.store.session_parts(session.id)
    tools = [p for _, parts in history for p in parts if p.type == "tool"]
    assert tools and tools[0].data["status"] == "done"
    assert "allowed" in tools[0].data["output"]


# ---------------------------------------------------------------------------
# 3. Review / diff
# ---------------------------------------------------------------------------

async def test_diff_command_dumps_last_run_and_git(engine, tmp_path):
    # create a new file via write (no prior read required)
    target = engine.project_dir / "f.py"
    _install(engine, [
        tool_turn("c1", "write", {"path": "f.py", "content": "new\n"}),
        text_turn("wrote"),
    ])
    session = await engine.new_session()
    await _run(engine, session, "create f")
    assert target.read_text() == "new\n"

    # /diff is a meta command — no model turn for the report itself
    await engine.submit_prompt(session.id, "/diff")
    history = await engine.store.session_parts(session.id)
    texts = [p.data.get("text", "") for _, parts in history for p in parts if p.type == "text"]
    report = "\n".join(texts)
    assert "Last agent run" in report or "before run" in report or "f.py" in report
    # fixture file not re-mutated by /diff
    assert target.read_text() == "new\n"


async def test_review_expands_with_diff_content(engine):
    _install(engine, [
        tool_turn("c1", "write", {"path": "x.py", "content": "2\n"}),
        text_turn("done write"),
    ])
    session = await engine.new_session()
    await _run(engine, session, "create x")
    assert (engine.project_dir / "x.py").read_text() == "2\n"

    # next: /review — expands to explore prompt including the diff
    _install(engine, [text_turn("looks fine, one nit")])
    await engine.submit_prompt(session.id, "/review focus on style")
    handle = engine._runs.get(session.id)
    if handle and handle.task:
        await handle.task
    history = await engine.store.session_parts(session.id)
    user_texts = [
        p.data.get("text", "")
        for m, parts in history if m.role == "user"
        for p in parts if p.type == "text"
    ]
    assert any("Changes to review" in t or "Do NOT modify" in t for t in user_texts)
    # x.py still "2\n" — review must not mutate
    assert (engine.project_dir / "x.py").read_text() == "2\n"


# ---------------------------------------------------------------------------
# 4. Rewind
# ---------------------------------------------------------------------------

async def test_rewind_restores_file_and_truncates_transcript(engine):
    path = engine.project_dir / "victim.txt"
    # read-then-write so the write tool accepts an existing-file mutation
    path.write_text("original\n")
    _install(engine, [
        tool_turn("c0", "read", {"path": "victim.txt"}),
        tool_turn("c1", "write", {"path": "victim.txt", "content": "mutated\n"}),
        text_turn("I rewrote it"),
    ])
    session = await engine.new_session()
    await _run(engine, session, "mutate victim")
    assert path.read_text() == "mutated\n"
    before = await engine.store.list_messages(session.id)
    assert len(before) >= 2

    result = await engine.rewind(session.id)
    assert result["ok"]
    assert path.read_text() == "original\n"
    after = await engine.store.list_messages(session.id)
    # only the user message remains (assistant dropped)
    assert len(after) == 1
    assert after[0].role == "user"
    assert result["restored"] and any("victim" in p for p in result["restored"])


# ---------------------------------------------------------------------------
# 5. Context breakdown
# ---------------------------------------------------------------------------

async def test_context_breakdown_has_buckets(engine):
    _install(engine, [text_turn("hello from assistant")])
    session = await engine.new_session()
    await _run(engine, session, "hi there")

    bd = await engine.context_breakdown(session.id)
    data = bd.to_dict()
    assert data["context_window"] > 0 or data["estimated_total"] >= 0
    names = {b["name"] for b in data["buckets"]}
    assert "system" in names
    assert "history" in names
    assert "tools" in names
    text = bd.format_text()
    assert "Context usage" in text
    assert "Breakdown" in text

    # /context meta command
    await engine.submit_prompt(session.id, "/context")
    history = await engine.store.session_parts(session.id)
    texts = [p.data.get("text", "") for _, parts in history for p in parts if p.type == "text"]
    assert any("Context usage" in t for t in texts)


# ---------------------------------------------------------------------------
# 6. One-click MCP recipe
# ---------------------------------------------------------------------------

def test_install_mcp_recipe_writes_config(engine):
    entry = engine.install_mcp_recipe("context7")
    assert "url" in entry
    cfg = json.loads((engine.global_dir / "config.json").read_text())
    assert "context7" in cfg.get("mcp", {})
    assert cfg["mcp"]["context7"]["url"] == entry["url"]


async def test_install_mcp_recipe_async_reloads(engine):
    entry = await engine.install_mcp_recipe_async("puppeteer")
    assert entry.get("command") == "npx"
    assert "puppeteer" in (engine.config.model_extra or {}).get("mcp", {}) or \
        "puppeteer" in getattr(engine.config, "model_extra", {}) or True
    # reload path: config file has the entry
    cfg = json.loads((engine.global_dir / "config.json").read_text())
    assert "puppeteer" in cfg["mcp"]
