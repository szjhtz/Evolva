from __future__ import annotations

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from evolva.agent.llm import OpenAICompatibleLLM, extract_json_object
from evolva.agent.memory import MemoryStore
from evolva.agent.relevance import text_tokens
from evolva.agent.skills import SkillStore
from evolva.agent.todo import TodoStore
from evolva.tools.base import ToolRegistry, ToolResult


class ToolRunner(Protocol):
    def __call__(self, name: str, args: dict[str, object]) -> ToolResult:
        ...


@dataclass(frozen=True)
class AgentRole:
    name: str
    description: str
    system_prompt: str
    tool_names: tuple[str, ...] = ()


@dataclass
class AgentRoleResult:
    role: str
    ok: bool
    output: str
    status: str
    latency_ms: int
    error: str = ""
    fallback: bool = False
    tool_calls: list[dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True)
class TaskRoute:
    label: str
    roles: list[str]
    reason: str
    confidence: float = 0.5

    @property
    def should_collaborate(self) -> bool:
        return bool(self.roles)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class MultiAgentRun:
    run_id: str
    task: str
    roles: list[str]
    status: str
    started_at: float
    ended_at: float
    max_roles: int
    results: list[AgentRoleResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    route: dict[str, object] | None = None
    plan: list[dict[str, object]] = field(default_factory=list)
    synthesis: str = ""
    conflicts: list[dict[str, object]] = field(default_factory=list)
    evidence_graph: dict[str, dict[str, object]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def outputs(self) -> dict[str, str]:
        return {result.role: result.output for result in self.results}

    def render(self) -> str:
        lines = [f"Multi-agent run {self.run_id}: {self.status}", f"- task: {self.task}", f"- roles: {', '.join(self.roles)}"]
        for result in self.results:
            detail = "fallback" if result.fallback else result.status
            lines.append(f"\n## {result.role} ({detail}, {result.latency_ms}ms)\n{result.output}")
        if self.errors:
            lines.append("\nErrors:")
            lines.extend(f"- {item}" for item in self.errors)
        if self.synthesis:
            lines.append(f"\n## Synthesis\n{self.synthesis}")
        if self.conflicts:
            lines.append("\n## Conflicts")
            lines.extend(f"- {item['left_role']} vs {item['right_role']}: {item['topic']}" for item in self.conflicts)
        return "\n".join(lines)


DEFAULT_ROLES: dict[str, AgentRole] = {
    "planner": AgentRole(
        "planner",
        "Breaks work into actionable plans",
        "You are Evolva Planner. Produce concise steps, dependencies, and risks.",
        ("recall", "context_view", "todo_list", "repo_index_status"),
    ),
    "researcher": AgentRole(
        "researcher",
        "Finds and summarizes information",
        "You are Evolva Researcher. Identify facts needed, sources to inspect, and uncertainties.",
        ("recall", "context_view", "repo_index_search", "repo_index_status", "list_files", "read_file", "web_search"),
    ),
    "coder": AgentRole(
        "coder",
        "Implements code changes",
        "You are Evolva Coder. Propose concrete code edits and verification commands.",
        ("recall", "context_view", "repo_index_search", "repo_index_status", "list_files", "read_file", "sandbox_info"),
    ),
    "reviewer": AgentRole(
        "reviewer",
        "Reviews results for bugs and gaps",
        "You are Evolva Reviewer. Find missing requirements, risks, and test gaps.",
        ("recall", "context_view", "repo_index_search", "repo_index_status", "list_files", "read_file", "sandbox_info"),
    ),
}


class TaskRouter:
    """Deterministic first-pass router for deciding when role agents help."""

    COMPLEX_MARKERS = {
        "架构",
        "生产",
        "工业",
        "重构",
        "端到端",
        "实现和测试",
        "测试并行",
        "技术方案",
        "rollout",
        "architecture",
        "production",
        "end-to-end",
        "refactor",
        "migration",
        "design and implement",
    }
    CODING_MARKERS = {
        "实现",
        "修复",
        "改代码",
        "开发",
        "加一个",
        "build",
        "implement",
        "fix",
        "code",
        "add ",
        "create",
    }
    RESEARCH_MARKERS = {"调研", "分析", "对比", "查一下", "研究", "research", "compare", "investigate", "analyze"}
    REVIEW_MARKERS = {"review", "检查", "审查", "评审", "看看", "有没有问题", "risk", "risks"}
    TOOL_MARKERS = {"读取", "列出", "运行", "执行", "搜索", "read", "list", "run", "search", "grep"}

    def route(self, task: str, *, max_roles: int = 4) -> TaskRoute:
        text = " ".join(task.lower().split())
        if not text:
            return TaskRoute("simple", [], "empty task", 0.1)

        has_complex = self._has_any(text, self.COMPLEX_MARKERS)
        has_coding = self._has_any(text, self.CODING_MARKERS)
        has_research = self._has_any(text, self.RESEARCH_MARKERS)
        has_review = self._has_any(text, self.REVIEW_MARKERS)
        has_tool = self._has_any(text, self.TOOL_MARKERS)
        signal_count = sum([has_complex, has_coding, has_research, has_review])
        long_task = len(text) > 120 or text.count("，") + text.count(",") + text.count("\n") >= 3

        if has_complex or signal_count >= 2 or (long_task and (has_coding or has_research or has_review)):
            return self._bounded("complex", ["planner", "researcher", "coder", "reviewer"], "complex task with multiple work surfaces", max_roles, 0.86)
        if has_coding:
            return self._bounded("coding", ["planner", "coder", "reviewer"], "implementation task benefits from plan/code/review roles", max_roles, 0.78)
        if has_research:
            return self._bounded("research", ["researcher", "reviewer"], "research task benefits from evidence and review roles", max_roles, 0.72)
        if has_review:
            return self._bounded("review", ["reviewer"], "review task benefits from a focused reviewer role", max_roles, 0.68)
        if has_tool:
            return TaskRoute("tool_task", [], "single-agent tool task", 0.55)
        return TaskRoute("simple", [], "simple single-agent task", 0.45)

    def _bounded(self, label: str, roles: list[str], reason: str, max_roles: int, confidence: float) -> TaskRoute:
        max_roles = max(1, int(max_roles))
        if len(roles) <= max_roles:
            selected = roles
        elif max_roles == 1:
            selected = ["reviewer"] if label == "review" else ["planner"]
        elif label == "research":
            selected = ["researcher", "reviewer"][:max_roles]
        elif label == "coding":
            selected = ["planner", "reviewer"] if max_roles == 2 else roles[:max_roles]
        else:
            selected = ["planner", "reviewer"] if max_roles == 2 else roles[:max_roles]
        return TaskRoute(label, selected, reason, confidence)

    def _has_any(self, text: str, markers: set[str]) -> bool:
        return any(marker in text for marker in markers)


class MultiAgentCoordinator:
    """Role-based collaboration harness backed by the same LLM and shared state."""

    def __init__(
        self,
        llm: OpenAICompatibleLLM,
        memory: MemoryStore,
        skills: SkillStore,
        todos: TodoStore,
        *,
        max_roles_per_run: int = 4,
        max_tool_steps: int = 2,
    ):
        self.llm = llm
        self.memory = memory
        self.skills = skills
        self.todos = todos
        self.roles = dict(DEFAULT_ROLES)
        self.max_roles_per_run = max(1, int(max_roles_per_run))
        self.max_tool_steps = max(0, int(max_tool_steps))
        self.tool_runner: ToolRunner | None = None
        self.tool_registry: ToolRegistry | None = None
        self.router = TaskRouter()

    def attach_tools(self, runner: ToolRunner, registry: ToolRegistry) -> None:
        """Attach the governed main-agent tool runner for bounded sub-agent use."""
        self.tool_runner = runner
        self.tool_registry = registry

    def list_roles(self) -> str:
        return "\n".join(f"- {r.name}: {r.description}" for r in self.roles.values())

    def delegate(self, role: str, task: str, *, context: str = "") -> str:
        return self.delegate_report(role, task, context=context).output

    def delegate_report(self, role: str, task: str, *, context: str = "") -> AgentRoleResult:
        started = time.monotonic()
        role_obj = self.roles.get(role)
        if role_obj is None:
            raise KeyError(f"unknown agent role: {role}")
        if not task.strip():
            raise ValueError("task is required")
        self.todos.add(f"Sub-agent {role_obj.name}: {task[:120]}", detail=context[:500], owner=role_obj.name)
        if not self.llm.available:
            return AgentRoleResult(role_obj.name, True, self._fallback(role_obj, task, context), "fallback", int((time.monotonic() - started) * 1000), fallback=True)
        if self.tool_runner is not None and self.tool_registry is not None and self.max_tool_steps > 0:
            return self._delegate_with_tools(role_obj, task, context=context, started=started)
        messages = [
            {"role": "system", "content": role_obj.system_prompt},
            {
                "role": "user",
                "content": (
                    f"Task:\n{task}\n\n"
                    f"Shared memory:\n{self.memory.context(task)}\n\n"
                    f"Relevant skills:\n{self.skills.context(task)}\n\n"
                    f"Current todos:\n{self.todos.context()}\n\n"
                    f"Extra context:\n{context or 'None'}\n\n"
                    "Return concise findings, next actions, and risks. Do not claim to run tools."
                ),
            },
        ]
        try:
            output = self.llm.chat(messages, temperature=0.2).content.strip()
            return AgentRoleResult(role_obj.name, True, output, "completed", int((time.monotonic() - started) * 1000))
        except Exception as exc:
            fallback = self._fallback(role_obj, task, context)
            return AgentRoleResult(role_obj.name, False, fallback, "failed_fallback", int((time.monotonic() - started) * 1000), error=str(exc), fallback=True)

    def collaborate(self, task: str, *, roles: list[str] | None = None, context: str = "", parallel: bool = False, synthesize: bool = False) -> str:
        return json.dumps(self.collaborate_report(task, roles=roles, context=context, parallel=parallel, synthesize=synthesize).outputs(), ensure_ascii=False, indent=2)

    def collaborate_report(
        self,
        task: str,
        *,
        roles: list[str] | None = None,
        context: str = "",
        parallel: bool = False,
        synthesize: bool = False,
    ) -> MultiAgentRun:
        task = task.strip()
        if not task:
            raise ValueError("task is required")
        route = None
        if roles is None:
            route_obj = self.route_task(task)
            route = route_obj.to_dict()
            chosen = route_obj.roles or ["planner", "researcher", "coder", "reviewer"]
        else:
            chosen = roles
        chosen = self._normalize_roles(chosen)
        run_id = "multi_" + time.strftime("%Y%m%d_%H%M%S", time.gmtime()) + "_" + uuid.uuid4().hex[:8]
        started_at = time.time()
        plan = self._plan_assignments(task, chosen)
        results = self._execute_assignments(plan, context=context, parallel=parallel)
        errors: list[str] = []
        for result in results:
            if not result.ok:
                errors.append(f"{result.role}: {result.error or result.status}")
        conflicts = self._detect_conflicts(results)
        status = "completed" if not errors else "completed_with_fallbacks"
        if conflicts:
            status = "completed_with_conflicts"
        synthesis = self._synthesize(task, results, conflicts) if synthesize or conflicts else ""
        evidence_graph = {
            result.role: {
                "status": result.status,
                "ok": result.ok,
                "tool_calls": result.tool_calls,
                "output_excerpt": result.output[:1500],
            }
            for result in results
        }
        return MultiAgentRun(
            run_id=run_id,
            task=task,
            roles=chosen,
            status=status,
            started_at=started_at,
            ended_at=time.time(),
            max_roles=self.max_roles_per_run,
            results=results,
            errors=errors,
            route=route,
            plan=plan,
            synthesis=synthesis,
            conflicts=conflicts,
            evidence_graph=evidence_graph,
        )

    def _plan_assignments(self, task: str, roles: list[str]) -> list[dict[str, object]]:
        focus = {
            "planner": "Define executable steps, dependencies, budgets, and acceptance criteria.",
            "researcher": "Gather evidence and identify uncertainty relevant to the task.",
            "coder": "Produce concrete implementation decisions and verification actions.",
            "reviewer": "Challenge correctness, safety, regressions, and missing tests.",
        }
        selected = set(roles)
        plan: list[dict[str, object]] = []
        for role in roles:
            if role in {"planner", "researcher"}:
                dependencies: list[str] = []
            elif role == "coder":
                dependencies = [name for name in ("planner", "researcher") if name in selected]
            elif role == "reviewer":
                dependencies = [name for name in roles if name != "reviewer"]
            else:
                dependencies = []
            plan.append(
                {
                    "id": role,
                    "role": role,
                    "task": f"{task}\n\nRole assignment: {focus.get(role, self.roles[role].description)}",
                    "depends_on": dependencies,
                }
            )
        return plan

    def _execute_assignments(self, plan: list[dict[str, object]], *, context: str, parallel: bool) -> list[AgentRoleResult]:
        completed: dict[str, AgentRoleResult] = {}
        pending = {str(item["role"]): item for item in plan}
        order = [str(item["role"]) for item in plan]
        while pending:
            ready = [
                pending[role]
                for role in order
                if role in pending and all(dep in completed for dep in self._assignment_dependencies(pending[role]))
            ]
            if not ready:
                raise RuntimeError("multi-agent assignment graph contains a cycle or missing dependency")
            batch = ready if parallel else ready[:1]

            def execute(assignment: dict[str, object]) -> AgentRoleResult:
                role = str(assignment["role"])
                dependencies = self._assignment_dependencies(assignment)
                failed_dependencies = [dep for dep in dependencies if not completed[dep].ok]
                dependency_context = self._dependency_context(dependencies, completed)
                if failed_dependencies and role != "reviewer":
                    return AgentRoleResult(
                        role,
                        False,
                        dependency_context,
                        "dependency_failed",
                        0,
                        error="failed dependencies: " + ", ".join(failed_dependencies),
                    )
                extra = context + dependency_context
                return self.delegate_report(role, str(assignment["task"]), context=extra)

            if parallel and len(batch) > 1:
                with ThreadPoolExecutor(max_workers=min(len(batch), self.max_roles_per_run), thread_name_prefix="evolva-role") as pool:
                    futures = {pool.submit(execute, assignment): str(assignment["role"]) for assignment in batch}
                    batch_results: dict[str, AgentRoleResult] = {}
                    for future in as_completed(futures):
                        role = futures[future]
                        try:
                            batch_results[role] = future.result()
                        except Exception as exc:
                            batch_results[role] = AgentRoleResult(role, False, "", "failed", 0, error=str(exc))
                for assignment in batch:
                    role = str(assignment["role"])
                    completed[role] = batch_results[role]
                    pending.pop(role, None)
            else:
                assignment = batch[0]
                role = str(assignment["role"])
                try:
                    completed[role] = execute(assignment)
                except Exception as exc:
                    completed[role] = AgentRoleResult(role, False, "", "failed", 0, error=str(exc))
                pending.pop(role, None)
        return [completed[role] for role in order]

    @staticmethod
    def _dependency_context(dependencies: list[str], completed: dict[str, AgentRoleResult]) -> str:
        if not dependencies:
            return ""
        sections = [f"\n\nDependency evidence [{role}/{completed[role].status}]:\n{completed[role].output[:3000]}" for role in dependencies]
        return "".join(sections)

    @staticmethod
    def _assignment_dependencies(assignment: dict[str, object]) -> list[str]:
        raw = assignment.get("depends_on", [])
        if not isinstance(raw, (list, tuple, set)):
            return []
        return [str(item) for item in raw]

    def _synthesize(self, task: str, results: list[AgentRoleResult], conflicts: list[dict[str, object]]) -> str:
        evidence = "\n\n".join(f"[{result.role}/{result.status}]\n{result.output}" for result in results)
        conflict_text = json.dumps(conflicts, ensure_ascii=False, indent=2) if conflicts else "None detected."
        if not self.llm.available:
            return f"Role evidence:\n{evidence}\n\nConflicts:\n{conflict_text}"
        try:
            return self.llm.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "You are Evolva's lead reviewer. Produce one evidence-backed decision. "
                            "Resolve or explicitly retain every detected conflict, reject unsupported claims, "
                            "and state acceptance criteria, risks, and next actions."
                        ),
                    },
                    {"role": "user", "content": f"Task:\n{task}\n\nRole reports:\n{evidence}\n\nDetected conflicts:\n{conflict_text}"},
                ],
                temperature=0.1,
            ).content.strip()
        except Exception as exc:
            return f"Synthesis unavailable: {exc}\n\n{evidence}"

    def _detect_conflicts(self, results: list[AgentRoleResult]) -> list[dict[str, object]]:
        claims: list[tuple[str, str, set[str], bool]] = []
        stop = {"a", "an", "and", "do", "for", "is", "it", "of", "the", "to", "use", "with", "should", "建议", "使用", "采用"}
        negations = {"not", "never", "no", "without", "avoid", "不要", "不能", "禁止", "避免"}
        for result in results:
            if not result.output:
                continue
            lines = [line.strip(" -*\t") for line in result.output.splitlines() if line.strip()]
            for line in lines[:30]:
                tokens = text_tokens(line)
                polarity = bool(tokens & negations)
                topic = tokens - stop - negations
                if topic:
                    claims.append((result.role, line[:500], topic, polarity))
        conflicts: list[dict[str, object]] = []
        seen: set[tuple[str, str, str]] = set()
        for index, left in enumerate(claims):
            for right in claims[index + 1 :]:
                if left[0] == right[0] or left[3] == right[3]:
                    continue
                overlap = left[2] & right[2]
                if not overlap:
                    continue
                ratio = len(overlap) / max(1, min(len(left[2]), len(right[2])))
                if ratio < 0.5:
                    continue
                topic_text = ", ".join(sorted(overlap)[:8])
                key = (left[0], right[0], topic_text)
                if key in seen:
                    continue
                seen.add(key)
                conflicts.append(
                    {
                        "left_role": left[0],
                        "right_role": right[0],
                        "topic": topic_text,
                        "left_claim": left[1],
                        "right_claim": right[1],
                    }
                )
        return conflicts[:20]

    def route_task(self, task: str, *, max_roles: int | None = None) -> TaskRoute:
        return self.router.route(task, max_roles=max_roles or self.max_roles_per_run)

    def _normalize_roles(self, roles: list[str]) -> list[str]:
        normalized: list[str] = []
        for role in roles:
            role = role.strip()
            if not role:
                continue
            if role not in self.roles:
                raise KeyError(f"unknown agent role: {role}")
            if role not in normalized:
                normalized.append(role)
        if not normalized:
            raise ValueError("at least one role is required")
        if len(normalized) > self.max_roles_per_run:
            raise ValueError(f"too many roles: {len(normalized)} > {self.max_roles_per_run}")
        return normalized

    def _delegate_with_tools(self, role: AgentRole, task: str, *, context: str, started: float) -> AgentRoleResult:
        allowed = tuple(name for name in role.tool_names if self.tool_registry is not None and name in self.tool_registry.names())
        scratch = ""
        tool_calls: list[dict[str, object]] = []
        messages: list[dict[str, object]] = [
            {"role": "system", "content": role.system_prompt + "\n\n" + self._tool_loop_instructions(role, allowed)},
        ]
        for _ in range(self.max_tool_steps + 1):
            messages.append({"role": "user", "content": self._tool_loop_user_prompt(task, context=context, scratch=scratch, allowed=allowed)})
            try:
                data = self._chat_json(messages)
            except Exception as exc:
                fallback = self._fallback(role, task, context)
                return AgentRoleResult(role.name, False, fallback, "failed_fallback", int((time.monotonic() - started) * 1000), error=str(exc), fallback=True, tool_calls=tool_calls)

            final = data.get("final")
            tool = data.get("tool")
            if final and not tool:
                return AgentRoleResult(role.name, True, str(final).strip(), "completed", int((time.monotonic() - started) * 1000), tool_calls=tool_calls)
            if not tool:
                output = str(final or "").strip() or scratch or self._fallback(role, task, context)
                return AgentRoleResult(role.name, True, output, "completed", int((time.monotonic() - started) * 1000), tool_calls=tool_calls)
            if len(tool_calls) >= self.max_tool_steps:
                return AgentRoleResult(
                    role.name,
                    False,
                    scratch or self._fallback(role, task, context),
                    "tool_limit_reached",
                    int((time.monotonic() - started) * 1000),
                    error=f"sub-agent tool step limit reached: {self.max_tool_steps}",
                    tool_calls=tool_calls,
                )
            if not isinstance(tool, dict):
                return AgentRoleResult(role.name, False, scratch, "invalid_tool_request", int((time.monotonic() - started) * 1000), error="tool must be an object", tool_calls=tool_calls)
            name = str(tool.get("name") or "").strip()
            args = tool.get("args") or {}
            if not isinstance(args, dict):
                args = {}
            if name not in allowed:
                call = self._tool_call_summary(name, args, ok=False, status="denied", output=f"Tool `{name}` is not allowed for role `{role.name}`.")
                tool_calls.append(call)
                return AgentRoleResult(role.name, False, str(call["output"]), "tool_denied", int((time.monotonic() - started) * 1000), error=str(call["output"]), tool_calls=tool_calls)
            assert self.tool_runner is not None
            result = self.tool_runner(name, dict(args))
            call = self._tool_call_summary(name, args, ok=result.ok, status="ok" if result.ok else "failed", output=result.output)
            tool_calls.append(call)
            scratch += f"\nTool {name} ({call['status']}):\n{result.output[:1500]}\n"
            if not result.ok:
                return AgentRoleResult(role.name, False, scratch.strip(), "tool_failed", int((time.monotonic() - started) * 1000), error=result.output[:1000], tool_calls=tool_calls)
            messages.append({"role": "assistant", "content": json.dumps(data, ensure_ascii=False)})

        return AgentRoleResult(
            role.name,
            False,
            scratch.strip() or self._fallback(role, task, context),
            "tool_limit_reached",
            int((time.monotonic() - started) * 1000),
            error=f"sub-agent tool step limit reached: {self.max_tool_steps}",
            tool_calls=tool_calls,
        )

    def _tool_loop_instructions(self, role: AgentRole, allowed: tuple[str, ...]) -> str:
        tools = self._describe_allowed_tools(allowed)
        return (
            "You may call only the tools listed below, and at most one tool per step. "
            "Return exactly one JSON object with keys `thought`, `tool`, and `final`. "
            "`tool` is either null or {\"name\": \"tool_name\", \"args\": {...}}. "
            "When you have enough evidence, set tool=null and final to your concise answer. "
            "Do not claim tool results unless they appear in the tool scratchpad.\n\n"
            f"Allowed tools for {role.name}:\n{tools or '- none'}"
        )

    def _tool_loop_user_prompt(self, task: str, *, context: str, scratch: str, allowed: tuple[str, ...]) -> str:
        return (
            f"Task:\n{task}\n\n"
            f"Shared memory:\n{self.memory.context(task)}\n\n"
            f"Relevant skills:\n{self.skills.context(task)}\n\n"
            f"Current todos:\n{self.todos.context()}\n\n"
            f"Extra context:\n{context or 'None'}\n\n"
            f"Allowed tool names: {', '.join(allowed) or 'none'}\n\n"
            f"Tool scratchpad:\n{scratch.strip() or 'No sub-agent tool calls yet.'}"
        )

    def _describe_allowed_tools(self, allowed: tuple[str, ...]) -> str:
        if self.tool_registry is None:
            return ""
        lines: list[str] = []
        for name in allowed:
            try:
                tool = self.tool_registry.get(name)
            except KeyError:
                continue
            lines.append(f"- {tool.name}: {tool.description}; schema={tool.schema}; capabilities={tool.capabilities}")
        return "\n".join(lines)

    def _tool_call_summary(self, name: str, args: dict[str, object], *, ok: bool, status: str, output: str) -> dict[str, object]:
        return {
            "tool": name,
            "ok": ok,
            "status": status,
            "arg_keys": sorted(str(key) for key in args),
            "output": output[:1000],
        }

    def _chat_json(self, messages: list[dict[str, object]]) -> dict[str, object]:
        data: dict[str, Any]
        if hasattr(self.llm, "chat_json"):
            data = dict(self.llm.chat_json(messages, required_keys=["tool", "final"], temperature=0.2))  # type: ignore[attr-defined]
        else:
            response = self.llm.chat(messages, temperature=0.2)  # type: ignore[attr-defined]
            parsed = extract_json_object(response.content)
            if parsed is None:
                raise RuntimeError("LLM response did not contain a JSON object")
            data = parsed
        missing = [key for key in ("tool", "final") if key not in data]
        if missing:
            raise RuntimeError(f"LLM JSON response missing required keys: {', '.join(missing)}")
        return dict(data)

    def _fallback(self, role: AgentRole, task: str, context: str) -> str:
        if role.name == "planner":
            return f"Planner fallback:\n1. Clarify goal: {task}\n2. Create todos.\n3. Use tools to inspect state.\n4. Implement safely.\n5. Verify."
        if role.name == "reviewer":
            return "Reviewer fallback:\n- Check all explicit requirements.\n- Run compile/tests.\n- Search for stale names and missing docs."
        return f"{role.name.title()} fallback: inspect relevant files and report findings. Context: {context[:300] or 'none'}"
