"""Skill library – prompt fragments agents can reference.

Skills follow the same three-layer pattern as agents:
builtin (empty for now) → global (~/.bytebarn/skills) → project (.bytebarn/skills)
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

from pydantic import BaseModel, ConfigDict


class SkillDef(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    description: str = ""
    body: str = ""
    source: str = "builtin"   # builtin | global | project


def _load_md_dir(d: Path, source: str) -> Dict[str, SkillDef]:
    skills: Dict[str, SkillDef] = {}
    if not d.exists():
        return skills
    for p in d.glob("*.md"):
        name = p.stem
        skills[name] = SkillDef(
            name=name,
            description=name.replace("_", " ").title(),
            body=p.read_text().strip(),
            source=source,
        )
    return skills


def load_skills(global_dir: Path, project_dir: Path | None = None) -> Dict[str, SkillDef]:
    skills = _load_md_dir(global_dir / "skills", "global")
    if project_dir:
        skills.update(_load_md_dir(project_dir / ".bytebarn" / "skills", "project"))
    return skills


class SkillRegistry:
    def __init__(self, global_dir: Path, project_dir: Path | None = None):
        self._global_dir = global_dir
        self._project_dir = project_dir
        self._skills = load_skills(global_dir, project_dir)

    def reload(self) -> None:
        self._skills = load_skills(self._global_dir, self._project_dir)

    def get(self, name: str) -> SkillDef | None:
        return self._skills.get(name)

    def list(self) -> list[SkillDef]:
        return sorted(self._skills.values(), key=lambda s: s.name)

    @property
    def skills(self) -> Dict[str, SkillDef]:
        return self._skills
