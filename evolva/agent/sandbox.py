from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from evolva.tools.base import ToolResult


DANGEROUS_SHELL = [
    "rm -rf /",
    "git reset --hard",
    "mkfs",
    ":(){:|:&};:",
    "shutdown",
    "reboot",
]


@dataclass(frozen=True)
class SandboxPolicy:
    root: Path
    workspace: Path
    allow_shell: bool = True
    default_timeout: int = 30
    backend: str = "local"


class SandboxBackend(Protocol):
    """Execution backend contract for local-first sandbox implementations."""

    name: str

    def run_shell(self, command: str, *, cwd: Path, timeout: int) -> ToolResult: ...

    def run_python(self, code: str, *, cwd: Path, timeout: int) -> ToolResult: ...


class LocalWorkspaceBackend:
    """Default backend: execute commands inside the workspace root."""

    name = "local"

    def run_shell(self, command: str, *, cwd: Path, timeout: int) -> ToolResult:
        try:
            proc = subprocess.run(command, cwd=cwd, shell=True, text=True, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            return ToolResult(False, f"Command timed out after {timeout}s: {exc}")
        output = (proc.stdout + proc.stderr).strip()
        return ToolResult(proc.returncode == 0, output or f"exit={proc.returncode}", {"returncode": proc.returncode, "backend": self.name})

    def run_python(self, code: str, *, cwd: Path, timeout: int) -> ToolResult:
        try:
            proc = subprocess.run(["python3", "-c", code], cwd=cwd, text=True, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            return ToolResult(False, f"Python timed out after {timeout}s: {exc}")
        output = (proc.stdout + proc.stderr).strip()
        return ToolResult(proc.returncode == 0, output or f"exit={proc.returncode}", {"returncode": proc.returncode, "backend": self.name})


class Sandbox:
    """Workspace-aware sandbox for path resolution and local command execution."""

    def __init__(self, policy: SandboxPolicy, backend: SandboxBackend | None = None):
        self.policy = SandboxPolicy(policy.root.resolve(), policy.workspace.resolve(), policy.allow_shell, policy.default_timeout, policy.backend)
        self.backend = backend or LocalWorkspaceBackend()
        self.policy.workspace.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self.policy.root

    @property
    def workspace(self) -> Path:
        return self.policy.workspace

    def resolve(self, path: str | Path, *, base: Path | None = None, must_be_under_root: bool = True) -> Path:
        base_path = (base or self.root).resolve()
        candidate = (base_path / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
        if must_be_under_root:
            try:
                candidate.relative_to(self.root)
            except ValueError as exc:
                raise ValueError(f"Path escapes sandbox root: {candidate}") from exc
        return candidate

    def describe(self) -> str:
        return f"Sandbox root={self.root}; workspace={self.workspace}; shell={'enabled' if self.policy.allow_shell else 'disabled'}; backend={self.backend.name}"

    def run_shell(self, command: str, *, cwd: str = ".", timeout: int | None = None) -> ToolResult:
        if not self.policy.allow_shell:
            return ToolResult(False, "Shell execution is disabled by sandbox policy")
        lowered = command.lower()
        if any(bad in lowered for bad in DANGEROUS_SHELL):
            return ToolResult(False, f"Blocked dangerous command: {command}")
        cwd_path = self.resolve(cwd)
        if not cwd_path.is_dir():
            return ToolResult(False, f"cwd is not a directory: {cwd_path}")
        return self.backend.run_shell(command, cwd=cwd_path, timeout=timeout or self.policy.default_timeout)

    def run_python(self, code: str, *, timeout: int = 10) -> ToolResult:
        return self.backend.run_python(code, cwd=self.root, timeout=timeout)
