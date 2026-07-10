from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from evolva.agent.redaction import Redactor
from evolva.storage import atomic_update_json, atomic_write_json, atomic_write_text, read_json


SESSION_SCHEMA_VERSION = 1


@dataclass
class SessionMessage:
    role: str
    content: str
    ts: float = field(default_factory=time.time)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentSession:
    id: str
    name: str
    created_at: float
    updated_at: float
    messages: list[SessionMessage] = field(default_factory=list)
    parent_id: str = ""
    schema_version: int = SESSION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SessionStore:
    """Versioned project-local conversation sessions with safe persistence."""

    def __init__(self, directory: Path, *, redactor: Redactor | None = None):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.current_file = self.directory / "current"
        self.redactor = redactor or Redactor()

    def ensure_current(self) -> AgentSession:
        current_id = self.current_id()
        if current_id:
            current = self.load(current_id)
            if current is not None:
                return current
        return self.create("Default")

    def create(self, name: str = "New session", *, parent_id: str = "", messages: list[SessionMessage] | None = None) -> AgentSession:
        now = time.time()
        session_id = "session_" + time.strftime("%Y%m%d_%H%M%S", time.gmtime(now)) + "_" + uuid.uuid4().hex[:8]
        session = AgentSession(session_id, name.strip() or "New session", now, now, list(messages or []), parent_id=parent_id)
        self._write(session)
        self.set_current(session.id)
        return session

    def load(self, session_id: str) -> AgentSession | None:
        path = self.path_for(session_id)
        if not path.exists():
            return None
        raw = read_json(path, {})
        if not isinstance(raw, dict):
            return None
        try:
            messages = [SessionMessage(**row) for row in raw.get("messages", []) if isinstance(row, dict)]
            return AgentSession(
                id=str(raw["id"]),
                name=str(raw.get("name") or "Session"),
                created_at=float(raw.get("created_at") or 0),
                updated_at=float(raw.get("updated_at") or 0),
                messages=messages,
                parent_id=str(raw.get("parent_id") or ""),
                schema_version=int(raw.get("schema_version") or 1),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def list(self, limit: int = 50) -> list[AgentSession]:
        sessions = [session for path in self.directory.glob("session_*.json") if (session := self.load(path.stem)) is not None]
        sessions.sort(key=lambda item: item.updated_at, reverse=True)
        return sessions[: max(1, int(limit))]

    def append(self, session_id: str, role: str, content: str, *, meta: dict[str, Any] | None = None) -> AgentSession:
        path = self.path_for(session_id)
        safe_content = self.redactor.redact_text(content)
        safe_meta = self.redactor.redact_json(meta or {})

        def update(raw: Any) -> dict[str, Any]:
            if not isinstance(raw, dict) or raw.get("id") != session_id:
                raise KeyError(f"session not found: {session_id}")
            rows = list(raw.get("messages", []))
            rows.append(asdict(SessionMessage(role=role, content=safe_content, meta=safe_meta if isinstance(safe_meta, dict) else {})))
            raw["messages"] = rows
            raw["updated_at"] = time.time()
            raw["schema_version"] = SESSION_SCHEMA_VERSION
            return raw

        atomic_update_json(path, {}, update)
        session = self.load(session_id)
        if session is None:
            raise RuntimeError(f"failed to reload session: {session_id}")
        return session

    def rename(self, session_id: str, name: str) -> AgentSession:
        clean = name.strip()
        if not clean:
            raise ValueError("session name is required")

        def update(raw: Any) -> dict[str, Any]:
            if not isinstance(raw, dict) or raw.get("id") != session_id:
                raise KeyError(f"session not found: {session_id}")
            raw["name"] = self.redactor.redact_text(clean)
            raw["updated_at"] = time.time()
            return raw

        atomic_update_json(self.path_for(session_id), {}, update)
        session = self.load(session_id)
        if session is None:
            raise RuntimeError(f"failed to reload session: {session_id}")
        return session

    def fork(self, session_id: str, name: str = "") -> AgentSession:
        source = self.load(session_id)
        if source is None:
            raise KeyError(f"session not found: {session_id}")
        copied = [SessionMessage(message.role, message.content, message.ts, dict(message.meta)) for message in source.messages]
        return self.create(name or f"{source.name} (fork)", parent_id=source.id, messages=copied)

    def last_user_message(self, session_id: str) -> str:
        session = self.load(session_id)
        if session is None:
            raise KeyError(f"session not found: {session_id}")
        for message in reversed(session.messages):
            if message.role == "user":
                return message.content
        return ""

    def set_current(self, session_id: str) -> None:
        if self.load(session_id) is None:
            raise KeyError(f"session not found: {session_id}")
        atomic_write_text(self.current_file, session_id + "\n")

    def current_id(self) -> str:
        if not self.current_file.exists():
            return ""
        return self.current_file.read_text(encoding="utf-8").strip()

    def path_for(self, session_id: str) -> Path:
        safe = session_id.replace("/", "_").replace("..", "_")
        return self.directory / f"{safe}.json"

    def _write(self, session: AgentSession) -> None:
        payload = self.redactor.redact_json(session.to_dict())
        atomic_write_json(self.path_for(session.id), payload)
