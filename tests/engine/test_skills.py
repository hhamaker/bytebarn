
import pytest

from bytebarn.engine.skills import (
    SkillRegistry,
    catalog_section,
    format_skill_prompt,
    parse_skill_file,
)
from bytebarn.engine.tools.registry import build_tools
from bytebarn.engine.tools.skill import SkillTool


@pytest.fixture
def skill_dirs(tmp_path):
    g = tmp_path / "global"
    p = tmp_path / "proj"
    # global_dir is ~/.bytebarn — skills at <global>/skills/
    (g / "skills").mkdir(parents=True)
    (p / ".bytebarn" / "skills").mkdir(parents=True)
    (g / "skills" / "review.md").write_text("global review")
    (p / ".bytebarn" / "skills" / "review.md").write_text("project review")
    (p / ".bytebarn" / "skills" / "local.md").write_text(
        "---\ndescription: Project-only helper\n---\nlocal only"
    )
    return g, p


def test_skill_registry_merges_and_overrides(skill_dirs):
    g, p = skill_dirs
    reg = SkillRegistry(project_dir=p, global_dir=g)
    names = {s.name for s in reg.list()}
    assert names == {"review", "local"}

    review = reg.get("review")
    assert review.body == "project review"
    assert review.source == "project"

    local = reg.get("local")
    assert local.source == "project"
    assert local.description == "Project-only helper"
    assert local.body == "local only"


def test_parse_skill_frontmatter(tmp_path):
    path = tmp_path / "tdd.md"
    path.write_text(
        "---\ndescription: Test-driven development workflow\n---\n"
        "1. Write a failing test\n2. Make it pass\n"
    )
    skill = parse_skill_file(path, source="project")
    assert skill.name == "tdd"
    assert skill.description == "Test-driven development workflow"
    assert "failing test" in skill.body


def test_catalog_section_lists_skills(skill_dirs):
    g, p = skill_dirs
    reg = SkillRegistry(project_dir=p, global_dir=g)
    text = catalog_section(reg.list())
    assert "<available-skills>" in text
    assert "review:" in text
    assert "local:" in text
    assert "Project-only helper" in text


def test_format_skill_prompt_includes_args(skill_dirs):
    g, p = skill_dirs
    reg = SkillRegistry(project_dir=p, global_dir=g)
    skill = reg.get("local")
    prompt = format_skill_prompt(skill, "fix the auth bug")
    assert "local only" in prompt
    assert "fix the auth bug" in prompt


def test_build_tools_includes_skill_when_registry_has_skills(skill_dirs):
    g, p = skill_dirs
    reg = SkillRegistry(project_dir=p, global_dir=g)
    tools = build_tools(None, include_task=True, skill_registry=reg)
    assert any(isinstance(t, SkillTool) for t in tools)
    empty = SkillRegistry(global_dir=g / "empty", project_dir=None)
    tools2 = build_tools(None, include_task=True, skill_registry=empty)
    assert not any(isinstance(t, SkillTool) for t in tools2)


async def test_skill_tool_loads_body(skill_dirs):
    from pathlib import Path

    from bytebarn.engine.tools.base import ToolContext

    g, p = skill_dirs
    reg = SkillRegistry(project_dir=p, global_dir=g)
    tool = SkillTool(reg)
    ctx = ToolContext(cwd=Path(p), session_id="s")
    result = await tool.execute(tool.Params(name="local"), ctx)
    assert not result.is_error
    assert "local only" in result.output
    assert result.title == "local"


async def test_skill_tool_unknown(skill_dirs):
    from pathlib import Path

    from bytebarn.engine.tools.base import ToolContext

    g, p = skill_dirs
    reg = SkillRegistry(project_dir=p, global_dir=g)
    tool = SkillTool(reg)
    ctx = ToolContext(cwd=Path(p), session_id="s")
    result = await tool.execute(tool.Params(name="nope"), ctx)
    assert result.is_error
    assert "unknown skill" in result.output