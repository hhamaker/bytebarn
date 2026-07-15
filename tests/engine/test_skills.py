from pathlib import Path

import pytest

from crew.engine.skills import SkillRegistry


@pytest.fixture
def skill_dirs(tmp_path):
    g = tmp_path / "global"
    p = tmp_path / "proj"
    (g / ".crew" / "skills").mkdir(parents=True)
    (p / ".crew" / "skills").mkdir(parents=True)
    (g / ".crew" / "skills" / "review.md").write_text("global review")
    (p / ".crew" / "skills" / "review.md").write_text("project review")
    (p / ".crew" / "skills" / "local.md").write_text("local only")
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
