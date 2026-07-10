# Requirements Baseline

## Goal

Move Evolva from a capable local harness toward a production-grade general agent by closing the verified safety, recovery, privacy, concurrency, reliability, evaluation, and governance gaps.

## Non-goals

- No hosted control plane or mandatory cloud dependency.
- No compatibility break for existing local `.evolva` state without migration/fallback handling.
- No committed test-only code, fixtures, or generated test output.

## User-visible Behavior

- Safe defaults do not silently grant host-level execution.
- Workflow resume only reuses successful, compatible nodes.
- Persisted prompts, context, traces, approvals, and logs redact secrets.
- MCP servers receive an explicit environment and execution boundary.
- The TUI serializes turns, supports consistent provider setup, progress, cancellation, sessions, and useful approvals.
- LLM calls expose reliable retry, structured-output, usage, and provider metadata.
- Workflow, multi-agent, retrieval, memory, observability, and eval behavior are production-oriented and auditable.

## Acceptance Criteria

1. Default execution cannot access the host through shell/Python without an explicit unsafe-development opt-in or an available isolated backend.
2. Failed workflow nodes are never reused by resume; persisted node state includes success/failure and attempts.
3. All persisted user/model/tool content passes through centralized redaction.
4. MCP child environments are allowlisted and high-risk server lifecycle operations are approved and audited.
5. Concurrent TUI turns cannot share mutable run state; provider setup behaves consistently.
6. LLM transport supports bounded retries with jitter/Retry-After, schema validation/repair, usage and request metadata, streaming/cancellation where supported.
7. Sessions, approvals, workflow DAG execution, multi-agent synthesis, retrieval, memory governance, observability retention/export, and eval coverage have explicit production contracts.
8. CI runs the meaningful type-check surface and release governance files exist.
9. Existing tests and eval gates pass; temporary new tests prove each behavior but are not committed.

## Constraints

- Preserve Python 3.10+ support and local-first operation.
- Prefer stdlib and existing dependencies unless a dependency materially improves a core engine.
- Production code, required configuration, migrations, and user documentation may be committed; temporary tests may not.

## Assumptions

- `main` is the requested release branch and direct push is authorized.
- Docker is an optional strong-isolation backend; systems without it must fail closed for production execution profiles.
- Existing `dev` users may explicitly opt into unsafe local execution for compatibility.

## Open Questions

None blocking. Conservative defaults and backward-compatible migration behavior will be used.

## Source Request

Implement every issue from the 2026-07-10 production-readiness review, test each change, do not commit test code, and push the resulting production changes.

## Repo Context

- Base SHA: `28b2f7e`
- Branch: `main`
- Initial worktree: clean
- Baseline: 178 passed, 1 skipped; coverage 78%; Ruff passed; full-package mypy reported 52 errors.
