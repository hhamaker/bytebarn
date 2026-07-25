"""Websearch tool, transcript search, edit/regenerate forking, export."""

import asyncio
import json

import httpx
import pytest

from bytebarn.engine.facade import Engine
from bytebarn.engine.providers.fake import FakeProvider, text_turn
from bytebarn.engine.tools.base import ToolContext
from bytebarn.engine.tools.websearch import WebSearchParams, WebSearchTool, parse_results


@pytest.fixture
async def engine(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    gdir = tmp_path / "global"
    gdir.mkdir()
    (gdir / "config.json").write_text(json.dumps(
        {"model": "fake/model", "small_model": "fake/model"}))
    eng = Engine(proj, db_path=tmp_path / "crew.db", global_dir=gdir)
    await eng.start()
    yield eng
    await eng.stop()


_DDG_PAGE = """
<div class="result">
  <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fdocs&amp;rut=x">Example <b>Docs</b></a>
  <a class="result__snippet" href="#">The documentation for <b>example</b> things.</a>
</div>
<div class="result">
  <a rel="nofollow" class="result__a" href="https://plain.dev/page">Plain link</a>
  <a class="result__snippet" href="#">Second snippet.</a>
</div>
"""


def test_websearch_parse_unwraps_ddg_redirects():
    results = parse_results(_DDG_PAGE, limit=8)
    assert results[0] == ("Example Docs", "https://example.com/docs",
                          "The documentation for example things.")
    assert results[1][1] == "https://plain.dev/page"


async def test_websearch_tool_formats_results(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["q"] == "crew app"
        return httpx.Response(200, text=_DDG_PAGE)

    tool = WebSearchTool(transport=httpx.MockTransport(handler))
    ctx = ToolContext(cwd=tmp_path, session_id="s")
    result = await tool.execute(WebSearchParams(query="crew app", limit=2), ctx)
    assert not result.is_error
    assert "1. Example Docs" in result.output
    assert "https://example.com/docs" in result.output
    assert "Second snippet." in result.output


async def test_websearch_tool_network_error(tmp_path):
    def handler(request):
        raise httpx.ConnectError("no network")

    tool = WebSearchTool(transport=httpx.MockTransport(handler))
    ctx = ToolContext(cwd=tmp_path, session_id="s")
    result = await tool.execute(WebSearchParams(query="x"), ctx)
    assert result.is_error


def test_websearch_registered_and_permissioned():
    from bytebarn.engine.permissions import _DEFAULTS, PermissionPolicy, SAFE
    from bytebarn.engine.tools.registry import ALL_TOOLS

    assert "websearch" in ALL_TOOLS
    assert _DEFAULTS["websearch"] == "ask"
    assert PermissionPolicy(session_mode=SAFE).resolve("websearch") == "deny"


async def _seed_conversation(engine, session_id, turns):
    for role, text in turns:
        message = await engine.store.add_message(session_id, role)
        await engine.store.add_part(message.id, "text", {"text": text})
        await asyncio.sleep(0)  # distinct created_at ordering via id ties is fine


async def test_search_sessions_matches_content_and_title(engine):
    s1 = await engine.new_session()
    await _seed_conversation(engine, s1.id, [
        ("user", "how do I fix the flux capacitor"),
        ("assistant", "Route the plutonium through the capacitor manifold."),
    ])
    await engine.store.update_session(s1.id, title="time machine repair")
    s2 = await engine.new_session(directory="/tmp/other")
    await _seed_conversation(engine, s2.id, [("user", "unrelated chat")])

    hits = await engine.store.search_sessions("plutonium")
    assert [s.id for s, _ in hits] == [s1.id]
    assert "plutonium" in hits[0][1]

    # title-only match comes back with an empty snippet
    hits = await engine.store.search_sessions("machine repair")
    assert [s.id for s, _ in hits] == [s1.id]
    assert hits[0][1] == ""

    assert await engine.store.search_sessions("flux", project_id="nope") == []


async def test_edit_and_rerun_truncates_and_forks(engine):
    engine.providers.register("fake", FakeProvider(
        lambda req: text_turn("answer two")))
    session = await engine.new_session()
    await _seed_conversation(engine, session.id, [
        ("user", "first question"),
        ("assistant", "first answer"),
    ])
    messages = await engine.store.list_messages(session.id)
    await engine.edit_and_rerun(session.id, messages[0].id, "better question")
    for _ in range(100):
        await asyncio.sleep(0.02)
        if not engine.is_running(session.id):
            break

    history = await engine.store.session_parts(session.id)
    texts = [p.data.get("text") for _, parts in history for p in parts
             if p.type == "text"]
    assert "first question" not in texts
    assert "first answer" not in texts
    assert "better question" in texts
    assert "answer two" in texts


async def test_regenerate_replaces_last_assistant_turn(engine):
    engine.providers.register("fake", FakeProvider(
        lambda req: text_turn("take two")))
    session = await engine.new_session()
    await _seed_conversation(engine, session.id, [
        ("user", "the prompt"),
        ("assistant", "take one"),
    ])
    assert await engine.regenerate(session.id)
    for _ in range(100):
        await asyncio.sleep(0.02)
        if not engine.is_running(session.id):
            break

    history = await engine.store.session_parts(session.id)
    texts = [p.data.get("text") for _, parts in history for p in parts
             if p.type == "text"]
    assert texts.count("the prompt") == 1
    assert "take one" not in texts
    assert "take two" in texts

    empty = await engine.new_session(directory="/tmp/empty")
    assert not await engine.regenerate(empty.id)  # nothing to redo


async def test_export_markdown_covers_part_types(engine):
    session = await engine.new_session()
    m1 = await engine.store.add_message(session.id, "user")
    await engine.store.add_part(m1.id, "text", {"text": "hello"})
    m2 = await engine.store.add_message(session.id, "assistant")
    await engine.store.add_part(m2.id, "reasoning", {"text": "pondering"})
    await engine.store.add_part(m2.id, "tool", {
        "tool": "bash", "title": "ls", "status": "done", "output": "a.txt"})
    await engine.store.add_part(m2.id, "text", {"text": "there is one file"})
    await engine.store.update_session(session.id, title="files chat")

    md = await engine.export_markdown(session.id)
    assert md.startswith("# files chat")
    assert "**You:**" in md and "hello" in md
    assert "pondering" in md
    assert "`bash` ls" in md and "a.txt" in md
    assert "there is one file" in md


def test_image_file_parts_become_image_content(tmp_path):
    import base64
    from types import SimpleNamespace

    from bytebarn.engine.runner import history_to_messages

    png = tmp_path / "shot.png"
    png.write_bytes(b"\x89PNG-fake-bytes")
    history = [(
        SimpleNamespace(role="user"),
        [SimpleNamespace(type="text", data={"text": "what is this"}),
         SimpleNamespace(type="file", data={"path": str(png)}),
         SimpleNamespace(type="file", data={"path": str(tmp_path / "notes.txt")})],
    )]
    msgs = history_to_messages(history)
    content = msgs[0].content
    assert content[0] == {"type": "text", "text": "what is this"}
    assert content[1]["type"] == "image"
    assert content[1]["media_type"] == "image/png"
    assert base64.b64decode(content[1]["data"]) == b"\x89PNG-fake-bytes"
    # non-image (and missing) files fall back to a text mention
    assert content[2]["type"] == "text" and "notes.txt" in content[2]["text"]


def test_image_content_reaches_both_wire_formats():
    from bytebarn.engine.providers.anthropic import _to_anthropic_messages
    from bytebarn.engine.providers.base import Msg
    from bytebarn.engine.providers.openai_compat import _to_openai_messages

    msgs = [Msg("user", [{"type": "text", "text": "look"},
                         {"type": "image", "media_type": "image/png", "data": "QUJD"}])]
    a = _to_anthropic_messages(msgs)
    assert a[0]["content"][1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}}
    o = _to_openai_messages("sys", msgs)
    blocks = o[1]["content"]
    assert blocks[0] == {"type": "text", "text": "look"}
    assert blocks[1]["image_url"]["url"] == "data:image/png;base64,QUJD"


async def test_attachments_survive_the_queue(engine):
    engine.providers.register("fake", FakeProvider(
        lambda req: text_turn("ok")))
    session = await engine.new_session()
    await engine.submit_prompt(session.id, "first")
    await engine.submit_prompt(session.id, "second", files=["/tmp/pic.png"])
    for _ in range(200):
        await asyncio.sleep(0.02)
        if not engine.is_running(session.id) and engine.queue_depth(session.id) == 0:
            break

    history = await engine.store.session_parts(session.id)
    file_parts = [p for _, parts in history for p in parts if p.type == "file"]
    assert [p.data["path"] for p in file_parts] == ["/tmp/pic.png"]


async def test_routines_schedule_and_queue_goals(engine):
    routine = await engine.store.add_routine(engine.project.id, "tidy the docs", 3600)
    assert (await engine.store.list_routines(engine.project.id))[0].id == routine.id

    # not due yet
    assert await engine.store.due_routines(routine.next_run - 1) == []
    # due: scheduler queues a goal and advances next_run
    await engine.store.update_routine(routine.id, next_run=0)
    task = asyncio.ensure_future(engine.run_routines(poll_s=0.01))
    try:
        for _ in range(100):
            await asyncio.sleep(0.01)
            if await engine.store.list_goals(engine.project.id):
                break
    finally:
        task.cancel()
    goals = await engine.store.list_goals(engine.project.id)
    assert any(g.prompt == "tidy the docs" for g in goals)
    refreshed = (await engine.store.list_routines(engine.project.id))[0]
    assert refreshed.next_run > 0  # advanced past the due time

    # disabled routines never fire
    await engine.store.update_routine(routine.id, enabled=0, next_run=0)
    assert await engine.store.due_routines(9e12) == []

    await engine.store.delete_routine(routine.id)
    assert await engine.store.list_routines(engine.project.id) == []


async def test_thinking_maps_to_anthropic_budget():
    from bytebarn.engine.providers.anthropic import AnthropicProvider
    from bytebarn.engine.providers.base import Msg, ModelRequest

    captured = {}

    class _FakeMessages:
        async def create(self, **kwargs):
            captured.update(kwargs)
            raise RuntimeError("stop here")  # request building is all we test

    class _FakeClient:
        messages = _FakeMessages()

    provider = AnthropicProvider(client=_FakeClient())
    req = ModelRequest(model_id="m", system="s",
                       messages=[Msg("user", [{"type": "text", "text": "hi"}])],
                       temperature=0.7, thinking="medium", max_tokens=8192)
    with pytest.raises(RuntimeError):
        async for _ in provider.stream(req):
            pass
    assert captured["thinking"] == {"type": "enabled", "budget_tokens": 8192}
    assert captured["max_tokens"] > 8192
    assert "temperature" not in captured  # API forbids it with thinking


async def test_retry_honors_server_retry_after():
    from types import SimpleNamespace

    from bytebarn.engine.providers.base import (
        ModelRequest, RetryableProviderError, retry_after_from, stream_with_retry,
    )

    # header extraction: valid, missing, absurd
    exc = SimpleNamespace(response=SimpleNamespace(headers={"retry-after": "17"}))
    assert retry_after_from(exc) == 17.0
    assert retry_after_from(SimpleNamespace()) is None
    too_long = SimpleNamespace(response=SimpleNamespace(headers={"retry-after": "9999"}))
    assert retry_after_from(too_long) is None

    # the loop waits at least the hinted window before retrying
    class Flaky:
        name = "flaky"
        calls = 0

        async def stream(self, req):
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise RetryableProviderError("429", retry_after=0.2)
            from bytebarn.engine.providers.base import Done, TextDelta
            yield TextDelta("ok")
            yield Done("end_turn")

    delays = []
    req = ModelRequest(model_id="m", system="s", messages=[])
    events = [e async for e in stream_with_retry(
        Flaky(), req, on_retry=lambda a, d: delays.append(d), base_delay=0.01)]
    assert any(type(e).__name__ == "TextDelta" for e in events)
    assert delays and delays[0] >= 0.2  # hint outranks the tiny base backoff


def test_history_never_ends_with_assistant_prefill():
    """A partial assistant turn (mid-stream failure) must not leave the
    outgoing conversation ending on an assistant message — newer Claude
    models reject that as unsupported prefill (400)."""
    from types import SimpleNamespace

    from bytebarn.engine.runner import history_to_messages

    history = [
        (SimpleNamespace(role="user"),
         [SimpleNamespace(type="text", data={"text": "fix the bug"})]),
        (SimpleNamespace(role="assistant"),
         [SimpleNamespace(type="text", data={"text": "Looking at the co"})]),  # cut off
    ]
    msgs = history_to_messages(history)
    assert msgs[-1].role == "user"
    assert "cut off" in msgs[-1].content[0]["text"]

    # tool-result turns already end with user — no nudge appended
    history[1][1].append(SimpleNamespace(
        id="p-tool", type="tool",
        data={"tool": "bash", "call_id": "c1",
              "input": {}, "output": "done", "status": "done"}))
    msgs = history_to_messages(history)
    assert msgs[-1].role == "user"
    assert msgs[-1].content[0]["type"] == "tool_result"
