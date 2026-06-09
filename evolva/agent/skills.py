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
    metadata: dict[str, Any] | None = None


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
            content = path.read_text(encoding="utf-8")
            metadata, body = self._parse_frontmatter(content)
            skills.append(Skill(path.stem, body, path, metadata))
        return skills

    def match(self, query: str, *, limit: int = 5) -> list[Skill]:
        """Return skills selected by manifest triggers and lexical relevance."""
        q = query.lower().strip()
        scored: list[tuple[int, Skill]] = []
        for skill in self.list():
            metadata = skill.metadata or {}
            triggers = self._metadata_list(metadata.get("triggers")) + self._metadata_list(metadata.get("keywords"))
            hay = (skill.name + "\n" + skill.content + "\n" + " ".join(triggers)).lower()
            score = sum(2 for trigger in triggers if trigger.lower() and trigger.lower() in q)
            score += sum(1 for token in q.split() if token in hay)
            if not q or score:
                scored.append((score, skill))
        scored.sort(key=lambda item: (item[0], item[1].name), reverse=True)
        return [skill for _, skill in scored[:limit]]

    def context(self, query: str = "") -> str:
        skills = self.list()
        if not skills:
            return "No skills."
        selected = self.match(query, limit=5)
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

    def _parse_frontmatter(self, content: str) -> tuple[dict[str, Any], str]:
        if not content.startswith("---\n"):
            return {}, content
        end = content.find("\n---", 4)
        if end < 0:
            return {}, content
        raw = content[4:end]
        body = content[content.find("\n", end + 4) + 1 :]
        metadata: dict[str, Any] = {}
        for line in raw.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            value = value.strip()
            if "," in value:
                metadata[key.strip()] = [item.strip() for item in value.split(",") if item.strip()]
            else:
                metadata[key.strip()] = value
        return metadata, body

    def _metadata_list(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, tuple):
            return [str(item) for item in value]
        return [str(value)]

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
