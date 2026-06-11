from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ArtifactRecord:
    """Versioned metadata for a produced local artifact."""

    path: str
    sha256: str
    size_bytes: int
    kind: str = "file"
    producer: str = ""
    run_id: str = ""
    event_id: str = ""
    created_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ArtifactManifest:
    """Append-only artifact manifest for eval, replay, and provenance.

    The manifest deliberately stores paths relative to the project root. This
    keeps traces portable across machines while still allowing sha256-based
    verification of produced files.
    """

    def __init__(self, path: Path, root: Path):
        self.path = path
        self.root = root.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record_file(
        self,
        path: Path,
        *,
        producer: str = "",
        run_id: str = "",
        event_id: str = "",
        kind: str = "file",
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRecord:
        """Record a file artifact and append it to manifest.jsonl."""

        resolved = path.resolve()
        try:
            relative = resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"artifact path escapes root: {resolved}") from exc
        digest = _sha256_file(resolved)
        stat = resolved.stat()
        record = ArtifactRecord(
            path=relative.as_posix(),
            sha256=digest,
            size_bytes=stat.st_size,
            kind=kind,
            producer=producer,
            run_id=run_id,
            event_id=event_id,
            created_at=time.time(),
            metadata=metadata or {},
        )
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
        return record

    def list(self, limit: int | None = None) -> list[ArtifactRecord]:
        """Return recorded artifacts, newest last unless limit trims from end."""

        if not self.path.exists():
            return []
        rows: list[ArtifactRecord] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(ArtifactRecord(**json.loads(line)))
            except Exception:
                continue
        if limit is not None:
            return rows[-int(limit) :]
        return rows

    def find(self, artifact_path: str) -> list[ArtifactRecord]:
        """Find manifest records by project-relative artifact path."""

        normalized = artifact_path.replace("\\", "/").lstrip("/")
        return [record for record in self.list() if record.path == normalized]


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
