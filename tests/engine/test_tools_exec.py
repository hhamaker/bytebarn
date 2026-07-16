import asyncio
import time

import pytest

from crew.engine.tools.base import ToolContext
from crew.engine.tools.bash import BashParams, BashTool
from crew.engine.tools.question import QuestionParams, QuestionTool
from crew.engine.tools.registry import build_tools
from crew.engine.tools.todowrite import TodoItem, TodoWriteParams, TodoWriteTool
from crew.engine.tools.webfetch import html_to_markdown


@pytest.fixture
def ctx(tmp_path):
    return ToolContext(cwd=tmp_path, session_id="s1")


async def test_bash_output_and_exit_code(ctx):
    result = await BashTool().execute(BashParams(command="echo hello; echo err 1>&2"), ctx)
    assert "hello" in result.output and "err" in result.output
    assert not result.is_error

    result = await BashTool().execute(BashParams(command="exit 3"), ctx)
    assert result.is_error and "[exit code 3]" in result.output


async def test_bash_runs_in_project_cwd(ctx, tmp_path):
    result = await BashTool().execute(BashParams(command="pwd"), ctx)
    assert str(tmp_path) in result.output


async def test_bash_timeout_kills_process_tree(ctx):
    start = time.monotonic()
    result = await BashTool().execute(BashParams(command="sleep 30", timeout=1), ctx)
    assert time.monotonic() - start < 5
    assert result.is_error and "timed out" in result.output


async def test_bash_abort(ctx):
    ctx.abort = asyncio.Event()

    async def aborter():
        await asyncio.sleep(0.2)
        ctx.abort.set()

    task = asyncio.ensure_future(aborter())
    start = time.monotonic()
    result = await BashTool().execute(BashParams(command="sleep 30", timeout=60), ctx)
    await task
    assert time.monotonic() - start < 5
    assert result.is_error and "aborted" in result.output


async def test_todowrite_calls_handler(ctx):
    received = []

    async def on_todos(items):
        received.append(items)

    ctx.on_todos = on_todos
    result = await TodoWriteTool().execute(
        TodoWriteParams(todos=[TodoItem(content="a"), TodoItem(content="b", status="completed")]), ctx
    )
    assert not result.is_error
    assert received[0][1] == {"content": "b", "status": "completed"}


async def test_question_resolves_via_handler(ctx):
    async def answer(question, options):
        assert question == "Pick one" and options == ["a", "b"]
        return "b"

    ctx.ask_question = answer
    result = await QuestionTool().execute(QuestionParams(question="Pick one", options=["a", "b"]), ctx)
    assert "user answered: b" in result.output


def test_html_to_markdown():
    html = "<html><head><style>x{}</style></head><body><h1>Title</h1><p>Hello <b>world</b></p><ul><li>one</li><li>two</li></ul><a href='http://x'>link</a></body></html>"
    md = html_to_markdown(html)
    assert "# Title" in md
    assert "**world**" in md
    assert "- one" in md
    assert "[link](http://x)" in md
    assert "style" not in md


def test_build_tools_registry():
    tools = build_tools(None, include_task=True, subagents=[("explore", "search things")])
    names = {t.name for t in tools}
    assert "task" in names and "bash" in names and "memory" in names and len(names) == 11
    task = next(t for t in tools if t.name == "task")
    assert "explore: search things" in task.description()

    tools = build_tools({"read": True, "bash": True}, include_task=False)
    names = {t.name for t in tools}
    assert names == {"read", "bash"}

    # subagents never get task even if their tools map allows everything
    tools = build_tools(None, include_task=False)
    assert "task" not in {t.name for t in tools}
