from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Skill:
    name: str
    content: str
    path: Path


class SkillStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self._ensure_seed_skills()

    def _ensure_seed_skills(self) -> None:
        seed = self.directory / "general_agent.md"
        if not seed.exists():
            seed.write_text(
                "# general_agent\n\n"
                "- Break complex requests into short plans.\n"
                "- Prefer safe workspace-local file operations.\n"
                "- Verify code changes by running lightweight checks when possible.\n"
                "- After failures, capture a lesson with the cause and prevention.\n",
                encoding="utf-8",
            )

    def list(self) -> list[Skill]:
        skills: list[Skill] = []
        for path in sorted(self.directory.glob("*.md")):
            skills.append(Skill(path.stem, path.read_text(encoding="utf-8"), path))
        return skills

    def context(self, query: str = "") -> str:
        skills = self.list()
        if not skills:
            return "No skills."
        q = query.lower()
        selected = []
        for skill in skills:
            hay = (skill.name + "\n" + skill.content).lower()
            if not q or any(token in hay for token in q.split()):
                selected.append(skill)
        if not selected:
            selected = skills[:3]
        return "\n\n".join(f"## {s.name}\n{s.content[:1500]}" for s in selected[:5])

    def upsert(self, name: str, content: str, *, metadata: dict[str, Any] | None = None) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip().lower()).strip("_") or f"skill_{int(time.time())}"
        path = self.directory / f"{safe}.md"
        body = content.strip()
        if metadata:
            body = self._with_frontmatter(safe, body, metadata)
        if path.exists():
            old = path.read_text(encoding="utf-8")
            if body not in old and content.strip() not in old:
                path.write_text(old.rstrip() + "\n\n" + body + "\n", encoding="utf-8")
        else:
            path.write_text(f"# {safe}\n\n{body}\n", encoding="utf-8")
        return path

    def stats(self) -> dict[str, int]:
        skills = self.list()
        evolved = sum(1 for s in skills if "source: self_evolution" in s.content or s.name.startswith("evolved_"))
        return {"total": len(skills), "evolved": evolved}

    def _with_frontmatter(self, safe_name: str, content: str, metadata: dict[str, Any]) -> str:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        data = {"name": safe_name, "status": "active", "updated_at": now, **metadata}
        if "created_at" not in data:
            data["created_at"] = now
        lines = ["---"]
        for key in sorted(data):
            value = data[key]
            if isinstance(value, (list, tuple)):
                rendered = ", ".join(str(x) for x in value)
            else:
                rendered = str(value)
            lines.append(f"{key}: {rendered}")
        lines.append("---")
        return "\n".join(lines) + "\n\n" + content
