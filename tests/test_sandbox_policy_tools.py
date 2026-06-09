from __future__ import annotations

import pytest

from evolva.agent.context import ContextStore
from evolva.agent.memory import MemoryStore
from evolva.agent.policy import PolicyConfig, PolicyEngine
from evolva.agent.sandbox import Sandbox, SandboxPolicy
from evolva.agent.skills import SkillStore
from evolva.agent.todo import TodoStore
from evolva.tools.base import Tool, ToolRegistry, ToolResult
from evolva.tools.builtin import build_registry


def make_registry(tmp_path):
    sandbox = Sandbox(SandboxPolicy(tmp_path, tmp_path / "workspace"))
    memory = MemoryStore(tmp_path / "memory.jsonl")
    skills = SkillStore(tmp_path / "skills")
    context = ContextStore(tmp_path / "context.json")
    todos = TodoStore(tmp_path / "todos.json")
    return build_registry(sandbox, memory, skills, context, todos), context


def test_sandbox_resolve_and_describe(tmp_path):
    sandbox = Sandbox(SandboxPolicy(tmp_path, tmp_path / "workspace"))
    assert sandbox.resolve("workspace/a.txt") == (tmp_path / "workspace" / "a.txt").resolve()
    assert "shell=enabled" in sandbox.describe()
    assert "backend=local" in sandbox.describe()
    with pytest.raises(ValueError, match="escapes"):
        sandbox.resolve("../escape.txt")


def test_sandbox_shell_disabled_dangerous_timeout_and_python(tmp_path):
    disabled = Sandbox(SandboxPolicy(tmp_path, tmp_path / "workspace", allow_shell=False))
    assert not disabled.run_shell("echo hi").ok

    sandbox = Sandbox(SandboxPolicy(tmp_path, tmp_path / "workspace"))
    assert not sandbox.run_shell("git reset --hard").ok
    assert not sandbox.run_shell("echo hi", cwd="missing").ok
    ok = sandbox.run_shell("printf hello", cwd=".")
    assert ok.ok and ok.output == "hello"
    assert ok.data["backend"] == "local"
    py_ok = sandbox.run_python("print('py')")
    assert py_ok.ok and py_ok.output == "py"
    py_bad = sandbox.run_python("raise SystemExit(3)")
    assert not py_bad.ok and py_bad.data["returncode"] == 3


def test_policy_decisions_for_network_shell_paths_and_secrets(tmp_path):
    policy = PolicyEngine(PolicyConfig(root=tmp_path, workspace=tmp_path / "workspace", network_enabled=False))
    assert not policy.check_tool("web_search", {"query": "x"}).allowed
    assert not policy.check_tool("shell", {"command": "rm -rf /"}).allowed
    secret = policy.check_tool("write_file", {"path": "a.txt", "content": "api_key='1234567890'"})
    assert secret.allowed and secret.requires_confirmation and secret.risk == "high"
    assert not policy.check_tool("read_file", {"path": "../secret"}).allowed
    assert policy.check_tool("list_files", {"path": "."}).allowed
    assert "denied_shell_patterns" in policy.as_tool_result().output


def test_tool_registry_register_get_call_describe_errors():
    reg = ToolRegistry()
    reg.register(Tool("ok", "demo", {"x": "int"}, lambda x: ToolResult(True, str(x))))
    assert reg.names() == ["ok"]
    assert "demo" in reg.describe()
    assert reg.call("ok", {"x": 7}).output == "7"
    with pytest.raises(KeyError, match="Unknown tool"):
        reg.get("missing")


def test_builtin_file_memory_context_todo_and_policy_tools(tmp_path):
    reg, context = make_registry(tmp_path)
    assert "write_file" in reg.names()
    assert reg.call("write_file", {"path": "workspace/a.txt", "content": "hello"}).ok
    assert reg.call("write_file", {"path": "workspace/a.txt", "content": " world", "append": True}).ok
    read = reg.call("read_file", {"path": "workspace/a.txt"})
    assert read.ok and read.output == "hello world"
    assert "Wrote file" in context.render("a.txt")
    assert "file\tworkspace/a.txt" in reg.call("list_files", {"path": "workspace"}).output
    assert not reg.call("read_file", {"path": "missing.txt"}).ok

    assert reg.call("remember", {"kind": "fact", "content": "pytest matters"}).ok
    assert "pytest" in reg.call("recall", {"query": "pytest"}).output
    assert reg.call("save_skill", {"name": "Testing", "content": "Run pytest"}).ok
    assert "testing" in reg.call("list_skills", {}).output
    assert reg.call("context_add", {"kind": "note", "content": "note"}).ok
    assert "note" in reg.call("context_view", {"query": "note"}).output
    assert "Compacted" in reg.call("context_compact", {"title": "summary"}).output
    todo = reg.call("todo_add", {"title": "task"})
    assert todo.ok and "#1" in todo.output
    assert "task" in reg.call("todo_list", {}).output
    assert "done" in reg.call("todo_update", {"todo_id": 1, "status": "done"}).output
    assert "Cleared 1" in reg.call("todo_clear", {}).output


def test_builtin_shell_python_policy_mcp_and_delegate_absent(tmp_path):
    reg, _ = make_registry(tmp_path)
    assert "Sandbox root" in reg.call("sandbox_info", {}).output
    assert not reg.call("policy_info", {}).ok
    assert not reg.call("mcp_servers", {}).ok
    assert not reg.call("mcp_tools", {"server": "x"}).ok
    assert not reg.call("mcp_call", {"server": "x", "tool": "y"}).ok
    assert not reg.call("delegate_agent", {"role": "planner", "task": "x"}).ok
    assert not reg.call("collaborate", {"task": "x"}).ok
    py = reg.call("python_exec", {"code": "print(2 + 3)"})
    assert py.ok and py.output == "5"
