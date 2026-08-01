"""Skill library – reusable prompt instructions agents can load on demand.

Skills follow the same layering as agents/commands:
  global (~/.bytebarn/skills/*.md) → project (<project>/.bytebarn/skills/*.md)
Project wins per name. Optional YAML frontmatter supplies ``description``;
the body is the skill instructions.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

import yaml
from pydantic import BaseModel, ConfigDict


class SkillDef(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    description: str = ""
    body: str = ""
    source: str = "builtin"   # builtin | global | project


def parse_skill_file(path: Path, source: str = "project") -> SkillDef:
    """Parse a skill markdown file (optional YAML frontmatter)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    name = path.stem
    description = name.replace("_", "-").replace("-", " ").strip().title()
    body = text.strip()
    if text.lstrip().startswith("---"):
        # split on first two --- fences
        rest = text.lstrip()[3:]
        end = rest.find("\n---")
        if end != -1:
            front_raw = rest[:end]
            body = rest[end + 4:].strip()  # after \n---
            try:
                front = yaml.safe_load(front_raw) or {}
            except yaml.YAMLError:
                front = {}
            if isinstance(front, dict):
                if front.get("description"):
                    description = str(front["description"]).strip()
                if front.get("name"):
                    name = str(front["name"]).strip() or name
    return SkillDef(name=name, description=description, body=body, source=source)


def _load_md_dir(d: Path, source: str) -> Dict[str, SkillDef]:
    skills: Dict[str, SkillDef] = {}
    if not d.is_dir():
        return skills
    for p in sorted(d.glob("*.md")):
        try:
            skill = parse_skill_file(p, source=source)
        except OSError:
            continue
        skills[skill.name] = skill
    return skills


def load_skills(global_dir: Path, project_dir: Path | None = None) -> Dict[str, SkillDef]:
    # global_dir is ~/.bytebarn — skills live at ~/.bytebarn/skills/
    skills = _load_md_dir(global_dir / "skills", "global")
    if project_dir:
        skills.update(_load_md_dir(project_dir / ".bytebarn" / "skills", "project"))
    return skills


def catalog_section(skills: list[SkillDef]) -> str:
    """System-prompt block listing skills the agent can load via the skill tool."""
    if not skills:
        return ""
    lines = [
        "<available-skills>",
        "Reusable skill instructions. When a skill matches the user's task,",
        "call the skill tool with its name to load the full guidance before acting.",
        "Prefer a matching skill over improvising a known workflow.",
        "Users can also force a skill with /skill <name> [args].",
    ]
    for s in skills:
        desc = s.description or s.name
        lines.append(f"- {s.name}: {desc}")
    lines.append("</available-skills>")
    return "\n".join(lines)


def format_skill_prompt(skill: SkillDef, arguments: str = "") -> str:
    """User-prompt expansion for /skill <name> [args]."""
    parts = [
        f"Follow the skill `{skill.name}` carefully:",
        "",
        skill.body.strip(),
    ]
    if arguments.strip():
        parts += ["", "User request:", arguments.strip()]
    return "\n".join(parts)


class SkillRegistry:
    def __init__(self, global_dir: Path, project_dir: Path | None = None):
        self._global_dir = global_dir
        self._project_dir = project_dir
        self._skills = load_skills(global_dir, project_dir)

    def reload(self) -> None:
        self._skills = load_skills(self._global_dir, self._project_dir)

    def get(self, name: str) -> SkillDef | None:
        if name in self._skills:
            return self._skills[name]
        lower = name.lower()
        for s in self._skills.values():
            if s.name.lower() == lower:
                return s
        return None

    def list(self) -> list[SkillDef]:
        return sorted(self._skills.values(), key=lambda s: s.name)

    @property
    def skills(self) -> Dict[str, SkillDef]:
        return self._skills
