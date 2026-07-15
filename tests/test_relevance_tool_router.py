from __future__ import annotations

from dataclasses import replace

from evolva.agent.context import ContextStore
from evolva.agent.core import EvolvaAgent
from evolva.agent.memory import MemoryStore
from evolva.agent.relevance import bounded_sections, relevance_score, text_tokens


def test_cjk_tokens_and_relevance_match_related_phrases() -> None:
    tokens = text_tokens("请用中文回答 SelfEvolutionEngine")

    assert "中文" in tokens
    assert "self" in tokens
    assert relevance_score("请用中文回答", "用户偏好使用中文回复") > 0
    assert relevance_score("请用中文回答", "运行 Python 单元测试") == 0


def test_memory_and_context_retrieve_related_chinese_text(tmp_path) -> None:
    memory = MemoryStore(tmp_path / "memory.jsonl")
    context = ContextStore(tmp_path / "context.json")
    memory.add("preference", "用户偏好使用中文回复", verified=True)
    context.add("decision", "后续回答使用中文")

    assert "使用中文" in memory.context("请用中文回答")
    assert "使用中文" in context.prompt_context("请用中文回答")


def test_bounded_sections_preserve_priority_and_total_budget() -> None:
    rendered = bounded_sections(
        [("critical", "x" * 80, 3), ("optional", "y" * 80, 1)],
        max_chars=100,
        section_min_chars=20,
    )

    assert len(rendered) <= 100
    assert "critical" in rendered
    assert rendered.count("x") > rendered.count("y")


def test_agent_prompt_routes_tools_and_bounds_context(temp_config) -> None:
    config = replace(temp_config, prompt_tool_limit=8, prompt_context_max_chars=6000, prompt_history_max_chars=1000)
    agent = EvolvaAgent(config, assume_yes=True)
    for index in range(20):
        agent.context.add("note", f"unrelated-{index} " + ("z" * 500))

    messages = agent._messages("修复 Python 代码并运行测试", "")
    system = str(messages[0]["content"])
    selected = agent.tool_router.select("修复 Python 代码并运行测试")

    assert len(selected) <= 8
    assert "read_file" in selected
    assert "shell" in selected
    assert len(system) < 8000
    assert "Available tools:" in system
    assert "audio_transcribe" not in system


def test_tool_router_changes_shortlist_by_intent(temp_config) -> None:
    agent = EvolvaAgent(replace(temp_config, prompt_tool_limit=7), assume_yes=True)

    coding = agent.tool_router.select("搜索代码，修复文件并运行测试")
    research = agent.tool_router.select("搜索网页并读取这个 URL 的资料")

    assert {"read_file", "run_tests"}.issubset(coding)
    assert "web_search" in research or "web_search_pro" in research
    assert "web_fetch" in research
    assert len(coding) <= 7 and len(research) <= 7
