# Contributing to Evolva

## Development setup

```bash
git clone https://github.com/koppx/Evolva.git
cd Evolva
python -m pip install -e ".[dev]"
```

## Change requirements

1. Keep runtime state under `.evolva/`; never add credentials, traces, or local
   workspace artifacts to Git.
2. Add or update tests for behavior changes. Security and workflow changes need
   failure-path and recovery coverage.
3. Run the same gates as CI before opening a pull request:

```bash
python -m ruff check evolva tests
python -m mypy evolva
python -m coverage run -m pytest -q
python -m coverage report
python -m build
```

4. For Agent behavior changes, run the relevant JSONL eval gate in `evals/tasks/`
   against its checked-in baseline.
5. Keep public interfaces backward compatible or document the migration in
   `CHANGELOG.md` and `docs/state-migrations.md`.

Pull requests should explain the user-visible behavior, risk, rollback path, and
verification evidence. Small, independently reviewable changes are preferred.
