"""Skill discovery (global + project).

Skills are plain markdown files stored under:
  <global>/.crew/skills/<name>.md
  <project>/.crew/skills/<name>.md

Project skills override global skills of the same name.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple


class Skill(NamedTuple):
    name: str
    body: str
    source: str  # "global" | "project"


def _discover(base: Path | None) -> dict[str, Skill]:
    if not base:
        return {}
    skills_dir = base / ".crew" / "skills"
    if not skills_dir.is_dir():
        return {}
    out: dict[str, Skill] = {}
    for p in sorted(skills_dir.glob("*.md")):
        name = p.stem
        out[name] = Skill(name=name, body=p.read_text().strip(), source="global" if base.parent == base else "project")
    return out


class SkillRegistry:
    def __init__(self, project_dir: Path | None = None, global_dir: Path | None = None):
        self.project_dir = Path(project_dir) if project_dir else None
        self.global_dir = Path(global_dir) if global_dir else None
        self.reload()

    def reload(self) -> None:
        g = _discover(self.global_dir)
        p = _discover(self.project_dir)
        # project wins
        merged: dict[str, Skill] = {}
        for name, sk in g.items():
            merged[name] = Skill(name, sk.body, "global")
        for name, sk in p.items():
            merged[name] = Skill(name, sk.body, "project")
        self.skills = merged

    def list(self) -> list[Skill]:
        return sorted(self.skills.values(), key=lambda s: s.name)

    def get(self, name: str) -> Skill | None:
        return self.skills.get(name)
