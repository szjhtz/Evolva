# Requirements Baseline

## Goal

Improve Evolva's measurable task success as a general local agent by making tool selection, context retrieval, repository editing, verification, recovery, memory promotion, multi-agent work, and runtime feedback evidence-driven.

## Non-goals

- Replace Evolva's local-first and OpenAI-compatible provider model.
- Remove the existing policy, sandbox, trace, workflow, or eval contracts.
- Require a hosted vector database or a specific commercial model.

## User-visible Behavior

- Relevant tools and context are selected per task instead of sending every tool and store on every model step.
- Chinese and English requests can retrieve related memory, context, skills, and repository code.
- Coding tasks can search, read ranges, patch with conflict detection, inspect diffs, and run tests.
- Agent runs expose explicit plan, execution, verification, recovery, checkpoint, and progress state.
- Runtime reflections remain candidates until evidence promotes them.
- Multi-agent runs use bounded dependent assignments and produce one synthesis used by the parent agent.
- TUI users receive live progress events while work is running.

## Acceptance Criteria

1. A checked-in quality benchmark covers repository edits, tool routing, Chinese retrieval, multi-turn context, verification, recovery, memory promotion, and multi-agent routing.
2. The default prompt exposes a bounded tool shortlist and enforces a configurable context budget.
3. The LLM client supports native function/tool calls with a compatible JSON fallback.
4. `search_text`, `read_file_range`, `apply_patch`, `git_diff`, and `run_tests` are governed tools with unit and integration tests.
5. Repo Index supports CJK queries, common source extensions, `.gitignore`, and does not hide ordinary `workflows`, `context`, or `skills` source directories.
6. The graph records plan, verification, recovery, repeated-call protection, and resumable checkpoints.
7. Automatically generated memories and skills are candidates until verified or promoted by evidence.
8. Multi-agent routing produces a dependency-aware plan, bounded work, shared evidence, and a synthesis consumed by the main run.
9. Model selection can vary by role/task and TUI progress is emitted before final completion.
10. Existing tests and eval gates remain green; new behavior has corresponding tests.

## Constraints

- Python 3.10 compatibility.
- Local-first operation with no mandatory network dependency.
- Existing public CLI/TUI commands remain compatible.
- Policy and sandbox checks remain the single path for all tool execution.

## Assumptions

- Native tool calls are optional because OpenAI-compatible providers differ; JSON action fallback remains supported.
- Deterministic local evals run in CI, while provider-backed quality runs are opt-in or scheduled.
- The default main-agent checkpoint store is project-local under `.evolva`.

## Open Questions

None blocking. Provider-specific model names and optional embedding providers remain runtime configuration.

## Source Request

Implement the eight prioritized agent-effect improvements from the 2026-07-15 review.

## Repo Context

- Base branch: `main`
- Base SHA: `796ba16`
- Initial worktree: clean
- Baseline: 178 passed, 1 skipped; Ruff and Mypy clean; five eval gates pass.
