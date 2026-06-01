from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from evolva.agent.memory import MemoryStore
from evolva.agent.skills import SkillStore


@dataclass
class EvolutionReport:
    lesson: str
    trigger: str = "manual_feedback"
    category: str = "general"
    actions: list[str] = field(default_factory=list)
    confidence: float = 0.75
    skill_name: str | None = None
    skill_path: str | None = None
    memory_written: bool = False
    deduped: bool = False
    ts: float = 0.0

    def __post_init__(self) -> None:
        if not self.ts:
            self.ts = time.time()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        status = "deduped" if self.deduped else "created"
        return f"[{self.trigger}/{self.category}/{status}] {self.lesson}"


class SelfEvolutionEngine:
    """Turns feedback and reflections into persistent lessons and skills."""

    def __init__(self, memory: MemoryStore, skills: SkillStore):
        self.memory = memory
        self.skills = skills

    def evolve(self, feedback: str, *, task: str = "", outcome: str = "", trigger: str = "manual_feedback") -> EvolutionReport:
        feedback = feedback.strip() or "No explicit feedback; summarize recent outcome."
        category = self._classify(feedback, task=task, outcome=outcome, trigger=trigger)
        actions = self._actions_for(category)
        confidence = self._confidence_for(trigger, category)
        lesson = self._lesson_from(feedback, task=task, outcome=outcome, category=category, actions=actions)

        duplicate = self.memory.find_similar("lesson", lesson)
        memory_written = duplicate is None
        if memory_written:
            self.memory.add("lesson", lesson, confidence=confidence, source=f"self_evolution:{trigger}:{category}")

        skill_name = self._skill_name(feedback or task or lesson, category=category)
        skill_body = self._skill_body(lesson, category=category, actions=actions)
        path = self.skills.upsert(
            skill_name,
            skill_body,
            metadata={
                "source": "self_evolution",
                "trigger": trigger,
                "category": category,
                "confidence": f"{confidence:.2f}",
                "deduped": str(duplicate is not None).lower(),
            },
        )
        return EvolutionReport(
            lesson=lesson,
            trigger=trigger,
            category=category,
            actions=actions,
            confidence=confidence,
            skill_name=skill_name,
            skill_path=str(path),
            memory_written=memory_written,
            deduped=duplicate is not None,
        )

    def reflect_after_turn(self, user_message: str, final_answer: str, failed_tools: list[str]) -> EvolutionReport | None:
        if not failed_tools and len(final_answer) < 4000:
            return None
        if failed_tools:
            feedback = "Tool failures occurred: " + ", ".join(failed_tools)
            trigger = "tool_failure"
        else:
            feedback = "Long answer; improve concision and verification."
            trigger = "quality_signal"
        return self.evolve(feedback, task=user_message, outcome=final_answer[:1000], trigger=trigger)

    def status(self, *, recent: int = 5) -> dict[str, Any]:
        lessons = [m for m in self.memory.all(1000) if m.kind == "lesson"]
        lesson_sources: dict[str, int] = {}
        for lesson in lessons:
            lesson_sources[lesson.source] = lesson_sources.get(lesson.source, 0) + 1
        recent_lessons = lessons[-recent:]
        skill_stats = self.skills.stats()
        return {
            "total_lessons": len(lessons),
            "memory_stats": self.memory.stats(),
            "skill_stats": skill_stats,
            "lesson_sources": lesson_sources,
            "recent_lessons": [m.content for m in recent_lessons],
        }

    def render_status(self) -> str:
        status = self.status()
        lines = [
            "Evolution status",
            f"- Total lessons: {status['total_lessons']}",
            f"- Skills: {status['skill_stats']['total']} total, {status['skill_stats']['evolved']} evolved",
        ]
        if status["lesson_sources"]:
            sources = ", ".join(f"{k}={v}" for k, v in sorted(status["lesson_sources"].items()))
            lines.append(f"- Lesson sources: {sources}")
        if status["recent_lessons"]:
            lines.append("- Recent lessons:")
            lines.extend(f"  - {lesson[:180]}" for lesson in status["recent_lessons"])
        else:
            lines.append("- Recent lessons: none")
        return "\n".join(lines)

    def _classify(self, feedback: str, *, task: str, outcome: str, trigger: str) -> str:
        text = " ".join([feedback, task, outcome, trigger]).lower()
        if "tool" in text or "shell" in text or "failed" in text or "error" in text or "失败" in text or "报错" in text:
            return "tool_failure"
        if "test" in text or "verify" in text or "check" in text or "验证" in text or "测试" in text:
            return "verification"
        if "safe" in text or "policy" in text or "guard" in text or "安全" in text or "权限" in text:
            return "safety"
        if "prefer" in text or "style" in text or "format" in text or "偏好" in text or "风格" in text:
            return "preference"
        if "plan" in text or "workflow" in text or "todo" in text or "流程" in text or "规划" in text:
            return "workflow"
        if "long answer" in text or "concise" in text or "简洁" in text or "太长" in text:
            return "quality"
        return "general"

    def _actions_for(self, category: str) -> list[str]:
        mapping = {
            "tool_failure": ["Inspect tool arguments before execution", "Prefer workspace-local paths", "Record the failing command and verify the fix"],
            "verification": ["Add an explicit verification step", "Run the lightest relevant check", "Report verification evidence"],
            "safety": ["Check policy before risky actions", "Ask for confirmation when required", "Prefer reversible operations"],
            "preference": ["Apply the user preference early", "Keep response style consistent", "Ask only when preference conflicts with task success"],
            "workflow": ["Create a short plan", "Track multi-step work with todos", "Close the loop with a final status"],
            "quality": ["Prefer concise answers", "State the outcome first", "Include only necessary details and verification"],
            "general": ["Identify when the lesson applies", "Turn it into a pre-action checklist", "Verify before final response"],
        }
        return mapping.get(category, mapping["general"])

    def _confidence_for(self, trigger: str, category: str) -> float:
        if trigger == "tool_failure":
            return 0.88
        if trigger == "manual_feedback":
            return 0.82 if category != "general" else 0.78
        if trigger == "quality_signal":
            return 0.72
        return 0.75

    def _lesson_from(self, feedback: str, *, task: str, outcome: str, category: str, actions: list[str]) -> str:
        bits = [f"Category: {category}", f"Feedback: {feedback}"]
        if task:
            bits.append(f"Task context: {task[:300]}")
        if outcome:
            bits.append(f"Outcome context: {outcome[:300]}")
        bits.append("Future behavior: " + "; ".join(actions))
        return " | ".join(bits)

    def _skill_body(self, lesson: str, *, category: str, actions: list[str]) -> str:
        checklist = "\n".join(f"{idx}. {action}." for idx, action in enumerate(actions, start=1))
        return (
            f"## Lesson\n{lesson}\n\n"
            f"## Applies When\n- A future task matches the `{category}` pattern or similar user feedback appears.\n\n"
            f"## Checklist\n{checklist}\n\n"
            "## Verification\n- Confirm the checklist was applied before the final response.\n"
            "- If the behavior fails again, record a more specific lesson and update this skill.\n"
        )

    def _skill_name(self, text: str, *, category: str = "general") -> str:
        words = re.findall(r"[a-zA-Z0-9\u4e00-\u9fff]+", text.lower())[:5]
        if not words:
            return f"evolved_{category}"
        # Keep ASCII-ish names readable; Chinese words are converted later by SkillStore sanitizer.
        return "evolved_" + category + "_" + "_".join(words)
