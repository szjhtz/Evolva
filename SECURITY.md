# Security Policy

## Supported versions

Security fixes are applied to the latest release on `main`. Older snapshots are
not maintained unless a release note says otherwise.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use GitHub's private
security advisory flow for `koppx/Evolva` and include:

- affected version or commit;
- reproduction steps and expected impact;
- whether credentials or host execution are involved;
- a suggested mitigation, if known.

Please allow 7 days for an initial response. Confirmed issues involving command
execution, credential exposure, sandbox escape, or policy bypass are treated as
release blockers.

## Deployment boundary

The default `local` sandbox backend is for development and does not isolate host
reads or processes. Production deployments must use an isolated backend, the
`prod` policy profile, explicit MCP trust/tool policies, and a dedicated runtime
directory. See [Production Operations](docs/production-operations.md).
