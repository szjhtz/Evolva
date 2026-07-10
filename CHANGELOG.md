# Changelog

This project follows [Semantic Versioning](https://semver.org/). Until 1.0,
minor releases may include documented API changes.

## Unreleased

### Added

- Versioned sessions with restore, rename, fork, retry, and TUI cancellation.
- Workflow node state, safe resume, retries, conditions, bounded parallel
  branches, and compensation actions.
- Structured LLM response repair, cancellation, SSE streaming API, provider
  request metadata, usage, cost, and retry controls.
- MCP environment isolation, server trust levels, and per-tool allow/deny rules.
- Namespaced and expiring memory/skills with verification and conflict handling.
- Correlated observability, retention, OTLP-shaped export, and Prometheus serving.
- Pluggable semantic repository embeddings and multi-agent parallel synthesis.
- Optional system keyring credential storage and dry-run-first state migration.

### Changed

- Production policy fails closed when command execution is not isolated.
- Docker execution drops capabilities, prevents privilege escalation, and mounts
  the project read-only outside explicit writable roots.
- Full-package mypy is now enforced in CI.

### Security

- Trace, context, sessions, memory, skills, workflow state, and approval payloads
  are redacted before persistence.
