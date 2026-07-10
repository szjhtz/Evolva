# Implementation Plan

## Architecture Summary

Treat policy as the control plane and execution/storage providers as enforceable data-plane boundaries. Persist explicit versioned run/session/node records, centralize redaction, and make every external execution path consume the same policy, budget, observability, and cancellation contracts.

## Task Order

1. Safety and correctness foundations
   - Fix workflow node-state persistence and resume.
   - Redact trace roots, context, audit, approvals, and persisted LLM output.
   - Make production execution fail closed; clearly label/guard unsafe local execution.
   - Allowlist MCP environments and require lifecycle approval.
   - Serialize TUI turns and unify provider setup.
2. Reliable interaction runtime
   - Add LLM response metadata, usage, bounded response reads, Retry-After/jitter, structured validation/repair, streaming and cancellation contracts.
   - Add persisted named sessions, restore/fork/retry surfaces, progress events, richer approval previews and scopes.
3. Orchestration and knowledge
   - Add workflow node retries/timeouts/conditions/parallel-ready scheduling/checkpoints/compensation metadata.
   - Add multi-agent task plans, parallel-safe role execution, synthesis, budgets and persisted reports.
   - Upgrade repo retrieval with pluggable semantic backend/call-graph signals.
   - Add memory namespaces, TTL, contradiction/source governance and skill provenance.
   - Add observability retention, incremental windows, OTLP/Prometheus serving hooks and correlation fields.
4. Quality and release governance
   - Expand model/e2e eval contracts, latency/cost reporting and provider matrices.
   - Bring the core package under mypy in manageable strict groups.
   - Add LICENSE, SECURITY, CONTRIBUTING, CHANGELOG and state migration documentation.

## Verification

- Existing: `python -m pytest -q`, coverage, Ruff, full-package mypy, build, all eval gates.
- Temporary tests: security boundary, redaction persistence, workflow failure resume, MCP environment, TUI turn serialization, transport retry/schema/session/DAG/memory/observability behavior.
- Behavioral probes must fail before each fix where practical and pass afterward.
- Before staging, verify no temporary tests or generated outputs are included.

## Risks

- Safe default changes can surprise users relying on host execution; provide explicit development opt-in and actionable errors.
- Persisted schema changes require tolerant readers and versioned writers.
- Parallel execution must not share TraceRecorder mutable current-run state.

## Requirement Mapping

Tasks 1-4 collectively cover all acceptance criteria in `00-requirements.md`; each implementation log entry records its targeted criteria and evidence.
