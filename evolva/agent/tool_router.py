from __future__ import annotations

from dataclasses import dataclass

from evolva.agent.relevance import relevance_score
from evolva.tools.base import ToolRegistry


@dataclass(frozen=True)
class ToolSelection:
    names: list[str]
    scores: dict[str, float]
    reason: str


class ToolRouter:
    """Select a compact tool surface for the current task and observation state."""

    INTENT_TOOLS = {
        "coding": (
            "代码 编程 修复 实现 测试 仓库 文件 修改 code implement fix test repo file patch",
            ("read_file", "search_text", "repo_index_search", "apply_patch", "git_diff", "run_tests", "shell", "read_file_range", "list_files", "write_file"),
        ),
        "research": (
            "搜索 网页 资料 调研 链接 网址 research search web source url compare",
            ("web_search", "web_search_pro", "web_fetch", "file_to_text"),
        ),
        "memory": (
            "记住 偏好 回忆 经验 技能 上下文 memory remember recall preference skill context",
            ("remember", "recall", "context_view", "context_add", "list_skills", "save_skill"),
        ),
        "planning": (
            "计划 任务 步骤 工作流 多代理 plan todo workflow agent delegate collaborate",
            ("todo_add", "todo_list", "todo_update", "delegate_agent", "collaborate"),
        ),
        "mcp": ("mcp 外部工具 server 服务器 plugin", ("mcp_servers", "mcp_tools", "mcp_call", "mcp_health")),
        "media": (
            "图片 视频 音频 pdf 表格 文档 image video audio spreadsheet document ocr",
            ("file_to_text", "ocr_image", "video_probe", "audio_transcribe", "pdf_extract", "spreadsheet_describe"),
        ),
    }
    DEFAULTS = ("list_files", "read_file", "repo_index_search", "recall", "context_view", "sandbox_info")

    def __init__(self, registry: ToolRegistry, *, limit: int = 8):
        self.registry = registry
        self.limit = max(1, int(limit))

    def select_report(self, task: str, *, scratch: str = "", recent_tools: tuple[str, ...] = ()) -> ToolSelection:
        query = f"{task}\n{scratch[-1500:]}".strip()
        available = set(self.registry.names())
        scores: dict[str, float] = {}
        for name in available:
            tool = self.registry.get(name)
            scores[name] = relevance_score(query, f"{name} {tool.description} {' '.join(tool.capabilities)}")

        matched_intents: list[str] = []
        for intent, (markers, names) in self.INTENT_TOOLS.items():
            intent_score = relevance_score(query, markers)
            if intent_score <= 0:
                continue
            matched_intents.append(intent)
            for rank, name in enumerate(names):
                if name in available:
                    scores[name] = scores.get(name, 0.0) + intent_score + max(0.0, 2.0 - rank * 0.12)

        for name in recent_tools:
            if name in available:
                scores[name] = scores.get(name, 0.0) + 1.5
        for name in self.DEFAULTS:
            if name in available:
                scores[name] = scores.get(name, 0.0) + 0.25

        ranked = sorted(available, key=lambda name: (-scores.get(name, 0.0), name))
        selected = [name for name in ranked if scores.get(name, 0.0) > 0][: self.limit]
        if len(selected) < min(3, self.limit):
            for name in self.DEFAULTS:
                if name in available and name not in selected:
                    selected.append(name)
                    if len(selected) >= min(3, self.limit):
                        break
        selected = selected[: self.limit]
        reason = ",".join(matched_intents) if matched_intents else "lexical/default"
        return ToolSelection(selected, {name: round(scores.get(name, 0.0), 4) for name in selected}, reason)

    def select(self, task: str, *, scratch: str = "", recent_tools: tuple[str, ...] = ()) -> list[str]:
        return self.select_report(task, scratch=scratch, recent_tools=recent_tools).names
