from pathlib import Path

import pytest

from bytebarn.engine.skills import SkillRegistry


@pytest.fixture
def skill_dirs(tmp_path):
    g = tmp_path / "global"
    p = tmp_path / "proj"
    (g / ".bytebarn" / "skills").mkdir(parents=True)
    (p / ".bytebarn" / "skills").mkdir(parents=True)
    (g / ".bytebarn" / "skills" / "review.md").write_text("global review")
    (p / ".bytebarn" / "skills" / "review.md").write_text("project review")
    (p / ".bytebarn" / "skills" / "local.md").write_text("local only")
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
