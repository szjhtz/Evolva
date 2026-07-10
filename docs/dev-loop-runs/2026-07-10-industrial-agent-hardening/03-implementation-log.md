# Implementation Log

Base SHA: `28b2f7e`

## Safety and correctness

- Persisted workflow node outcomes and attempts; resume reuses only successful,
  fingerprint-compatible nodes.
- Centralized redaction across traces, context, sessions, approvals, workflow
  state, memory, skills, and runtime migration.
- Hardened Docker execution and made production local execution fail closed.
- Added MCP environment/tool governance and optional Docker process isolation.

## Runtime and interaction

- Added LLM retry metadata, Retry-After/jitter, bounded reads, structured repair,
  cancellation, usage/cost data, and an SSE streaming interface.
- Added persistent named sessions, fork/retry, turn serialization, cancellation,
  scoped approvals, and a working Textual provider wizard.

## Orchestration and knowledge

- Added workflow retry, timeout, conditions, bounded parallel branches, and
  compensation.
- Added multi-agent assignments, parallel role execution, and lead synthesis.
- Added semantic embedding providers/reference signals to Repo Index.
- Added Memory/Skill namespace, TTL, verification, conflicts, and rollback.
- Added correlated observability, retention, OTLP export, and Prometheus serving.
- Added post-promotion Dream verification and attributed asset rollback.

## Quality and release

- Expanded Eval with live-provider enforcement, P95, token, and cost gates while
  preserving the legacy summary API.
- Expanded CI to full-package mypy and all five checked-in Eval gates.
- Added keyring credentials, runtime state migration, Apache-2.0 licensing,
  security/contribution/changelog/operations docs, and clean package manifests.

No repository test file was changed. New acceptance tests lived only at
`/private/tmp/test_evolva_hardening.py`.
