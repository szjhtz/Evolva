from __future__ import annotations

from dataclasses import dataclass

from evolva.config import AgentConfig


@dataclass(frozen=True)
class ModelRoute:
    tier: str
    candidates: tuple[str, ...]
    reason: str

    @property
    def selected(self) -> str:
        return self.candidates[0]

    def to_dict(self) -> dict[str, object]:
        return {"tier": self.tier, "selected": self.selected, "candidates": list(self.candidates), "reason": self.reason}


class ModelRouter:
    """Route work to configured model tiers with an ordered failover chain."""

    CODING_MARKERS = {"code", "bug", "fix", "implement", "refactor", "test", "代码", "修复", "实现", "重构", "测试"}
    REASONING_MARKERS = {"architecture", "design", "production", "migration", "review", "架构", "设计", "生产", "迁移", "评审", "方案"}

    def __init__(self, config: AgentConfig):
        self.config = config

    def route(self, task: str, *, recovery_attempts: int = 0, selected_tools: list[str] | None = None) -> ModelRoute:
        text = task.lower()
        tools = set(selected_tools or [])
        if not self.config.model_routing_enabled:
            return self._route("default", self.config.model, "model routing disabled")
        if recovery_attempts > 0:
            return self._route("reasoning", self.config.model_reasoning or self.config.model, "recovery requires stronger reasoning")
        if any(marker in text for marker in self.CODING_MARKERS) or {"apply_patch", "run_tests", "git_diff"} & tools:
            return self._route("coding", self.config.model_coding or self.config.model_reasoning or self.config.model, "coding task or coding tools selected")
        if any(marker in text for marker in self.REASONING_MARKERS) or len(text) > 500:
            return self._route("reasoning", self.config.model_reasoning or self.config.model, "complex reasoning task")
        return self._route("fast", self.config.model_fast or self.config.model, "simple or latency-sensitive task")

    def _route(self, tier: str, preferred: str, reason: str) -> ModelRoute:
        candidates: list[str] = []
        for model in (preferred, self.config.model, *self.config.model_fallbacks):
            model = str(model).strip()
            if model and model not in candidates:
                candidates.append(model)
        return ModelRoute(tier, tuple(candidates), reason)
