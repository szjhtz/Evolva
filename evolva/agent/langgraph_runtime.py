from __future__ import annotations

import hashlib
import inspect
import json
import re
import time
import uuid
from collections.abc import Mapping
from typing import Any, Literal, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from evolva.agent.llm import LLMToolCall, extract_json_object
from evolva.tools.base import ToolResult


CODE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".go",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".swift",
    ".ts",
    ".tsx",
    ".vue",
}
MUTATING_SHELL_RE = re.compile(
    r"(?:^|\s)(?:rm|mv|cp|mkdir|touch|install|git\s+(?:add|commit|mv|rm)|sed\s+-i|npm\s+install|pip\s+install)(?:\s|$)|(?:^|[^<])>{1,2}[^>]"
)
VERIFYING_SHELL_RE = re.compile(r"(?:^|\s)(?:pytest|unittest|test|lint|mypy|ruff|build|check|compile|tsc|cargo\s+test|go\s+test)(?:\s|$)")


class EvolvaGraphState(TypedDict, total=False):
    run_id: str
    user_message: str
    image_sources: list[str]
    scratch: str
    step: int
    final: str
    proposed_final: str
    plan: list[str]
    acceptance_criteria: list[str]
    verification: dict[str, Any]
    recovery_attempts: int
    tool_logs: list[str]
    tool_records: list[dict[str, Any]]
    failed_tools: list[str]
    action_fingerprints: dict[str, int]
    stopped_by_limit: bool
    last_action: dict[str, Any] | None
    last_tool_name: str | None
    last_tool_args: dict[str, Any]
    route: str
    llm_timeout: int
    execution_bounds: Any
    cancellation_token: Any
    cancelled: bool
    turn_messages: list[dict[str, Any]]
    last_tool_call_id: str
    last_native_assistant: dict[str, Any] | None
    pending_native_calls: list[dict[str, Any]]
    llm_usage: dict[str, Any]


class EvolvaLangGraphRuntime:
    """Plan, act, observe, verify, and recover with resumable checkpoints."""

    def __init__(self, agent: Any):
        self.agent = agent
        self.graph = self._build_graph()

    def run(
        self,
        user_message: str,
        image_sources: list[str] | None = None,
        llm_timeout: int | None = None,
        execution_bounds: Any | None = None,
        cancellation_token: Any | None = None,
        resume_run_id: str | None = None,
    ) -> EvolvaGraphState:
        initial: EvolvaGraphState
        if resume_run_id:
            checkpoint = self.agent.checkpoints.load(resume_run_id)
            if checkpoint.get("status") == "completed":
                raise ValueError(f"Agent run `{resume_run_id}` is already completed")
            initial = cast(EvolvaGraphState, dict(checkpoint["state"]))
            initial.update({"run_id": resume_run_id, "final": "", "route": "analyze"})
            self.agent.tracer.event(
                "checkpoint_resumed",
                {"run_id": resume_run_id, "step": initial.get("step", 0), "tool_calls": len(initial.get("tool_records", []))},
            )
        else:
            run_id = self.agent.tracer.current_run_id or f"agent_{uuid.uuid4().hex[:12]}"
            initial = {
                "run_id": run_id,
                "user_message": user_message,
                "image_sources": image_sources or [],
                "scratch": "",
                "step": 0,
                "final": "",
                "proposed_final": "",
                "plan": [],
                "acceptance_criteria": [],
                "verification": {},
                "recovery_attempts": 0,
                "tool_logs": [],
                "tool_records": [],
                "failed_tools": [],
                "action_fingerprints": {},
                "stopped_by_limit": False,
                "last_action": None,
                "last_tool_name": None,
                "last_tool_args": {},
                "route": "analyze",
                "turn_messages": [],
                "last_tool_call_id": "",
                "last_native_assistant": None,
                "pending_native_calls": [],
                "llm_usage": {},
            }
        if image_sources is not None:
            initial["image_sources"] = image_sources
        if llm_timeout is not None:
            initial["llm_timeout"] = int(llm_timeout)
        if execution_bounds is not None:
            initial["execution_bounds"] = execution_bounds
        if cancellation_token is not None:
            initial["cancellation_token"] = cancellation_token
        self._checkpoint(initial)
        try:
            result = self.graph.invoke(initial)
        except Exception:
            checkpoint = self.agent.checkpoints.load(str(initial["run_id"]))
            self.agent.checkpoints.save(str(initial["run_id"]), checkpoint["state"], status="interrupted")
            self.agent.tracer.event("checkpoint_interrupted", {"run_id": initial["run_id"]})
            raise
        self._checkpoint(result, status="completed")
        return result

    def _build_graph(self):
        graph = StateGraph(EvolvaGraphState)
        graph.add_node("prepare", self._prepare)
        graph.add_node("analyze", self._analyze)
        graph.add_node("llm", self._llm)
        graph.add_node("tool", self._tool)
        graph.add_node("observe", self._observe)
        graph.add_node("verify", self._verify)
        graph.add_node("recover", self._recover)
        graph.add_node("persist", self._persist)
        graph.add_node("auto_evolve", self._auto_evolve)

        graph.add_edge(START, "prepare")
        graph.add_conditional_edges("prepare", self._route_after_prepare, {"analyze": "analyze", "persist": "persist"})
        graph.add_edge("analyze", "llm")
        graph.add_conditional_edges("llm", self._route_after_llm, {"tool": "tool", "verify": "verify", "persist": "persist"})
        graph.add_edge("tool", "observe")
        graph.add_conditional_edges("observe", self._route_after_observe, {"tool": "tool", "llm": "llm", "persist": "persist"})
        graph.add_conditional_edges("verify", self._route_after_verify, {"recover": "recover", "persist": "persist"})
        graph.add_conditional_edges("recover", self._route_after_recover, {"llm": "llm", "persist": "persist"})
        graph.add_edge("persist", "auto_evolve")
        graph.add_edge("auto_evolve", END)
        return graph.compile()

    def _prepare(self, state: EvolvaGraphState) -> dict[str, Any]:
        self.agent.tracer.event("langgraph_node", {"node": "prepare", "run_id": state.get("run_id", "")})
        token = state.get("cancellation_token")
        if token is not None and token.cancelled:
            update: dict[str, Any] = {"route": "persist", "final": "Run cancelled.", "cancelled": True}
        else:
            update = {"route": "analyze"}
        self._checkpoint(state, update)
        return update

    def _route_after_prepare(self, state: EvolvaGraphState) -> Literal["analyze", "persist"]:
        return "persist" if state.get("final") else "analyze"

    def _analyze(self, state: EvolvaGraphState) -> dict[str, Any]:
        plan = list(state.get("plan", []))
        acceptance = list(state.get("acceptance_criteria", []))
        if not plan:
            plan, acceptance = self._build_plan(state["user_message"])
        update: dict[str, Any] = {"plan": plan, "acceptance_criteria": acceptance, "route": "llm"}
        self.agent.tracer.event("langgraph_node", {"node": "analyze", "plan": plan, "acceptance_criteria": acceptance})
        self._checkpoint(state, update)
        return update

    def _llm(self, state: EvolvaGraphState) -> dict[str, Any]:
        step = int(state.get("step", 0)) + 1
        scratch = state.get("scratch", "")
        messages = self.agent._messages(
            state["user_message"],
            scratch,
            image_sources=state.get("image_sources") or None,
            turn_messages=state.get("turn_messages", []),
        )
        self.agent.tracer.event(
            "langgraph_node",
            {"node": "llm", "step": step, "scratch_chars": len(scratch), "selected_tools": list(self.agent.last_selected_tools)},
        )
        self.agent.tracer.event(
            "prompt",
            {
                "message_count": len(messages),
                "scratch_chars": len(scratch),
                "system_chars": len(messages[0]["content"]),
                "selected_tools": list(self.agent.last_selected_tools),
                "plan": state.get("plan", []),
            },
        )
        raw_timeout = state.get("llm_timeout")
        timeout = int(raw_timeout) if raw_timeout is not None else int(self.agent.config.request_timeout)
        started = time.time()
        token = state.get("cancellation_token")
        if token is not None and token.cancelled:
            cancel_update: dict[str, Any] = {"step": step, "final": "Run cancelled.", "route": "persist", "cancelled": True}
            self._checkpoint(state, cancel_update)
            return cancel_update
        chat_kwargs: dict[str, Any] = {"timeout": timeout, "cancellation_token": token}
        if self.agent.config.llm_native_tools:
            chat_kwargs.update({"tools": self.agent.tools.openai_tools(self.agent.last_selected_tools), "tool_choice": "auto"})
        model_route = self.agent.model_router.route(
            state["user_message"],
            recovery_attempts=int(state.get("recovery_attempts", 0)),
            selected_tools=list(self.agent.last_selected_tools),
        )
        self.agent.tracer.event("model_route", {**model_route.to_dict(), "step": step})
        response = None
        selected_model = model_route.selected
        last_error: RuntimeError | None = None
        for index, candidate in enumerate(model_route.candidates):
            selected_model = candidate
            try:
                response = self.agent.llm.chat(messages, **self._supported_chat_kwargs({**chat_kwargs, "model": candidate}))
                break
            except RuntimeError as exc:
                if "cancelled" in str(exc).lower():
                    cancel_update = {"step": step, "final": "Run cancelled.", "route": "persist", "cancelled": True}
                    self._checkpoint(state, cancel_update)
                    return cancel_update
                last_error = exc
                if index + 1 < len(model_route.candidates):
                    self.agent.tracer.event(
                        "model_fallback",
                        {"step": step, "failed_model": candidate, "next_model": model_route.candidates[index + 1], "error": str(exc)[:1000]},
                    )
        if response is None:
            assert last_error is not None
            raise last_error
        raw = response.content
        usage = getattr(response, "usage", None)
        aggregate_usage = self._merge_usage(state.get("llm_usage", {}), usage if isinstance(usage, dict) else {})
        self.agent.last_llm_usage = aggregate_usage
        self.agent.tracer.event(
            "llm_response",
            {
                "raw": raw[:4000],
                "latency_ms": int((time.time() - started) * 1000),
                "attempts": getattr(response, "attempts", 1),
                "retries": getattr(response, "retries", 0),
                "model": selected_model,
                "provider_model": getattr(response, "model", ""),
                "request_id": getattr(response, "request_id", ""),
                "finish_reason": getattr(response, "finish_reason", ""),
                "usage": usage,
                "aggregate_usage": aggregate_usage,
                "tool_calls": [{"id": call.id, "name": call.name, "arguments": call.arguments} for call in getattr(response, "tool_calls", [])],
            },
        )
        native_calls: list[LLMToolCall] = list(getattr(response, "tool_calls", []) or [])
        if native_calls:
            assistant_message = {
                "role": "assistant",
                "content": raw or None,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": call.raw_arguments or json.dumps(call.arguments)},
                    }
                    for call in native_calls
                ],
            }
            first, *remaining = native_calls
            native_action: dict[str, Any] = {"tool": {"name": first.name, "args": first.arguments}, "final": None, "protocol": "native"}
            native_update: dict[str, Any] = {
                "step": step,
                "llm_usage": aggregate_usage,
                "last_action": native_action,
                "last_tool_name": first.name,
                "last_tool_args": first.arguments,
                "last_tool_call_id": first.id,
                "last_native_assistant": assistant_message,
                "pending_native_calls": [self._tool_call_dict(call) for call in remaining],
                "route": "tool",
            }
            self._checkpoint(state, native_update)
            return native_update
        parsed_action = extract_json_object(raw)
        if not parsed_action:
            plain_update: dict[str, Any] = {
                "step": step,
                "llm_usage": aggregate_usage,
                "proposed_final": raw.strip(),
                "route": "verify",
                "last_action": None,
            }
            self._checkpoint(state, plain_update)
            return plain_update
        if parsed_action.get("final"):
            final_update: dict[str, Any] = {
                "step": step,
                "llm_usage": aggregate_usage,
                "proposed_final": str(parsed_action["final"]),
                "route": "verify",
                "last_action": parsed_action,
            }
            self._checkpoint(state, final_update)
            return final_update
        raw_tool = parsed_action.get("tool")
        if not isinstance(raw_tool, dict):
            no_tool_update: dict[str, Any] = {
                "step": step,
                "llm_usage": aggregate_usage,
                "proposed_final": str(parsed_action.get("thought", "Done.")),
                "route": "verify",
                "last_action": parsed_action,
            }
            self._checkpoint(state, no_tool_update)
            return no_tool_update
        name = str(raw_tool.get("name") or "")
        args = raw_tool.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        tool_update: dict[str, Any] = {
            "step": step,
            "llm_usage": aggregate_usage,
            "last_action": parsed_action,
            "last_tool_name": name,
            "last_tool_args": args,
            "route": "tool",
        }
        self._checkpoint(state, tool_update)
        return tool_update

    def _route_after_llm(self, state: EvolvaGraphState) -> Literal["tool", "verify", "persist"]:
        if state.get("route") == "tool" and state.get("last_tool_name"):
            return "tool"
        if state.get("route") == "verify":
            return "verify"
        return "persist"

    def _tool(self, state: EvolvaGraphState) -> dict[str, Any]:
        token = state.get("cancellation_token")
        if token is not None and token.cancelled:
            cancel_update: dict[str, Any] = {"final": "Run cancelled.", "route": "persist", "cancelled": True}
            self._checkpoint(state, cancel_update)
            return cancel_update
        name = state.get("last_tool_name") or ""
        args = state.get("last_tool_args") or {}
        fingerprint = self._action_fingerprint(name, args)
        fingerprints = dict(state.get("action_fingerprints", {}))
        previous_count = int(fingerprints.get(fingerprint, 0))
        fingerprints[fingerprint] = previous_count + 1
        self.agent.tracer.event("langgraph_node", {"node": "tool", "tool": name, "fingerprint": fingerprint[:16]})
        max_repeated = max(1, int(self.agent.config.agent_max_repeated_actions))
        if previous_count >= max_repeated:
            result = ToolResult(False, f"Repeated tool action blocked after {max_repeated} execution(s): {name}", {"repeated_action": True})
        else:
            result = self.agent._call_tool(name, args)
        error_type = "" if result.ok else self._classify_error(result.output)
        log = f"TOOL {name}({json.dumps(args, ensure_ascii=False)}) -> ok={result.ok}\n{result.output}"
        tool_logs = [*state.get("tool_logs", []), log]
        failed_tools = list(state.get("failed_tools", []))
        if not result.ok:
            failed_tools.append(name)
        bounds = state.get("execution_bounds")
        if bounds and bounds.max_file_changes is not None and name in {"write_file", "apply_patch", "shell", "python_exec"}:
            baseline = set(getattr(bounds, "baseline_modified_files", frozenset()) or [])
            current = self.agent.modified_file_paths() if hasattr(self.agent, "modified_file_paths") else set()
            changed = len(current - baseline)
            if changed > bounds.max_file_changes:
                failed_tools.append(name)
                over_budget = f"Loop execution budget exceeded: max_file_changes={bounds.max_file_changes} exceeded after `{name}` ({changed} changed files)."
                log += f"\n{over_budget}"
                tool_logs[-1] = log
                result = ToolResult(False, f"{result.output}\n{over_budget}", result.data)
                error_type = "budget"
        record = {
            "index": len(state.get("tool_records", [])),
            "name": name,
            "args": args,
            "ok": result.ok,
            "output": result.output[:4000],
            "error_type": error_type,
            "mutation": self._is_mutation(name, args, result),
            "verification_kind": self._verification_kind(name, args, result),
            "data": result.data if isinstance(result.data, (dict, list, str, int, float, bool, type(None))) else str(result.data),
        }
        tool_records = [*state.get("tool_records", []), record]
        scratch = ((state.get("scratch") or "") + "\n" + log[:4000])[-max(1, int(self.agent.config.prompt_scratch_max_chars)) :]
        if error_type:
            scratch += "\nRECOVERY HINT: " + self._recovery_hint(error_type, name)
            scratch = scratch[-max(1, int(self.agent.config.prompt_scratch_max_chars)) :]
        turn_messages = list(state.get("turn_messages", []))
        call_id = state.get("last_tool_call_id", "")
        native_assistant = state.get("last_native_assistant")
        if call_id:
            if isinstance(native_assistant, dict):
                turn_messages.append(native_assistant)
            turn_messages.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": result.output[:4000]})
        update: dict[str, Any] = {
            "tool_logs": tool_logs,
            "tool_records": tool_records,
            "failed_tools": failed_tools,
            "action_fingerprints": fingerprints,
            "scratch": scratch,
            "turn_messages": turn_messages,
            "last_tool_call_id": "",
            "last_native_assistant": None,
        }
        self._checkpoint(state, update)
        return update

    def _observe(self, state: EvolvaGraphState) -> dict[str, Any]:
        step = int(state.get("step", 0))
        pending = list(state.get("pending_native_calls", []))
        self.agent.tracer.event("langgraph_node", {"node": "observe", "step": step, "pending_native_calls": len(pending)})
        if pending:
            next_call = pending.pop(0)
            update: dict[str, Any] = {
                "last_tool_name": str(next_call.get("name", "")),
                "last_tool_args": dict(next_call.get("arguments", {})),
                "last_tool_call_id": str(next_call.get("id", "")),
                "pending_native_calls": pending,
                "route": "tool",
            }
            self._checkpoint(state, update)
            return update
        if step >= self.agent.config.max_steps:
            tool_logs = state.get("tool_logs", [])
            proposed = state.get("proposed_final", "")
            suffix = "\n".join(tool_logs[-3:])
            final = proposed or "达到最大执行步数，已停止。已完成的工具结果如下：\n" + suffix
            update = {"final": final, "route": "persist", "stopped_by_limit": True}
            self._checkpoint(state, update)
            return update
        update = {"route": "llm"}
        self._checkpoint(state, update)
        return update

    def _route_after_observe(self, state: EvolvaGraphState) -> Literal["tool", "llm", "persist"]:
        if state.get("final"):
            return "persist"
        return "tool" if state.get("route") == "tool" else "llm"

    def _verify(self, state: EvolvaGraphState) -> dict[str, Any]:
        report = self._verification_report(state)
        self.agent.tracer.event("langgraph_node", {"node": "verify", **report})
        self.agent.tracer.event("verification", report)
        if report["passed"]:
            update: dict[str, Any] = {"verification": report, "final": state.get("proposed_final", ""), "route": "persist"}
        else:
            attempts = int(state.get("recovery_attempts", 0))
            can_recover = attempts < max(0, int(self.agent.config.agent_max_recovery_attempts)) and int(state.get("step", 0)) < int(self.agent.config.max_steps)
            if can_recover:
                update = {"verification": report, "route": "recover"}
            else:
                reasons = "; ".join(report["reasons"])
                proposed = state.get("proposed_final", "").strip()
                final = (proposed + "\n\n" if proposed else "") + f"Verification incomplete: {reasons}"
                update = {"verification": report, "final": final, "route": "persist"}
        self._checkpoint(state, update)
        return update

    def _route_after_verify(self, state: EvolvaGraphState) -> Literal["recover", "persist"]:
        return "recover" if state.get("route") == "recover" else "persist"

    def _recover(self, state: EvolvaGraphState) -> dict[str, Any]:
        attempts = int(state.get("recovery_attempts", 0)) + 1
        reasons = "; ".join(state.get("verification", {}).get("reasons", []))
        directive = (
            "\nVERIFIER REQUEST: The proposed final answer cannot be accepted yet. "
            f"Resolve these issues: {reasons}. Use a different action when the previous one failed or repeated, "
            "then gather concrete verification evidence before returning final."
        )
        scratch = ((state.get("scratch") or "") + directive)[-max(1, int(self.agent.config.prompt_scratch_max_chars)) :]
        update = {"recovery_attempts": attempts, "scratch": scratch, "proposed_final": "", "route": "llm"}
        self.agent.tracer.event("langgraph_node", {"node": "recover", "attempt": attempts, "reasons": reasons})
        self.agent.tracer.event("recovery", {"attempt": attempts, "reasons": state.get("verification", {}).get("reasons", [])})
        self._checkpoint(state, update)
        return update

    def _route_after_recover(self, state: EvolvaGraphState) -> Literal["llm", "persist"]:
        return "persist" if state.get("final") else "llm"

    def _persist(self, state: EvolvaGraphState) -> dict[str, Any]:
        self.agent.tracer.event("langgraph_node", {"node": "persist"})
        user_message = state["user_message"]
        image_sources = state.get("image_sources") or []
        final = state.get("final") or state.get("proposed_final") or ""
        history_user = user_message if not image_sources else f"{user_message}\n[Images: {', '.join(image_sources)}]"
        self.agent.history.append({"role": "user", "content": history_user})
        self.agent.history.append({"role": "assistant", "content": final})
        self.agent.sessions.append(self.agent.active_session.id, "user", history_user)
        self.agent.sessions.append(self.agent.active_session.id, "assistant", final)
        refreshed = self.agent.sessions.load(self.agent.active_session.id)
        if refreshed is not None:
            self.agent.active_session = refreshed
        self.agent.context.add("message", history_user, role="user")
        self.agent.context.add("message", final, role="assistant")
        self.agent.tracer.event("context_write", {"items": 2})
        update = {"final": final}
        self._checkpoint(state, update)
        return update

    def _auto_evolve(self, state: EvolvaGraphState) -> dict[str, Any]:
        self.agent.tracer.event("langgraph_node", {"node": "auto_evolve"})
        if self.agent.config.auto_evolve:
            report = self.agent.evolution.reflect_after_turn(state["user_message"], state.get("final", ""), state.get("failed_tools", []))
            payload = {"failed_tools": state.get("failed_tools", []), "report": report.to_dict() if report else None}
            self.agent.tracer.event("auto_evolve", payload)
            if report:
                self.agent.context.add("decision", report.summary(), role="evolution", meta={"evolution": report.to_dict()})
        return {}

    def _build_plan(self, user_message: str) -> tuple[list[str], list[str]]:
        lowered = user_message.lower()
        is_code = any(token in lowered for token in ("code", "bug", "test", "fix", "implement", "代码", "修复", "实现", "测试"))
        plan = ["Identify the requested outcome and constraints", "Gather the smallest set of relevant evidence"]
        if is_code:
            plan.extend(["Apply a narrow change with governed tools", "Inspect the diff and run focused verification"])
        else:
            plan.append("Produce the requested result from evidence")
        plan.append("Report the outcome, evidence, and remaining uncertainty")
        acceptance = ["The response addresses the requested outcome", "Claims are supported by tool evidence or explicit uncertainty"]
        if is_code:
            acceptance.extend(["Changed files are inspected after mutation", "Relevant checks pass or failures are reported clearly"])
        return plan, acceptance

    def _verification_report(self, state: Mapping[str, Any]) -> dict[str, Any]:
        records = list(state.get("tool_records", []))
        reasons: list[str] = []
        evidence: list[str] = []
        unresolved = self._unresolved_failures(records)
        if unresolved:
            reasons.append("unresolved tool failures: " + ", ".join(unresolved))
        mutation_indexes = [index for index, record in enumerate(records) if record.get("ok") and record.get("mutation")]
        if mutation_indexes:
            latest_mutation = max(mutation_indexes)
            after = records[latest_mutation + 1 :]
            verification = [record for record in after if record.get("ok") and record.get("verification_kind")]
            if not verification:
                reasons.append("no post-change readback, diff, or test evidence")
            else:
                evidence.extend(f"{record['name']}:{record['verification_kind']}" for record in verification)
            code_mutations = [record for record in records if record.get("ok") and record.get("mutation") and self._mutates_code(record)]
            if code_mutations:
                strong = [record for record in after if record.get("ok") and record.get("verification_kind") in {"test", "build", "lint", "typecheck"}]
                if not strong:
                    reasons.append("code changed without a passing test, build, lint, or typecheck")
                else:
                    evidence.extend(f"{record['name']}:{record['verification_kind']}" for record in strong)
        proposed = state.get("proposed_final", "").strip()
        if not proposed:
            reasons.append("model did not provide a final answer")
        return {
            "passed": not reasons,
            "reasons": reasons,
            "evidence": list(dict.fromkeys(evidence)),
            "mutation_count": len(mutation_indexes),
            "tool_calls": len(records),
            "recovery_attempts": int(state.get("recovery_attempts", 0)),
        }

    @staticmethod
    def _unresolved_failures(records: list[dict[str, Any]]) -> list[str]:
        unresolved: list[str] = []
        for index, record in enumerate(records):
            if record.get("ok"):
                continue
            name = str(record.get("name", "unknown"))
            if not any(later.get("ok") and later.get("name") == name for later in records[index + 1 :]):
                unresolved.append(f"{name}({record.get('error_type') or 'error'})")
        return list(dict.fromkeys(unresolved))

    @staticmethod
    def _mutates_code(record: dict[str, Any]) -> bool:
        raw_args = record.get("args")
        args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
        path = str(args.get("path", "")).lower()
        return any(path.endswith(suffix) for suffix in CODE_SUFFIXES)

    @staticmethod
    def _is_mutation(name: str, args: dict[str, Any], result: ToolResult) -> bool:
        if not result.ok:
            return False
        if name in {"write_file", "apply_patch"}:
            return True
        if name == "shell":
            return bool(MUTATING_SHELL_RE.search(str(args.get("command", ""))))
        if name == "python_exec":
            code = str(args.get("code", ""))
            return any(token in code for token in ("write_text(", "write_bytes(", ".unlink(", ".mkdir(", "open("))
        return False

    @staticmethod
    def _verification_kind(name: str, args: dict[str, Any], result: ToolResult) -> str:
        if not result.ok:
            return ""
        if name == "run_tests":
            command = str(args.get("command", "")).lower()
            if "mypy" in command or "typecheck" in command or "tsc" in command:
                return "typecheck"
            if "lint" in command or "ruff" in command:
                return "lint"
            if "build" in command or "compile" in command:
                return "build"
            return "test"
        if name == "shell" and VERIFYING_SHELL_RE.search(str(args.get("command", "")).lower()):
            return "test"
        if name == "git_diff":
            return "diff"
        if name in {"read_file", "read_file_range", "search_text"}:
            return "readback"
        return ""

    @staticmethod
    def _classify_error(output: str) -> str:
        lowered = output.lower()
        if "policy denied" in lowered or "not allowed" in lowered or "user denied" in lowered:
            return "policy"
        if "timed out" in lowered or "timeout" in lowered:
            return "timeout"
        if "unknown tool" in lowered or "unexpected keyword" in lowered or "required positional" in lowered:
            return "invalid_arguments"
        if "budget exceeded" in lowered:
            return "budget"
        if "not found" in lowered or "no such file" in lowered:
            return "not_found"
        if "repeated tool action" in lowered:
            return "repeated_action"
        return "execution"

    @staticmethod
    def _recovery_hint(error_type: str, tool_name: str) -> str:
        hints = {
            "policy": "Choose a lower-risk tool or request approval; do not retry the same denied action.",
            "timeout": "Narrow the operation, lower the workload, or use a bounded alternative before retrying.",
            "invalid_arguments": f"Inspect the `{tool_name}` schema and correct the arguments.",
            "not_found": "Search or list paths first, then retry with evidence-backed input.",
            "repeated_action": "Change strategy; the same tool call will remain blocked.",
            "budget": "Stop expanding scope and report the execution-budget boundary.",
            "execution": "Inspect the error output, change the action, and verify the recovery.",
        }
        return hints.get(error_type, hints["execution"])

    @staticmethod
    def _action_fingerprint(name: str, args: dict[str, Any]) -> str:
        payload = json.dumps({"name": name, "args": args}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _tool_call_dict(call: LLMToolCall) -> dict[str, Any]:
        return {"id": call.id, "name": call.name, "arguments": call.arguments}

    @classmethod
    def _merge_usage(cls, current: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
        merged = dict(current)
        for key, value in new.items():
            existing = merged.get(key)
            if isinstance(value, dict):
                merged[key] = cls._merge_usage(existing if isinstance(existing, dict) else {}, value)
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                merged[key] = (existing if isinstance(existing, (int, float)) else 0) + value
            else:
                merged[key] = value
        return merged

    def _checkpoint(self, state: Mapping[str, Any], update: Mapping[str, Any] | None = None, *, status: str = "running") -> None:
        merged = dict(state)
        if update:
            merged.update(update)
        run_id = str(merged.get("run_id", ""))
        if not run_id:
            return
        serializable = {
            key: value
            for key, value in merged.items()
            if key not in {"execution_bounds", "cancellation_token"}
        }
        raw_redacted = self.agent.tracer.redactor.redact_json(serializable)
        redacted: dict[str, Any] = raw_redacted if isinstance(raw_redacted, dict) else {}
        self.agent.checkpoints.save(run_id, redacted, status=status)
        self.agent.tracer.event(
            "checkpoint_saved",
            {"run_id": run_id, "status": status, "step": merged.get("step", 0), "tool_calls": len(merged.get("tool_records", []))},
        )

    def _supported_chat_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Keep lightweight provider/test adapters compatible without hiding internal TypeErrors."""

        try:
            signature = inspect.signature(self.agent.llm.chat)
        except (TypeError, ValueError):
            return kwargs
        if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
            return kwargs
        return {name: value for name, value in kwargs.items() if name in signature.parameters}
