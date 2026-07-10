from __future__ import annotations

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evolva.agent.core import EvolvaAgent
from evolva.storage import atomic_write_json
from evolva.tools.base import ToolResult


@dataclass
class WorkflowResult:
    workflow_id: str
    ok: bool
    outputs: dict[str, Any] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)
    run_id: str = ""
    status: str = ""
    path: str = ""
    node_states: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "ok": self.ok,
            "outputs": self.outputs,
            "logs": self.logs,
            "run_id": self.run_id,
            "status": self.status,
            "path": self.path,
            "node_states": self.node_states,
        }


class WorkflowEngine:
    """A tiny workflow DAG/state-machine runner backed by Evolva tools and role agents."""

    def __init__(self, agent: EvolvaAgent):
        self.agent = agent
        self.runs_dir = self.agent.config.workflows_dir / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def run_file(self, path: Path, *, resume: bool = False) -> WorkflowResult:
        data = json.loads(path.read_text(encoding="utf-8"))
        return self.run(data, resume=resume)

    def run(self, spec: dict[str, Any], *, resume: bool = False) -> WorkflowResult:
        workflow_id = str(spec.get("id") or time.strftime("workflow_%Y%m%d_%H%M%S"))
        run_id = time.strftime("workflow_%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
        fingerprint = self._fingerprint(spec)
        outputs: dict[str, Any] = {}
        logs: list[str] = []
        node_states: dict[str, dict[str, Any]] = {}
        try:
            nodes = self._normalize_nodes(spec.get("nodes", []))
            execution_order = self._topological_order(nodes)
        except ValueError as exc:
            result = WorkflowResult(workflow_id, False, {}, [f"Workflow planning error: {exc}"], run_id=run_id, status="planning_failed")
            return self._persist(result, spec=spec, fingerprint=fingerprint)
        resume_outputs = self._resume_outputs(workflow_id, fingerprint, nodes) if resume else {}
        if resume_outputs:
            outputs.update(resume_outputs)
            node_states.update(
                {
                    node_id: {"status": "completed", "ok": True, "resumed": True, "output": output}
                    for node_id, output in resume_outputs.items()
                }
            )
            logs.append(f"[resume] reused nodes={','.join(sorted(resume_outputs))}")
        for node_id in execution_order:
            node = nodes[node_id]
            kind = node.get("type", "agent")
            deps = node.get("depends_on", [])
            if node_id in resume_outputs:
                dep_text = ",".join(deps) if deps else "none"
                logs.append(f"[{node_id}/{kind}] depends_on={dep_text} ok=True resumed=True\n{outputs[node_id]}")
                self._persist(
                    WorkflowResult(workflow_id, False, outputs, logs, run_id=run_id, status="running", node_states=node_states),
                    spec=spec,
                    fingerprint=fingerprint,
                    nodes=nodes,
                )
                continue
            if not self._condition_allows(node.get("when"), outputs, node_states):
                outputs[node_id] = ""
                node_states[node_id] = {"status": "skipped", "ok": True, "resumed": False, "output": "", "attempts": 0}
                logs.append(f"[{node_id}/{kind}] skipped=True condition={node.get('when')!r}")
                continue
            started_at = time.time()
            retries = max(0, int(node.get("retries", 0)))
            attempts = 0
            node_result = ToolResult(False, "Workflow node did not execute")
            attempt_results: list[dict[str, Any]] = []
            for attempt in range(1, retries + 2):
                attempts = attempt
                node_result = self._execute_node(node, outputs)
                attempt_results.append({"attempt": attempt, "ok": node_result.ok, "output": node_result.output[:1000]})
                if node_result.ok:
                    break
                if attempt <= retries:
                    delay = max(0.0, float(node.get("retry_backoff", 0.25))) * (2 ** (attempt - 1))
                    logs.append(f"[{node_id}/{kind}] retry={attempt}/{retries} delay={delay:.2f}s")
                    if delay:
                        time.sleep(min(delay, 5.0))
            outputs[node_id] = node_result.output
            node_states[node_id] = {
                "status": "completed" if node_result.ok else "failed",
                "ok": node_result.ok,
                "resumed": False,
                "output": node_result.output,
                "started_at": started_at,
                "ended_at": time.time(),
                "attempts": attempts,
                "attempt_results": attempt_results,
                "details": node_result.data if isinstance(node_result.data, dict) else {},
            }
            dep_text = ",".join(deps) if deps else "none"
            logs.append(f"[{node_id}/{kind}] depends_on={dep_text} ok={node_result.ok}\n{node_result.output}")
            self.agent.context.add("artifact", f"Workflow {workflow_id} node {node_id} ok={node_result.ok}\n{node_result.output[:1000]}")
            self._persist(
                WorkflowResult(workflow_id, node_result.ok, outputs, logs, run_id=run_id, status="running" if node_result.ok else "failed", node_states=node_states),
                spec=spec,
                fingerprint=fingerprint,
                nodes=nodes,
            )
            if not node_result.ok and not node.get("continue_on_error", False):
                compensation_ok = self._run_compensations(nodes, node_states, outputs, logs)
                return self._persist(
                    WorkflowResult(
                        workflow_id,
                        False,
                        outputs,
                        logs,
                        run_id=run_id,
                        status="failed" if compensation_ok else "compensation_failed",
                        node_states=node_states,
                    ),
                    spec=spec,
                    fingerprint=fingerprint,
                    nodes=nodes,
                )
        return self._persist(
            WorkflowResult(workflow_id, True, outputs, logs, run_id=run_id, status="completed", node_states=node_states),
            spec=spec,
            fingerprint=fingerprint,
            nodes=nodes,
        )

    def _normalize_nodes(self, raw_nodes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Normalize node IDs and dependencies while preserving sequential specs.

        Existing workflows without `depends_on` keep their original sequential
        semantics. Newer DAG specs can use explicit `depends_on` to run nodes in
        dependency order even when they are declared out of order.
        """
        nodes: dict[str, dict[str, Any]] = {}
        previous_id: str | None = None
        for idx, raw in enumerate(raw_nodes):
            node = dict(raw)
            node_id = str(node.get("id") or f"node_{idx + 1}")
            if node_id in nodes:
                raise ValueError(f"duplicate workflow node id: {node_id}")
            if "depends_on" in node:
                deps_value = node.get("depends_on") or []
                if isinstance(deps_value, str):
                    deps = [deps_value]
                else:
                    deps = [str(item) for item in deps_value]
            else:
                deps = [previous_id] if previous_id else []
            node["id"] = node_id
            node["depends_on"] = deps
            retries = int(node.get("retries", 0))
            if retries < 0:
                raise ValueError(f"node {node_id} retries must be >= 0")
            nodes[node_id] = node
            previous_id = node_id
        for node_id, node in nodes.items():
            for dep in node.get("depends_on", []):
                if dep not in nodes:
                    raise ValueError(f"node {node_id} depends on missing node {dep}")
            when = node.get("when")
            if when is not None and not isinstance(when, (bool, dict)):
                raise ValueError(f"node {node_id} condition must be a boolean or object")
            if isinstance(when, dict) and when.get("node") and str(when["node"]) not in nodes:
                raise ValueError(f"node {node_id} condition references missing node {when['node']}")
        return nodes

    def _topological_order(self, nodes: dict[str, dict[str, Any]]) -> list[str]:
        order: list[str] = []
        state: dict[str, str] = {}

        def visit(node_id: str, stack: list[str]) -> None:
            status = state.get(node_id)
            if status == "done":
                return
            if status == "visiting":
                cycle = " -> ".join(stack + [node_id])
                raise ValueError(f"workflow dependency cycle: {cycle}")
            state[node_id] = "visiting"
            for dep in nodes[node_id].get("depends_on", []):
                visit(dep, stack + [node_id])
            state[node_id] = "done"
            order.append(node_id)

        for node_id in nodes:
            visit(node_id, [])
        return order

    def _run_tool_node(self, node: dict[str, Any], outputs: dict[str, Any]) -> ToolResult:
        name = str(node["tool"])
        args = self._render(node.get("args", {}), outputs)
        if name in {"shell", "python_exec"} and node.get("timeout") is not None and "timeout" not in args:
            args["timeout"] = max(1, int(node["timeout"]))
        return self.agent._call_tool(name, args)

    def _run_role_node(self, node: dict[str, Any], outputs: dict[str, Any]) -> ToolResult:
        role = str(node.get("role", "planner"))
        task = str(self._render(node.get("task", ""), outputs))
        context = json.dumps(outputs, ensure_ascii=False)
        return self.agent._call_tool("delegate_agent", {"role": role, "task": task, "context_text": context})

    def _run_agent_node(self, node: dict[str, Any], outputs: dict[str, Any]) -> ToolResult:
        prompt = str(self._render(node.get("prompt", node.get("task", "")), outputs))
        timeout = int(node["timeout"]) if node.get("timeout") is not None else None
        result = self.agent.chat(prompt, llm_timeout=timeout)
        return ToolResult(not result.failed_tools, result.answer, result)

    def _execute_node(self, node: dict[str, Any], outputs: dict[str, Any]) -> ToolResult:
        kind = str(node.get("type", "agent"))
        try:
            if kind == "tool":
                return self._run_tool_node(node, outputs)
            if kind == "role":
                return self._run_role_node(node, outputs)
            if kind == "agent":
                return self._run_agent_node(node, outputs)
            if kind == "parallel":
                return self._run_parallel_node(node, outputs)
            return ToolResult(False, f"Unknown workflow node type: {kind}")
        except Exception as exc:
            return ToolResult(False, f"Workflow node error: {exc}")

    def _run_parallel_node(self, node: dict[str, Any], outputs: dict[str, Any]) -> ToolResult:
        raw_branches = node.get("branches", [])
        if not isinstance(raw_branches, list) or not raw_branches:
            return ToolResult(False, "Parallel workflow node requires non-empty branches")
        branches: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, raw in enumerate(raw_branches):
            if not isinstance(raw, dict):
                return ToolResult(False, f"Parallel branch {index + 1} must be an object")
            branch = dict(raw)
            branch_id = str(branch.get("id") or f"branch_{index + 1}")
            if branch_id in seen:
                return ToolResult(False, f"Duplicate parallel branch id: {branch_id}")
            if branch.get("type") == "parallel":
                return ToolResult(False, "Nested parallel workflow nodes are not supported")
            seen.add(branch_id)
            branch["id"] = branch_id
            branches.append(branch)
        max_workers = max(1, min(int(node.get("max_parallelism", len(branches))), len(branches), 16))
        results: dict[str, ToolResult] = {}
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="evolva-workflow") as pool:
            futures = {pool.submit(self._execute_node, branch, dict(outputs)): str(branch["id"]) for branch in branches}
            for future in as_completed(futures):
                branch_id = futures[future]
                try:
                    results[branch_id] = future.result()
                except Exception as exc:
                    results[branch_id] = ToolResult(False, f"Parallel branch error: {exc}")
        details = {
            "branches": {
                branch["id"]: {"ok": results[str(branch["id"])].ok, "output": results[str(branch["id"])].output}
                for branch in branches
            }
        }
        ok = all(result.ok for result in results.values())
        return ToolResult(ok, json.dumps(details["branches"], ensure_ascii=False, indent=2), details)

    def _condition_allows(self, when: Any, outputs: dict[str, Any], node_states: dict[str, dict[str, Any]]) -> bool:
        if when is None:
            return True
        if isinstance(when, bool):
            return when
        if not isinstance(when, dict):
            raise ValueError("workflow node condition must be a boolean or object")
        node_id = str(when.get("node") or "")
        if not node_id:
            return bool(when.get("value", False))
        if "ok" in when and bool(node_states.get(node_id, {}).get("ok")) != bool(when["ok"]):
            return False
        output = outputs.get(node_id)
        if "equals" in when and output != when["equals"]:
            return False
        if "contains" in when and str(when["contains"]) not in str(output):
            return False
        return True

    def _run_compensations(
        self,
        nodes: dict[str, dict[str, Any]],
        node_states: dict[str, dict[str, Any]],
        outputs: dict[str, Any],
        logs: list[str],
    ) -> bool:
        ok = True
        completed = [node_id for node_id, state in node_states.items() if state.get("status") == "completed" and state.get("ok") is True]
        for node_id in reversed(completed):
            compensation = nodes.get(node_id, {}).get("compensate")
            if not isinstance(compensation, dict) or not compensation.get("tool"):
                continue
            tool = str(compensation["tool"])
            args = self._render(compensation.get("args", {}), outputs)
            result = self.agent._call_tool(tool, args)
            key = f"compensation:{node_id}"
            node_states[key] = {"status": "completed" if result.ok else "failed", "ok": result.ok, "tool": tool, "output": result.output, "attempts": 1}
            logs.append(f"[{key}/{tool}] ok={result.ok}\n{result.output}")
            ok = ok and result.ok
        return ok

    def _render(self, value: Any, outputs: dict[str, Any]) -> Any:
        if isinstance(value, str):
            rendered = value
            for key, output in outputs.items():
                rendered = rendered.replace("{{" + key + "}}", str(output))
            return rendered
        if isinstance(value, list):
            return [self._render(v, outputs) for v in value]
        if isinstance(value, dict):
            return {k: self._render(v, outputs) for k, v in value.items()}
        return value

    def _persist(
        self,
        result: WorkflowResult,
        *,
        spec: dict[str, Any],
        fingerprint: str,
        nodes: dict[str, dict[str, Any]] | None = None,
    ) -> WorkflowResult:
        path = self.runs_dir / f"{result.run_id}.json"
        result.path = str(path)
        payload = {
            **result.to_dict(),
            "spec_fingerprint": fingerprint,
            "node_fingerprints": {node_id: self._fingerprint(node) for node_id, node in (nodes or {}).items()},
            "spec": spec,
            "updated_at": time.time(),
        }
        redactor = getattr(self.agent.tracer, "redactor", None)
        if redactor is not None:
            payload = redactor.redact_json(payload)
        atomic_write_json(path, payload)
        return result

    def _resume_outputs(self, workflow_id: str, fingerprint: str, nodes: dict[str, dict[str, Any]]) -> dict[str, Any]:
        latest = self._latest_run(workflow_id, fingerprint)
        if not latest:
            return {}
        node_fingerprints = latest.get("node_fingerprints", {})
        outputs = latest.get("outputs", {})
        node_states = latest.get("node_states", {})
        if not isinstance(node_states, dict):
            return {}
        reusable: dict[str, Any] = {}
        for node_id, output in outputs.items():
            node = nodes.get(node_id)
            if not node:
                continue
            if node_fingerprints.get(node_id) != self._fingerprint(node):
                continue
            state = node_states.get(node_id)
            if not isinstance(state, dict) or state.get("status") != "completed" or state.get("ok") is not True:
                continue
            deps = node.get("depends_on", [])
            if all(dep in reusable for dep in deps):
                reusable[node_id] = output
        return reusable

    def _latest_run(self, workflow_id: str, fingerprint: str) -> dict[str, Any] | None:
        latest: dict[str, Any] | None = None
        latest_updated = 0.0
        for path in self.runs_dir.glob("workflow_*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("workflow_id") != workflow_id:
                continue
            if data.get("status") not in {"failed", "running", "completed"}:
                continue
            updated = float(data.get("updated_at", 0.0))
            if updated >= latest_updated:
                latest = {**data, "path": str(path)}
                latest_updated = updated
        return latest

    def _fingerprint(self, value: Any) -> str:
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        import hashlib

        return hashlib.sha256(rendered.encode("utf-8")).hexdigest()
