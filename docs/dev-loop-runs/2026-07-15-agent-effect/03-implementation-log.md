# Implementation Log

Implementation started from clean `main` at `796ba16`.

## Batch 1: Relevance, Prompt Budgets, and Tool Routing

- Added bilingual identifier/CJK relevance scoring and bounded prompt sections.
- Memory, Context, Skills, and Tool Router now rank task-relevant material.
- Verified by `tests/test_relevance_tool_router.py` and prompt/tool-route eval cases.

## Batch 2: Native Tools and Coding Operations

- Normalized OpenAI-compatible tool calls while preserving JSON actions.
- Added governed range reads, text search, conflict-aware patching, diff, and test tools.
- Verified by `tests/test_coding_tools.py`, native-call integration tests, and coding eval cases.

## Batch 3: Repository Retrieval

- Added `.gitignore` handling, CJK retrieval, common source languages, and generic symbol chunks.
- Removed broad directory hiding that excluded ordinary source folders.
- Verified by `tests/test_repo_index.py` and Repo Index eval gates.

## Batch 4: Verified and Resumable Main Loop

- Expanded the graph with analyze, verify, and recover nodes.
- Added repeated-action blocking, error taxonomy, post-change evidence gates, code-test requirements, aggregate usage, multi-call tool protocol, and atomic checkpoints.
- Added `/resume [run_id|latest]` and live TUI events.
- Verified by recovery, repetition, interruption/resume, native-call, model-route, and TUI tests.

## Batch 5: Governed Learning and Multi-Agent Work

- Automatic reflections now create inactive candidates; promotion requires regression success and two independent evidence sources.
- Multi-agent runs now use dependency DAGs, dependency evidence, bounded parallel levels, conflict detection, and lead-reviewer synthesis.
- Verified by state-store promotion tests, DAG integration tests, and agent-quality eval cases.

## Batch 6: Model Routing and Quality Gates

- Added fast/coding/reasoning tiers with ordered provider-model fallback.
- Added 43 deterministic agent-quality contracts, including a real multi-turn memory case, and a required CI baseline.
- Final evidence: Ruff passed; Mypy passed on 52 source files; Pytest passed with 201 tests and one skip; all six eval gates passed at score 1.0; sdist and wheel build succeeded.
