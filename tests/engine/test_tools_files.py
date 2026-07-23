import pytest

from bytebarn.engine.tools.base import TRUNCATE_AT, ToolContext, truncate_output
from bytebarn.engine.tools.edit import EditParams, EditTool
from bytebarn.engine.tools.glob import GlobParams, GlobTool
from bytebarn.engine.tools.grep import GrepParams, GrepTool
from bytebarn.engine.tools.read import ReadParams, ReadTool
from bytebarn.engine.tools.write import WriteParams, WriteTool


@pytest.fixture
def ctx(tmp_path):
    return ToolContext(cwd=tmp_path, session_id="s1")


async def test_read_numbered_lines(ctx, tmp_path):
    (tmp_path / "f.txt").write_text("alpha\nbeta\ngamma")
    result = await ReadTool().execute(ReadParams(path="f.txt"), ctx)
    assert result.output.split("\n")[0].endswith("→alpha")
    assert "   2→beta" in result.output
    assert str(tmp_path / "f.txt") in ctx.files_read


async def test_read_offset_limit(ctx, tmp_path):
    (tmp_path / "f.txt").write_text("\n".join(f"line{i}" for i in range(10)))
    result = await ReadTool().execute(ReadParams(path="f.txt", offset=2, limit=3), ctx)
    lines = result.output.split("\n")
    assert lines[0].endswith("→line2") and lines[2].endswith("→line4")
    assert "more lines" in result.output


async def test_read_missing_file(ctx):
    result = await ReadTool().execute(ReadParams(path="nope.txt"), ctx)
    assert result.is_error


async def test_write_new_file_creates_parents(ctx, tmp_path):
    result = await WriteTool().execute(WriteParams(path="a/b/c.txt", content="hi"), ctx)
    assert not result.is_error
    assert (tmp_path / "a/b/c.txt").read_text() == "hi"


async def test_write_overwrite_requires_prior_read(ctx, tmp_path):
    (tmp_path / "f.txt").write_text("original")
    result = await WriteTool().execute(WriteParams(path="f.txt", content="new"), ctx)
    assert result.is_error
    await ReadTool().execute(ReadParams(path="f.txt"), ctx)
    result = await WriteTool().execute(WriteParams(path="f.txt", content="new"), ctx)
    assert not result.is_error
    assert (tmp_path / "f.txt").read_text() == "new"


async def test_edit_requires_read_and_unique(ctx, tmp_path):
    (tmp_path / "f.py").write_text("x = 1\ny = 1\n")
    tool = EditTool()
    result = await tool.execute(EditParams(path="f.py", old_string="= 1", new_string="= 2"), ctx)
    assert result.is_error and "read" in result.output
    await ReadTool().execute(ReadParams(path="f.py"), ctx)
    result = await tool.execute(EditParams(path="f.py", old_string="= 1", new_string="= 2"), ctx)
    assert result.is_error and "2 times" in result.output
    result = await tool.execute(EditParams(path="f.py", old_string="x = 1", new_string="x = 2"), ctx)
    assert not result.is_error
    assert (tmp_path / "f.py").read_text() == "x = 2\ny = 1\n"


async def test_edit_replace_all_and_not_found(ctx, tmp_path):
    (tmp_path / "f.py").write_text("a a a")
    await ReadTool().execute(ReadParams(path="f.py"), ctx)
    tool = EditTool()
    result = await tool.execute(EditParams(path="f.py", old_string="zz", new_string="y"), ctx)
    assert result.is_error and "not found" in result.output
    result = await tool.execute(
        EditParams(path="f.py", old_string="a", new_string="b", replace_all=True), ctx
    )
    assert not result.is_error
    assert (tmp_path / "f.py").read_text() == "b b b"


async def test_glob_newest_first_and_cap(ctx, tmp_path):
    import os
    import time

    for i, name in enumerate(["old.py", "mid.py", "new.py"]):
        p = tmp_path / name
        p.write_text("x")
        t = time.time() - (10 - i)
        os.utime(p, (t, t))
    result = await GlobTool().execute(GlobParams(pattern="*.py"), ctx)
    names = [line.split("/")[-1] for line in result.output.split("\n")]
    assert names == ["new.py", "mid.py", "old.py"]


@pytest.mark.parametrize("force_python", [False, True])
async def test_grep_modes(ctx, tmp_path, monkeypatch, force_python):
    if force_python:
        monkeypatch.setattr("shutil.which", lambda _: None)
    (tmp_path / "a.py").write_text("def foo():\n    pass\n")
    (tmp_path / "b.py").write_text("def bar():\n    foo()\n    foo()\n")
    (tmp_path / "c.txt").write_text("foo\n")
    tool = GrepTool()

    result = await tool.execute(GrepParams(pattern="foo", glob="*.py"), ctx)
    files = set(result.output.strip().split("\n"))
    assert any("a.py" in f for f in files) and any("b.py" in f for f in files)
    assert not any("c.txt" in f for f in files)

    result = await tool.execute(GrepParams(pattern="foo", glob="*.py", output_mode="count"), ctx)
    counts = dict(line.rsplit(":", 1) for line in result.output.strip().split("\n"))
    assert {k.split("/")[-1]: v for k, v in counts.items()} == {"a.py": "1", "b.py": "2"}

    result = await tool.execute(GrepParams(pattern="foo", glob="*.py", output_mode="content"), ctx)
    assert "def foo():" in result.output

    result = await tool.execute(GrepParams(pattern="nomatchxyz"), ctx)
    assert result.output == "no matches"


def test_truncation(tmp_path):
    short, sidecar = truncate_output("abc", tmp_path)
    assert short == "abc" and sidecar is None
    big = "x" * (TRUNCATE_AT + 1000)
    out, sidecar = truncate_output(big, tmp_path)
    assert len(out) < len(big)
    assert "truncated" in out
    assert sidecar and (tmp_path / sidecar.split("/")[-1]).read_text() == big
