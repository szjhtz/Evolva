from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from evolva.agent.evolution import EvolutionReport, SelfEvolutionEngine
from evolva.agent.tracing import TraceRecorder


@dataclass
class EvolutionProposal:
    id: str
    source: str
    trigger: str
    category: str
    feedback: str
    task: str = ""
    outcome: str = ""
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.75

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvolutionAnalysis:
    source: str
    inspected: int
    proposals: list[EvolutionProposal] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "inspected": self.inspected, "proposals": [p.to_dict() for p in self.proposals]}


class TraceEvolutionAnalyzer:
    """Find recurring improvement opportunities from execution traces."""

    def __init__(self, tracer: TraceRecorder):
        self.tracer = tracer

    def analyze(self, *, limit: int = 20) -> EvolutionAnalysis:
        rows = self.tracer.list_runs(limit=limit)
        proposals: list[EvolutionProposal] = []
        failed_tools: dict[str, list[str]] = {}
        denied_tools: dict[str, list[str]] = {}
        long_answers: list[str] = []
        statuses: dict[str, int] = {}

        for row in rows:
            run_id = str(row.get("run_id", ""))
            if not run_id:
                continue
            try:
                data = self.tracer.load(run_id)
            except Exception:
                continue
            status = str(data.get("status", "unknown"))
            statuses[status] = statuses.get(status, 0) + 1
            if "failure" in status or "error" in status:
                failed_tools.setdefault("run_status", []).append(run_id)
            final = str(data.get("final_answer", ""))
            if len(final) >= 4000:
                long_answers.append(run_id)
            for event in data.get("events", []):
                kind = event.get("kind")
                payload = event.get("data", {}) or {}
                if kind == "tool_call" and payload.get("ok") is False:
                    tool = str(payload.get("tool", "unknown"))
                    failed_tools.setdefault(tool, []).append(run_id)
                elif kind == "tool_error":
                    tool = str(payload.get("tool", "unknown"))
                    failed_tools.setdefault(tool, []).append(run_id)
                elif kind == "policy_decision" and payload.get("allowed") is False:
                    tool = str(payload.get("tool", "unknown"))
                    denied_tools.setdefault(tool, []).append(run_id)

        for tool, run_ids in sorted(failed_tools.items(), key=lambda item: (-len(item[1]), item[0])):
            proposals.append(
                EvolutionProposal(
                    id=f"trace_tool_failure_{tool}",
                    source="trace",
                    trigger="trace_analysis",
                    category="tool_failure",
                    feedback=f"Trace analysis found {len(run_ids)} failed `{tool}` tool/run pattern(s).",
                    task="Analyze recent traces for recurring tool failures.",
                    outcome=f"Affected traces: {', '.join(run_ids[:8])}",
                    evidence=run_ids[:10],
                    confidence=min(0.95, 0.72 + 0.04 * len(run_ids)),
                )
            )

        for tool, run_ids in sorted(denied_tools.items(), key=lambda item: (-len(item[1]), item[0])):
            proposals.append(
                EvolutionProposal(
                    id=f"trace_policy_denied_{tool}",
                    source="trace",
                    trigger="trace_analysis",
                    category="safety",
                    feedback=f"Trace analysis found {len(run_ids)} policy denial(s) for `{tool}`.",
                    task="Analyze recent traces for guardrail friction.",
                    outcome=f"Affected traces: {', '.join(run_ids[:8])}",
                    evidence=run_ids[:10],
                    confidence=min(0.9, 0.70 + 0.04 * len(run_ids)),
                )
            )

        if long_answers:
            proposals.append(
                EvolutionProposal(
                    id="trace_quality_long_answers",
                    source="trace",
                    trigger="trace_analysis",
                    category="quality",
                    feedback=f"Trace analysis found {len(long_answers)} overly long final answer(s).",
                    task="Analyze recent traces for response quality issues.",
                    outcome=f"Affected traces: {', '.join(long_answers[:8])}",
                    evidence=long_answers[:10],
                    confidence=min(0.86, 0.68 + 0.03 * len(long_answers)),
                )
            )

        if statuses and statuses.get("completed_with_tool_failures", 0) >= 2:
            count = statuses["completed_with_tool_failures"]
            proposals.append(
                EvolutionProposal(
                    id="trace_workflow_completed_with_failures",
                    source="trace",
                    trigger="trace_analysis",
                    category="workflow",
                    feedback=f"Trace analysis found {count} run(s) completed despite tool failures; improve recovery workflow.",
                    task="Analyze recent traces for incomplete recovery loops.",
                    outcome=json.dumps(statuses, ensure_ascii=False, sort_keys=True),
                    evidence=[f"completed_with_tool_failures={count}"],
                    confidence=min(0.88, 0.70 + 0.03 * count),
                )
            )

        return EvolutionAnalysis(source="trace", inspected=len(rows), proposals=proposals)


class EvalEvolutionAnalyzer:
    """Convert eval failures into concrete self-evolution proposals."""

    def __init__(self, results_dir: Path):
        self.results_dir = results_dir

    def latest_report(self) -> Path | None:
        reports = sorted(self.results_dir.glob("*.json"), reverse=True)
        return reports[0] if reports else None

    def analyze_file(self, path: Path | None = None) -> EvolutionAnalysis:
        report_path = path or self.latest_report()
        if report_path is None:
            return EvolutionAnalysis(source="eval", inspected=0, proposals=[])
        data = json.loads(report_path.read_text(encoding="utf-8"))
        results = data.get("results", [])
        proposals: list[EvolutionProposal] = []
        for result in results:
            if result.get("passed"):
                continue
            checks = result.get("checks", {}) or {}
            failed_checks = [name for name, ok in checks.items() if not ok]
            category = self._category_for_failed_checks(failed_checks, result)
            task_id = str(result.get("id", "unnamed"))
            answer = str(result.get("answer", ""))[:800]
            proposals.append(
                EvolutionProposal(
                    id=f"eval_failure_{task_id}",
                    source="eval",
                    trigger="eval_failure",
                    category=category,
                    feedback=f"Eval `{task_id}` failed checks: {', '.join(failed_checks) or 'no explicit checks passed' }.",
                    task=f"Improve behavior for eval task `{task_id}` from {report_path.name}.",
                    outcome=f"Answer excerpt: {answer}",
                    evidence=failed_checks or [task_id],
                    confidence=0.86,
                )
            )
        return EvolutionAnalysis(source="eval", inspected=len(results), proposals=proposals)

    def _category_for_failed_checks(self, failed_checks: list[str], result: dict[str, Any]) -> str:
        text = " ".join(failed_checks + result.get("tool_logs", [])).lower()
        if "tool_error" in text or "ok=false" in text:
            return "tool_failure"
        if "artifact_exists" in text:
            return "workflow"
        if "contains" in text:
            return "quality"
        return "verification"


def apply_proposals(engine: SelfEvolutionEngine, proposals: list[EvolutionProposal]) -> list[EvolutionReport]:
    reports: list[EvolutionReport] = []
    for proposal in proposals:
        reports.append(
            engine.evolve(
                proposal.feedback,
                task=proposal.task,
                outcome=proposal.outcome,
                trigger=proposal.trigger,
                category=proposal.category,
                evidence=proposal.evidence,
                confidence=proposal.confidence,
            )
        )
    return reports


def render_analysis(analysis: EvolutionAnalysis) -> str:
    lines = [f"Evolution analysis: {analysis.source}", f"- Inspected: {analysis.inspected}", f"- Proposals: {len(analysis.proposals)}"]
    for proposal in analysis.proposals:
        evidence = ", ".join(proposal.evidence[:5]) if proposal.evidence else "none"
        lines.append(f"- {proposal.id} [{proposal.category}/{proposal.confidence:.2f}] {proposal.feedback} evidence={evidence}")
    return "\n".join(lines)


def render_reports(reports: list[EvolutionReport]) -> str:
    if not reports:
        return "No evolution reports."
    lines = [f"Applied evolution reports: {len(reports)}"]
    for report in reports:
        lines.append(f"- {report.summary()} skill={report.skill_name} memory_written={report.memory_written}")
    return "\n".join(lines)
