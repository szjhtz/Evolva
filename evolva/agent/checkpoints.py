from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from evolva.storage import atomic_write_json, read_json


CHECKPOINT_SCHEMA_VERSION = "agent-checkpoint.v1"


class AgentCheckpointStore:
    """Atomic, resumable checkpoints for the conversational agent runtime."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def path_for(self, run_id: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in run_id).strip("_")
        if not safe:
            raise ValueError("run_id cannot be empty")
        return self.directory / f"{safe}.json"

    def save(self, run_id: str, state: dict[str, Any], *, status: str = "running") -> Path:
        path = self.path_for(run_id)
        existing = read_json(path, {})
        created_at = existing.get("created_at", time.time()) if isinstance(existing, dict) else time.time()
        payload = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "run_id": run_id,
            "status": status,
            "created_at": created_at,
            "updated_at": time.time(),
            "state": state,
        }
        atomic_write_json(path, payload)
        return path

    def load(self, run_id: str) -> dict[str, Any]:
        path = self.path_for(run_id)
        data = read_json(path, {})
        if not isinstance(data, dict) or data.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
            raise ValueError(f"Invalid or missing agent checkpoint: {run_id}")
        if not isinstance(data.get("state"), dict):
            raise ValueError(f"Checkpoint state is invalid: {run_id}")
        return data

    def list(self, *, limit: int = 20, include_completed: bool = False) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in sorted(self.directory.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            data = read_json(path, {})
            if not isinstance(data, dict):
                continue
            if not include_completed and data.get("status") == "completed":
                continue
            raw_state = data.get("state")
            state: dict[str, Any] = raw_state if isinstance(raw_state, dict) else {}
            rows.append(
                {
                    "run_id": data.get("run_id", path.stem),
                    "status": data.get("status", "unknown"),
                    "updated_at": data.get("updated_at"),
                    "step": state.get("step", 0),
                    "user_message": str(state.get("user_message", ""))[:160],
                    "path": str(path),
                }
            )
            if len(rows) >= max(1, int(limit)):
                break
        return rows
