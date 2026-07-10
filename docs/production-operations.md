# Production Operations

## Required controls

- Set `EVOLVA_PROFILE=prod`.
- Set `EVOLVA_SANDBOX_BACKEND=docker` and verify with `evolva sandbox smoke`.
- Keep `EVOLVA_SANDBOX_CONTAINER_NETWORK=none` unless a reviewed task requires
  outbound access.
- Set `EVOLVA_RUNTIME_HOME` to a dedicated, access-controlled volume.
- Use `EVOLVA_CREDENTIAL_BACKEND=keyring` or inject short-lived credentials from
  the deployment secret manager.
- Configure every MCP server with `isolation: docker`, an environment allowlist,
  trust level, and tool allowlist. The production policy denies unapproved MCP
  execution by default.
- Export metrics to the platform collector and retain traces according to your
  privacy policy.

## Release gate

Run unit tests, full mypy, package build, all checked-in eval baselines, and at
least one live-provider eval suite with latency and cost budgets. A release fails
when a baseline task regresses, P95 exceeds its budget, or a required live model
is unavailable.

## Rollback

Keep application code and runtime state versioned independently. Roll back code
to the previous release, then restore runtime state only from an encrypted,
schema-compatible snapshot. Workflow compensation handles declared task-side
effects; it is not a replacement for infrastructure backups.
