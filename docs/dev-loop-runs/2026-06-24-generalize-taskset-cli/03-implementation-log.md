# Implementation Log

## Changes
- Renamed the local CSV/attachment workflow modules to generic task-set modules:
  - `evolva/tools/taskset.py`
  - `evolva/eval/taskset.py`
- Replaced the old specialized CLI namespace with `evolva taskset`:
  - `taskset health`
  - `taskset smoke`
  - `taskset run`
  - `taskset inspect-file`
- Renamed built-in tools to generic names:
  - `taskset_context`
  - `taskset_smoke_check`
  - `taskset_tool_health`
- Updated capability mapping, MCP preset descriptions/tags, and tests.
- Removed previous dev-loop artifact directory that contained suite-specific wording.

## Verification
- `.venv/bin/python -m ruff check evolva tests` -> pass
- `.venv/bin/python -m pytest -q tests/test_taskset_tools.py tests/test_agent_cli_workflow_mcp_eval_tui.py` -> 49 passed, 1 skipped
- `.venv/bin/python -m pytest -q` -> 176 passed, 1 skipped
- Dataset-specific and suite-specific wording scan -> no matches in repo excluding `.git`, `.venv`, caches, and pyc files.
