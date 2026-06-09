from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from evolva.agent.core import EvolvaAgent
from evolva.config import AgentConfig


@dataclass
class EvalResult:
    id: str
    passed: bool
    score: float
    checks: dict[str, bool]
    answer: str
    tool_logs: list[str] = field(default_factory=list)
    duration_ms: int = 0


@dataclass
class EvalGateResult:
    """Result of comparing an eval run with local quality gates and baselines."""

    ok: bool
    current: dict[str, Any]
    baseline: dict[str, Any] | None = None
    messages: list[str] = field(default_factory=list)
    regressions: list[str] = field(default_factory=list)


class EvalHarness:
    """Small stdlib eval harness for agent regression baselines."""

    def __init__(self, config: AgentConfig | None = None, *, assume_yes: bool = True):
        self.config = config or AgentConfig()
        self.agent = EvolvaAgent(self.config, assume_yes=assume_yes)
        self.results_dir = self.config.eval_results_dir
        self.results_dir.mkdir(parents=True, exist_ok=True)

    def run_file(self, tasks_path: Path) -> list[EvalResult]:
        results: list[EvalResult] = []
        with tasks_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                task = json.loads(line)
                results.append(self.run_task(task))
        self.write_report(results, tasks_path.stem)
        return results

    def run_task(self, task: dict[str, Any]) -> EvalResult:
        started = time.time()
        if "tool" in task:
            tool_name = str(task["tool"])
            tool_result = self.agent._call_tool(tool_name, dict(task.get("args", {})))
            answer = tool_result.output
            tool_logs = [f"TOOL {tool_name} ok={tool_result.ok}\n{tool_result.output}"]
        else:
            result = self.agent.chat(str(task["input"]))
            answer = result.answer
            tool_logs = result.tool_logs
        duration_ms = int((time.time() - started) * 1000)
        checks = self.score(task, answer, tool_logs, duration_ms=duration_ms)
        passed = all(checks.values()) if checks else bool(answer.strip())
        score = sum(1 for ok in checks.values() if ok) / max(1, len(checks))
        return EvalResult(
            id=str(task.get("id", "unnamed")),
            passed=passed,
            score=score,
            checks=checks,
            answer=answer,
            tool_logs=tool_logs,
            duration_ms=duration_ms,
        )

    def score(self, task: dict[str, Any], answer: str, tool_logs: list[str], *, duration_ms: int | None = None) -> dict[str, bool]:
        text = answer + "\n" + "\n".join(tool_logs)
        checks: dict[str, bool] = {}
        for expected in task.get("expected_contains", []):
            checks[f"contains:{expected}"] = str(expected) in text
        for forbidden in task.get("forbidden_contains", []):
            checks[f"not_contains:{forbidden}"] = str(forbidden) not in text
        for pattern in task.get("expected_regex", []):
            checks[f"regex:{pattern}"] = re.search(str(pattern), text, flags=re.MULTILINE) is not None
        for artifact in task.get("expected_artifacts", []):
            path = self._safe_artifact_path(str(artifact))
            checks[f"artifact_inside_root:{artifact}"] = path is not None
            checks[f"artifact_exists:{artifact}"] = bool(path and path.exists())
        for expected in task.get("expected_memory", []):
            query = str(expected)
            checks[f"memory:{query}"] = query in self.agent.memory.context(query)
        for expected in task.get("expected_context", []):
            query = str(expected)
            checks[f"context:{query}"] = query in self.agent.context.render(query=query)
        if "max_duration_ms" in task and duration_ms is not None:
            checks[f"duration<={task['max_duration_ms']}ms"] = duration_ms <= int(task["max_duration_ms"])
        if "no_tool_error" in task.get("scorers", []):
            checks["no_tool_error"] = "ok=False" not in text and "Tool error" not in text
        return checks

    def _safe_artifact_path(self, artifact: str) -> Path | None:
        path = (self.config.root / artifact).resolve()
        try:
            path.relative_to(self.config.root.resolve())
        except ValueError:
            return None
        return path

    def write_report(self, results: list[EvalResult], name: str = "eval") -> Path:
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = self.results_dir / f"{name}_{ts}.json"
        payload = {
            "summary": self.summary(results),
            "results": [asdict(r) for r in results],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def report_payload(self, results: list[EvalResult], *, name: str = "eval") -> dict[str, Any]:
        """Return a stable report payload suitable for CI baselines."""
        return {
            "version": 1,
            "name": name,
            "summary": self.summary(results),
            "tasks": {
                result.id: {
                    "passed": result.passed,
                    "score": result.score,
                    "checks": result.checks,
                    "duration_ms": result.duration_ms,
                }
                for result in results
            },
        }

    def gate(
        self,
        results: list[EvalResult],
        *,
        baseline_path: Path | None = None,
        min_score: float | None = None,
        no_regression: bool = False,
        name: str = "eval",
    ) -> EvalGateResult:
        """Evaluate results against score thresholds and an optional baseline.

        The gate is intentionally deterministic and local-only so it can run in
        CI without an external service. Baseline files are JSON reports produced
        by :meth:`report_payload` or older reports that contain `summary` and
        `results`.
        """
        current = self.report_payload(results, name=name)
        messages: list[str] = []
        regressions: list[str] = []
        baseline = self.load_baseline(baseline_path) if baseline_path else None
        avg_score = float(current["summary"].get("avg_score", 0.0))
        if min_score is not None and avg_score < min_score:
            regressions.append(f"avg_score {avg_score:.3f} is below required {min_score:.3f}")
        failed = int(current["summary"].get("failed", 0))
        if failed:
            regressions.append(f"{failed} eval task(s) failed")
        if baseline and no_regression:
            regressions.extend(self.compare_reports(current, baseline))
        if baseline_path:
            messages.append(f"baseline={baseline_path}")
        if min_score is not None:
            messages.append(f"min_score={min_score:.3f}")
        if no_regression:
            messages.append("no_regression=true")
        return EvalGateResult(ok=not regressions, current=current, baseline=baseline, messages=messages, regressions=regressions)

    def load_baseline(self, path: Path | None) -> dict[str, Any] | None:
        if path is None:
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 1, "missing": str(path), "summary": {}, "tasks": {}}
        except Exception:
            return {"version": 1, "invalid": str(path), "summary": {}, "tasks": {}}
        if "tasks" in payload:
            return payload
        tasks = {}
        for item in payload.get("results", []):
            task_id = str(item.get("id", "unnamed"))
            tasks[task_id] = {
                "passed": bool(item.get("passed", False)),
                "score": float(item.get("score", 0.0)),
                "checks": item.get("checks", {}),
                "duration_ms": int(item.get("duration_ms", 0)),
            }
        return {"version": 1, "summary": payload.get("summary", {}), "tasks": tasks}

    def compare_reports(self, current: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
        """Return human-readable regressions from baseline to current."""
        regressions: list[str] = []
        if baseline.get("missing"):
            regressions.append(f"baseline missing: {baseline['missing']}")
            return regressions
        if baseline.get("invalid"):
            regressions.append(f"baseline invalid: {baseline['invalid']}")
            return regressions
        current_tasks = current.get("tasks", {})
        baseline_tasks = baseline.get("tasks", {})
        for task_id, expected in baseline_tasks.items():
            actual = current_tasks.get(task_id)
            if actual is None:
                regressions.append(f"task {task_id} missing from current run")
                continue
            if bool(expected.get("passed", False)) and not bool(actual.get("passed", False)):
                regressions.append(f"task {task_id} regressed from pass to fail")
            expected_score = float(expected.get("min_score", expected.get("score", 0.0)))
            actual_score = float(actual.get("score", 0.0))
            if actual_score + 1e-9 < expected_score:
                regressions.append(f"task {task_id} score {actual_score:.3f} below baseline {expected_score:.3f}")
        base_avg = baseline.get("summary", {}).get("avg_score")
        cur_avg = current.get("summary", {}).get("avg_score")
        if base_avg is not None and cur_avg is not None and float(cur_avg) + 1e-9 < float(base_avg):
            regressions.append(f"avg_score {float(cur_avg):.3f} below baseline {float(base_avg):.3f}")
        return regressions

    def summary(self, results: list[EvalResult]) -> dict[str, Any]:
        total = len(results)
        passed = sum(1 for r in results if r.passed)
        avg_score = sum(r.score for r in results) / max(1, total)
        return {"total": total, "passed": passed, "failed": total - passed, "avg_score": avg_score}


def render_results(results: list[EvalResult]) -> str:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    lines = [f"Eval results: {passed}/{total} passed"]
    for result in results:
        mark = "PASS" if result.passed else "FAIL"
        lines.append(f"- {mark} {result.id} score={result.score:.2f} duration={result.duration_ms}ms checks={result.checks}")
    return "\n".join(lines)


def render_gate(gate: EvalGateResult) -> str:
    """Render CI gate output in a compact human-readable form."""
    summary = gate.current.get("summary", {})
    lines = [
        "Eval gate: " + ("PASS" if gate.ok else "FAIL"),
        f"- total={summary.get('total', 0)} passed={summary.get('passed', 0)} failed={summary.get('failed', 0)} avg_score={float(summary.get('avg_score', 0.0)):.3f}",
    ]
    for message in gate.messages:
        lines.append(f"- {message}")
    for regression in gate.regressions:
        lines.append(f"- REGRESSION: {regression}")
    return "\n".join(lines)
