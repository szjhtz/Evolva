from __future__ import annotations

import json

import pytest

from evolva.agent.context import ContextStore
from evolva.agent.evolution import SelfEvolutionEngine
from evolva.agent.memory import MemoryStore
from evolva.agent.skills import SkillStore
from evolva.agent.todo import TodoStore


def test_memory_ignores_empty_and_malformed_rows(tmp_path):
    path = tmp_path / "memory.jsonl"
    store = MemoryStore(path)
    empty = store.add("fact", "   ")
    assert empty.content == ""
    assert not path.exists()

    path.write_text('{"kind":"fact","content":"alpha beta","confidence":0.9,"source":"test","ts":1}\nnot-json\n')
    assert [m.content for m in store.search("alpha")] == ["alpha beta"]
    assert "alpha beta" in store.context("beta")


def test_memory_search_ranks_exact_matches(tmp_path):
    store = MemoryStore(tmp_path / "memory.jsonl")
    store.add("fact", "Use pytest for unit tests", source="a")
    store.add("lesson", "Always run compileall", source="b")
    assert store.search("pytest")[0].content == "Use pytest for unit tests"
    assert len(store.all(limit=1)) == 1


def test_skill_store_seeds_sanitizes_and_appends(tmp_path):
    skills = SkillStore(tmp_path / "skills")
    assert "general_agent" in [s.name for s in skills.list()]
    path = skills.upsert("Check Python!!!", "Run py_compile")
    assert path.name == "check_python.md"
    skills.upsert("Check Python!!!", "Run pytest")
    text = path.read_text()
    assert "Run py_compile" in text and "Run pytest" in text
    assert "check_python" in skills.context("pytest")


def test_context_store_caps_searches_and_compacts(tmp_path):
    context = ContextStore(tmp_path / "context.json", max_items=3)
    context.add("note", "first old")
    context.add("decision", "Use sandbox", role="planner", meta={"area": "safety"})
    context.add("artifact", "Generated report")
    context.add("message", "latest message", role="user")

    assert "first old" not in context.render(limit=10)
    assert "Use sandbox" in context.render("safety")
    summary = context.compact("Daily summary")
    assert summary.kind == "summary"
    assert summary.meta["source_items"] == 3
    assert "Daily summary" in context.prompt_context("summary")


def test_context_store_rejects_empty_content(tmp_path):
    with pytest.raises(ValueError, match="required"):
        ContextStore(tmp_path / "context.json").add("note", " ")


def test_todo_store_lifecycle_errors_and_clear(tmp_path):
    todos = TodoStore(tmp_path / "todos.json")
    with pytest.raises(ValueError, match="title"):
        todos.add(" ")
    first = todos.add("Implement tests", detail="pytest", owner="coder")
    second = todos.add("Review", owner="reviewer")
    assert second.id == first.id + 1
    assert "Implement tests" in todos.context()
    todos.update(first.id, status="done")
    assert "Implement tests" not in todos.render(include_done=False)
    assert todos.clear() == 1
    assert "Review" in todos.render()
    assert todos.clear(include_done=True) == 1
    assert todos.render() == "No todos."
    with pytest.raises(KeyError):
        todos.update(999, status="done")
    item = todos.add("Validate bad status")
    with pytest.raises(ValueError, match="invalid status"):
        todos.update(item.id, status="bad")


def test_self_evolution_records_memory_and_skill(tmp_path):
    memory = MemoryStore(tmp_path / "memory.jsonl")
    skills = SkillStore(tmp_path / "skills")
    report = SelfEvolutionEngine(memory, skills).evolve("Prefer tests", task="edit code", outcome="ok")
    assert "Prefer tests" in report.lesson
    assert report.skill_path
    assert "Prefer tests" in memory.context("Prefer")
    assert "Procedure" in skills.context("Prefer")


def test_reflect_after_turn_only_for_failures_or_long_answer(tmp_path):
    engine = SelfEvolutionEngine(MemoryStore(tmp_path / "memory.jsonl"), SkillStore(tmp_path / "skills"))
    assert engine.reflect_after_turn("task", "short", []) is None
    assert engine.reflect_after_turn("task", "short", ["shell"]) is not None
    assert engine.reflect_after_turn("task", "x" * 4001, []) is not None
