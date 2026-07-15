from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from typing import Any

from evolva.agent.memory import MemoryStore
from evolva.agent.skills import SkillStore


@dataclass
class EvolutionReport:
    lesson: str
    trigger: str = "manual_feedback"
    category: str = "general"
    actions: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.75
    fingerprint: str = ""
    skill_name: str | None = None
    skill_path: str | None = None
    memory_written: bool = False
    deduped: bool = False
    asset_status: str = "candidate"
    promotion_ready: bool = False
    ts: float = 0.0

    def __post_init__(self) -> None:
        if not self.ts:
            self.ts = time.time()
        if not self.fingerprint:
            self.fingerprint = self.compute_fingerprint(self.lesson, self.category)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        status = "deduped" if self.deduped else "created"
        return f"[{self.trigger}/{self.category}/{self.asset_status}/{status}/{self.fingerprint}] {self.lesson}"

    @staticmethod
    def compute_fingerprint(lesson: str, category: str) -> str:
        normalized = " ".join(f"{category} {lesson}".lower().split())
        return sha256(normalized.encode("utf-8")).hexdigest()[:12]


class SelfEvolutionEngine:
    """Turns feedback and reflections into persistent lessons and skills."""

    def __init__(self, memory: MemoryStore, skills: SkillStore):
        self.memory = memory
        self.skills = skills

    def evolve(
        self,
        feedback: str,
        *,
        task: str = "",
        outcome: str = "",
        trigger: str = "manual_feedback",
        category: str | None = None,
        evidence: list[str] | None = None,
        confidence: float | None = None,
        promote: bool | None = None,
    ) -> EvolutionReport:
        feedback = feedback.strip() or "No explicit feedback; summarize recent outcome."
        category = category or self._classify(feedback, task=task, outcome=outcome, trigger=trigger)
        actions = self._actions_for(category)
        confidence = confidence if confidence is not None else self._confidence_for(trigger, category)
        evidence = self._normalize_evidence(evidence or [], task=task, outcome=outcome)
        lesson = self._lesson_from(feedback, task=task, outcome=outcome, category=category, actions=actions, evidence=evidence)
        fingerprint = EvolutionReport.compute_fingerprint(lesson, category)
        promote = self._trusted_promotion_trigger(trigger) if promote is None else bool(promote)
        asset_status = "active" if promote else "candidate"

        duplicate = self.memory.find_similar("lesson", lesson, statuses={"active", "candidate", "verified"})
        if duplicate is not None:
            lesson = duplicate.content
            source_parts = duplicate.source.split(":")
            if source_parts and source_parts[0] == "self_evolution":
                fingerprint = source_parts[-1]
            asset_status = duplicate.status
            promote = duplicate.status == "active"
        memory_written = duplicate is None
        if memory_written:
            source = f"self_evolution:{trigger}:{category}:{fingerprint}"
            self.memory.add(
                "lesson",
                lesson,
                confidence=confidence,
                source=source,
                evidence=evidence,
                status=asset_status,
                verified=promote,
            )

        existing_skill = next(
            (skill for skill in self.skills.list() if str((skill.metadata or {}).get("fingerprint") or "") == fingerprint),
            None,
        )
        if duplicate is not None and existing_skill is not None:
            skill_name = existing_skill.name
            path = existing_skill.path
        else:
            skill_name = self._skill_name(feedback or task or lesson, category=category)
            skill_body = self._skill_body(lesson, category=category, actions=actions, evidence=evidence, fingerprint=fingerprint)
            path = self.skills.upsert(
                skill_name,
                skill_body,
                metadata={
                    "source": "self_evolution",
                    "trigger": trigger,
                    "category": category,
                    "confidence": f"{confidence:.2f}",
                    "fingerprint": fingerprint,
                    "deduped": str(duplicate is not None).lower(),
                    "status": asset_status,
                    "verified": str(promote).lower(),
                    "evidence_sources": sorted(self._evidence_sources(evidence)),
                },
            )
        return EvolutionReport(
            lesson=lesson,
            trigger=trigger,
            category=category,
            actions=actions,
            evidence=evidence,
            confidence=confidence,
            fingerprint=fingerprint,
            skill_name=skill_name,
            skill_path=str(path),
            memory_written=memory_written,
            deduped=duplicate is not None,
            asset_status=asset_status,
            promotion_ready=promote,
        )

    def promote_fingerprint(
        self,
        fingerprint: str,
        *,
        evidence: list[str],
        regression_passed: bool,
        min_independent_sources: int = 2,
    ) -> dict[str, Any]:
        """Promote a staged lesson and skill only after an evidence gate passes."""

        fingerprint = fingerprint.strip()
        sources = self._evidence_sources(evidence)
        result: dict[str, Any] = {
            "fingerprint": fingerprint,
            "promoted": False,
            "regression_passed": bool(regression_passed),
            "evidence_sources": sorted(sources),
            "reason": "",
        }
        if not fingerprint:
            result["reason"] = "fingerprint is required"
            return result
        if not regression_passed:
            result["reason"] = "regression verification did not pass"
            return result
        if len(sources) < max(1, int(min_independent_sources)):
            result["reason"] = f"requires {max(1, int(min_independent_sources))} independent evidence sources"
            return result
        memories = [
            item
            for item in self.memory.all(100000, include_expired=True, namespace=None)
            if item.kind == "lesson" and item.source.endswith(f":{fingerprint}") and item.status in {"candidate", "verified"}
        ]
        active_memories = [
            item
            for item in self.memory.all(100000, include_expired=True, namespace=None)
            if item.kind == "lesson" and item.source.endswith(f":{fingerprint}") and item.status == "active"
        ]
        if active_memories:
            result.update(
                {
                    "promoted": True,
                    "memories": [item.id for item in active_memories],
                    "skills": [
                        skill.name
                        for skill in self.skills.list()
                        if str((skill.metadata or {}).get("fingerprint") or "") == fingerprint
                        and str((skill.metadata or {}).get("status") or "active") == "active"
                    ],
                    "reason": "candidate already promoted",
                }
            )
            return result
        if not memories:
            result["reason"] = "candidate memory not found"
            return result
        if any(item.conflicts_with for item in memories):
            result["reason"] = "candidate conflicts with active memory"
            return result
        verification_note = "regression_passed; sources=" + ",".join(sorted(sources))
        promoted_memories: list[str] = []
        for item in memories:
            self.memory.add_evidence(item.id, evidence)
            self.memory.verify(item.id, evidence=verification_note)
            if self.memory.promote(item.id, evidence="candidate promotion gate passed"):
                promoted_memories.append(item.id)
        promoted_skills: list[str] = []
        for skill in self.skills.list():
            if str((skill.metadata or {}).get("fingerprint") or "") != fingerprint:
                continue
            self.skills.verify(skill.name, evidence=verification_note)
            if self.skills.promote(skill.name, evidence="candidate promotion gate passed"):
                promoted_skills.append(skill.name)
        result.update(
            {
                "promoted": bool(promoted_memories),
                "memories": promoted_memories,
                "skills": promoted_skills,
                "reason": "promotion gate passed" if promoted_memories else "candidate promotion failed",
            }
        )
        return result

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

    def rollback_fingerprint(self, fingerprint: str, *, reason: str) -> dict[str, Any]:
        """Deactivate every durable asset attributed to one evolution run."""

        fingerprint = fingerprint.strip()
        rolled_back_memories: list[str] = []
        deprecated_skills: list[str] = []
        if not fingerprint:
            return {"fingerprint": "", "memories": [], "skills": []}
        for item in self.memory.all(100000, include_expired=True, namespace=None):
            if item.status == "active" and item.source.endswith(f":{fingerprint}"):
                if self.memory.rollback(item.id, reason=reason):
                    rolled_back_memories.append(item.id)
        for skill in self.skills.list():
            metadata = skill.metadata or {}
            if str(metadata.get("fingerprint") or "") != fingerprint:
                continue
            if self.skills.set_status(skill.name, "deprecated", reason=reason):
                deprecated_skills.append(skill.name)
        return {"fingerprint": fingerprint, "memories": rolled_back_memories, "skills": deprecated_skills}

    def status(self, *, recent: int = 5) -> dict[str, Any]:
        lessons = [m for m in self.memory.all(1000) if m.kind == "lesson"]
        lesson_sources: dict[str, int] = {}
        lesson_categories: dict[str, int] = {}
        for lesson in lessons:
            lesson_sources[lesson.source] = lesson_sources.get(lesson.source, 0) + 1
            category = self._category_from_source(lesson.source) or self._classify(lesson.content, task="", outcome="", trigger=lesson.source)
            lesson_categories[category] = lesson_categories.get(category, 0) + 1
        recent_lessons = lessons[-recent:]
        skill_stats = self.skills.stats()
        return {
            "total_lessons": len(lessons),
            "memory_stats": self.memory.stats(),
            "skill_stats": skill_stats,
            "lesson_sources": lesson_sources,
            "lesson_categories": lesson_categories,
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
        if status["lesson_categories"]:
            categories = ", ".join(f"{k}={v}" for k, v in sorted(status["lesson_categories"].items()))
            lines.append(f"- Lesson categories: {categories}")
        if status["recent_lessons"]:
            lines.append("- Recent lessons:")
            lines.extend(f"  - {lesson[:180]}" for lesson in status["recent_lessons"])
        else:
            lines.append("- Recent lessons: none")
        return "\n".join(lines)

    def audit(self, *, trace_analysis: Any | None = None, eval_analysis: Any | None = None) -> dict[str, Any]:
        """Return an inspectable self-evolution health snapshot.

        The audit is intentionally stdlib/data-only so CLI, TUI, tests, and future
        automation can reason about the evolution loop without parsing prose.
        """
        status = self.status(recent=8)
        proposals: list[Any] = []
        if trace_analysis is not None:
            proposals.extend(getattr(trace_analysis, "proposals", []) or [])
        if eval_analysis is not None:
            proposals.extend(getattr(eval_analysis, "proposals", []) or [])
        categories = dict(status["lesson_categories"])
        for proposal in proposals:
            category = getattr(proposal, "category", "general")
            categories.setdefault(category, 0)
        weak_categories = [name for name, count in sorted(categories.items()) if count == 0]
        return {
            "status": status,
            "proposal_count": len(proposals),
            "proposal_categories": sorted({getattr(p, "category", "general") for p in proposals}),
            "weak_categories": weak_categories,
            "recommendations": self._audit_recommendations(status, proposals, weak_categories),
        }

    def render_audit(self, *, trace_analysis: Any | None = None, eval_analysis: Any | None = None) -> str:
        audit = self.audit(trace_analysis=trace_analysis, eval_analysis=eval_analysis)
        status = audit["status"]
        lines = [
            "Evolution audit",
            f"- Lessons: {status['total_lessons']}",
            f"- Evolved skills: {status['skill_stats']['evolved']}/{status['skill_stats']['total']}",
            f"- Pending proposals: {audit['proposal_count']}",
        ]
        if status["lesson_categories"]:
            lines.append("- Categories: " + ", ".join(f"{k}={v}" for k, v in sorted(status["lesson_categories"].items())))
        if audit["proposal_categories"]:
            lines.append("- Proposal categories: " + ", ".join(audit["proposal_categories"]))
        if audit["recommendations"]:
            lines.append("- Recommended next steps:")
            lines.extend(f"  - {item}" for item in audit["recommendations"])
        else:
            lines.append("- Recommended next steps: none")
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

    @staticmethod
    def _trusted_promotion_trigger(trigger: str) -> bool:
        return trigger == "manual_feedback" or trigger.startswith("dream_promote:")

    @staticmethod
    def _evidence_sources(evidence: list[str]) -> set[str]:
        sources: set[str] = set()
        for item in evidence:
            text = str(item).strip()
            if not text:
                continue
            if ":" in text:
                source = text.split(":", 1)[0]
            elif "=" in text:
                source = text.split("=", 1)[0]
            else:
                source = text.split(None, 1)[0]
            sources.add(source.strip().lower() or "unknown")
        return sources

    def _lesson_from(self, feedback: str, *, task: str, outcome: str, category: str, actions: list[str], evidence: list[str]) -> str:
        bits = [f"Category: {category}", f"Feedback: {feedback}"]
        if task:
            bits.append(f"Task context: {task[:300]}")
        if outcome:
            bits.append(f"Outcome context: {outcome[:300]}")
        if evidence:
            bits.append("Evidence: " + "; ".join(evidence[:5]))
        bits.append("Future behavior: " + "; ".join(actions))
        return " | ".join(bits)

    def _skill_body(self, lesson: str, *, category: str, actions: list[str], evidence: list[str], fingerprint: str) -> str:
        checklist = "\n".join(f"{idx}. {action}." for idx, action in enumerate(actions, start=1))
        evidence_lines = "\n".join(f"- {item}" for item in evidence[:8]) or "- Manual feedback or runtime reflection."
        return (
            f"## Fingerprint\n`{fingerprint}`\n\n"
            f"## Lesson\n{lesson}\n\n"
            f"## Evidence\n{evidence_lines}\n\n"
            f"## Applies When\n- A future task matches the `{category}` pattern or similar user feedback appears.\n\n"
            f"## Checklist\n{checklist}\n\n"
            "## Verification\n- Confirm the checklist was applied before the final response.\n"
            "- If the behavior fails again, record a more specific lesson and update this skill.\n"
        )

    def _normalize_evidence(self, evidence: list[str], *, task: str, outcome: str) -> list[str]:
        rows = [str(item).strip().replace("\n", " ")[:220] for item in evidence if str(item).strip()]
        if task:
            rows.append("task=" + task.replace("\n", " ")[:220])
        if outcome:
            rows.append("outcome=" + outcome.replace("\n", " ")[:220])
        deduped: list[str] = []
        seen: set[str] = set()
        for row in rows:
            if row not in seen:
                seen.add(row)
                deduped.append(row)
        return deduped[:10]

    def _category_from_source(self, source: str) -> str | None:
        parts = source.split(":")
        if len(parts) >= 3 and parts[0] == "self_evolution":
            return parts[2]
        return None

    def _audit_recommendations(self, status: dict[str, Any], proposals: list[Any], weak_categories: list[str]) -> list[str]:
        recommendations: list[str] = []
        if proposals:
            recommendations.append("Review and apply high-confidence trace/eval proposals with `evolva evolve trace --apply` or `evolva evolve eval --apply`.")
        if status["total_lessons"] == 0:
            recommendations.append("Seed the loop with direct feedback via `evolva evolve feedback ...`.")
        if status["skill_stats"]["evolved"] < status["total_lessons"]:
            recommendations.append("Materialize important lessons as Markdown skills so future prompts can reuse them.")
        if weak_categories:
            recommendations.append("Add targeted lessons for uncovered categories: " + ", ".join(weak_categories[:5]) + ".")
        if not recommendations:
            recommendations.append("Keep running trace/eval audits after meaningful agent sessions.")
        return recommendations

    def _skill_name(self, text: str, *, category: str = "general") -> str:
        words = re.findall(r"[a-zA-Z0-9\u4e00-\u9fff]+", text.lower())[:5]
        if not words:
            return f"evolved_{category}"
        # Keep ASCII-ish names readable; Chinese words are converted later by SkillStore sanitizer.
        return "evolved_" + category + "_" + "_".join(words)
