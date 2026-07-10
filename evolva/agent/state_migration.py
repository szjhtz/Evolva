from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from evolva.agent.redaction import Redactor


STATE_SCHEMA_VERSION = 2


@dataclass
class StateMigrationReport:
    root: str
    apply: bool
    scanned_files: int = 0
    changed_files: int = 0
    redacted_values: int = 0
    skipped_files: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class StateMigrator:
    """Redact legacy runtime JSON/JSONL state without retaining secret backups."""

    INCLUDED_DIRS = {
        "alerts",
        "artifacts",
        "context",
        "dreams",
        "eval_results",
        "loops",
        "loop_runs",
        "memory",
        "metrics",
        "policy",
        "sessions",
        "skills",
        "todo",
        "traces",
        "workflows",
    }

    def __init__(self, runtime_home: Path, redactor: Redactor | None = None):
        self.runtime_home = runtime_home.expanduser().resolve()
        self.redactor = redactor or Redactor()

    def run(self, *, apply: bool = False) -> StateMigrationReport:
        report = StateMigrationReport(root=str(self.runtime_home), apply=apply)
        if not self.runtime_home.exists():
            return report
        for path in sorted(self.runtime_home.rglob("*")):
            if not path.is_file() or path.suffix not in {".json", ".jsonl", ".md"}:
                continue
            relative = path.relative_to(self.runtime_home)
            if not relative.parts or relative.parts[0] not in self.INCLUDED_DIRS:
                report.skipped_files += 1
                continue
            report.scanned_files += 1
            try:
                changed, rendered, count = self._migrate_file(path)
                if not changed:
                    continue
                report.changed_files += 1
                report.redacted_values += count
                if apply:
                    self._atomic_write(path, rendered)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                report.errors.append(f"{relative}: {exc}")
        if apply and not report.errors:
            manifest = self.runtime_home / "state.json"
            self._atomic_write(
                manifest,
                json.dumps({"schema_version": STATE_SCHEMA_VERSION}, indent=2) + "\n",
            )
        return report

    def _migrate_file(self, path: Path) -> tuple[bool, str, int]:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".md":
            safe = self.redactor.redact_text(text)
            return safe != text, safe, int(safe != text)
        if path.suffix == ".jsonl":
            rendered: list[str] = []
            changed = False
            count = 0
            for line_number, line in enumerate(text.splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSONL line {line_number}") from exc
                safe, item_count = self._redact(raw)
                changed = changed or safe != raw
                count += item_count
                rendered.append(json.dumps(safe, ensure_ascii=False, separators=(",", ":")))
            return changed, "\n".join(rendered) + ("\n" if rendered else ""), count
        raw = json.loads(text)
        safe, count = self._redact(raw)
        return safe != raw, json.dumps(safe, ensure_ascii=False, indent=2) + "\n", count

    def _redact(self, value: Any) -> tuple[Any, int]:
        safe = self.redactor.redact_json(value)
        return safe, self._changed_leaf_count(value, safe)

    def _changed_leaf_count(self, before: Any, after: Any) -> int:
        if isinstance(before, dict) and isinstance(after, dict):
            return sum(self._changed_leaf_count(value, after.get(key)) for key, value in before.items())
        if isinstance(before, list) and isinstance(after, list):
            return sum(self._changed_leaf_count(a, b) for a, b in zip(before, after))
        return int(before != after)

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
