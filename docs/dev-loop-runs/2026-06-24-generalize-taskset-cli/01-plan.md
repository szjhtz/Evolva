# Plan

## Goal and Architecture Summary
Rename the local task-set support layer to generic taskset support while preserving behavior.

## Expected Changes
- `evolva/tools/taskset.py`
- `evolva/eval/taskset.py`
- CLI parser/function names: old specialized naming -> `taskset`
- Tool registry names: `taskset_context`, `taskset_smoke_check`, `taskset_tool_health`
- Tests: rename imports, command invocations, and expected strings.
- MCP preset descriptions/tags: keep wording generic.

## Task Order
1. Rename modules/tests and update imports.
2. Rename functions/classes/constants and user-visible strings.
3. Replace CLI namespace with `taskset` while preserving existing `eval <jsonl>` behavior.
4. Update capabilities and registry descriptions.
5. Run lint/tests and scan for unwanted suite-specific wording.

## Verification Commands
- `.venv/bin/python -m ruff check evolva tests`
- `.venv/bin/python -m pytest -q tests/test_taskset_tools.py tests/test_agent_cli_workflow_mcp_eval_tui.py`
- `.venv/bin/python -m pytest -q`
- `rg -n "dataset-specific names" . ...`
- `rg -n "specialized task wording" evolva tests README.md ...`

## Risks
- Existing users of the old command namespace will need to switch to `evolva taskset`.
- String/function rename may miss an import or capability map entry.
