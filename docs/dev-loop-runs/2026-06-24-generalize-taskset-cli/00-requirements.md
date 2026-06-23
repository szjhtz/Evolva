# Requirements Baseline

## Goal
Refactor Evolva so local task-set preparation is presented as a generic agent capability, not as a suite-specific product surface.

## Non-goals
- Do not remove the underlying local CSV/attachment inspection capabilities.
- Do not add any dataset-specific branding.
- Do not change model behavior unrelated to task-set inspection support.

## User-visible Behavior
- Remove the old specialized task command namespace.
- Replace it with generic task-set naming.
- Built-in tools and user-facing strings should describe reusable task preparation capabilities.

## Acceptance Criteria
- No old specialized parser/help path remains.
- Generic commands cover health, smoke, run, and inspect-file workflows.
- Built-in tool names/descriptions use generic task-set naming.
- Existing functionality remains covered by tests.
- No dataset-specific branding appears.

## Constraints
- Keep Evolva positioned as a universal local-first agent harness.
- Avoid destructive git operations and do not revert unrelated changes.
- Use `.venv/bin/python` for checks.

## Assumptions
- `taskset` is an acceptable generic CLI namespace because it describes a local collection of tasks plus attachments without implying suite specialization.
- Existing `evolva eval <jsonl>` behavior remains intact as a separate generic developer workflow.

## Open Questions
None blocking.

## Source Request
User asked to make Evolva a generic agent, not a narrowly targeted runner.

## Repo Context
- Branch: main
- Base SHA: 1272b0c1a8e87819d8899aba66e86c884794e350
- Initial status: clean relative to origin/main
