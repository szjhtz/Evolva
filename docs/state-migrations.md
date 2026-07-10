# Runtime State Migrations

Runtime state is stored outside source code under `.evolva/` by default. Before
upgrading a long-lived workspace, stop active Evolva processes and take a backup
only if your organization has an approved encrypted location for sensitive data.

## Schema 2: persisted secret redaction

Schema 2 redacts known credential patterns from legacy JSON, JSONL, and runtime
Skill Markdown state.
Audit the migration first:

```bash
evolva migrate state
```

The command reports scanned, changed, skipped, and invalid files without writing.
Apply it explicitly after reviewing the counts:

```bash
evolva migrate state --apply
```

Writes are atomic and no plaintext backup is retained. A successful migration
creates `.evolva/state.json` with `schema_version: 2`. Invalid files are listed as
errors and cause a non-zero exit; they are not modified.

The migration excludes provider configuration and MCP server configuration.
Move provider secrets to the operating-system credential store separately by
setting `EVOLVA_CREDENTIAL_BACKEND=keyring`, installing `evolva[credentials]`,
and saving the API key again from `/config wizard`.
