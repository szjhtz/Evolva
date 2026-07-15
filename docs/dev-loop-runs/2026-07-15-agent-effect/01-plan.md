# Implementation Plan

## Architecture Summary

Introduce small shared contracts for text relevance, context budgets, tool selection, action parsing, checkpoints, verification evidence, and role assignments. Keep existing stores and policy boundaries, then route all new behavior through them.

## Task Order

1. Add deterministic agent-quality eval cases and aggregate task metrics.
2. Add language-aware text relevance and context budgeting; use them in memory, context, skills, and tool selection.
3. Extend LLM responses with native tool-call data and preserve JSON fallback.
4. Add governed coding tools with conflict-safe patching and bounded output.
5. Make Repo Index git-aware, CJK-capable, and useful across common source languages.
6. Add plan, verifier, recovery, loop detection, and checkpoint state to the graph.
7. Make evolution assets candidates and add evidence-based promotion.
8. Replace role repetition with dependency-aware assignments, evidence, synthesis, and bounded routing.
9. Add role/task model routing and live TUI progress events.
10. Run unit, integration, eval, typing, lint, packaging, and acceptance audits.

## Expected Modules

- `evolva/agent/relevance.py`, `context.py`, `memory.py`, `skills.py`, `tool_router.py`
- `evolva/agent/llm.py`, `core.py`, `langgraph_runtime.py`, `checkpoints.py`
- `evolva/tools/base.py`, `builtin.py`, `capabilities.py`
- `evolva/agent/repo_index.py`, `evolution.py`, `multi_agent.py`
- `evolva/eval/*`, `evals/tasks/*`, `.github/workflows/ci.yml`
- `evolva/tui.py`, `evolva/config.py`, README/config docs

## Test Strategy

- Unit tests for tokenization, ranking, budgets, action parsing, patch conflicts, promotion, routing, and checkpoint serialization.
- Integration tests for prompt construction, native tool calls, coding workflow, recovery, resume, multi-agent synthesis, and event delivery.
- Deterministic eval tasks for end-to-end contracts and task metrics.
- Final commands: Ruff, Mypy, full Pytest/Coverage, five existing eval gates plus the new agent-quality gate, and package build.

## Risks

- Provider variation in tool-call payloads: keep strict normalization and JSON fallback.
- Prompt regressions: measure selected tools, context characters, and total prompt characters in tests/evals.
- False memory promotion: default auto-generated assets to candidate and require explicit evidence.
- Checkpoint replay side effects: checkpoint after each successful tool call and store tool-call fingerprints.

## Acceptance Mapping

Each numbered requirement in `00-requirements.md` maps to the same numbered implementation area above and must have passing automated evidence in `04-acceptance-report.md`.
