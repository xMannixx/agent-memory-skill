# Contributing

Thanks for your interest in improving `agent-memory-skill`. This project is a
small, local-first memory layer for Hermes Agent. Contributions that keep it
dependency-free, deterministic, and well-tested are very welcome.

## Project principles

- **Local-first and dependency-free at runtime.** The runtime uses only the
  Python standard library. Do not add runtime dependencies without discussion.
- **Deterministic behavior.** Retrieval and policy logic should be reproducible.
  No network calls or non-deterministic ranking in the core path.
- **Tested behavior.** Changes to memory behavior, schema, policy, or retrieval
  must come with tests.
- **English everywhere in code.** Comments, docstrings, CLI strings, and
  injected prompt text are English.

## Dev setup

```bash
git clone https://github.com/xMannixx/agent-memory-skill
cd agent-memory-skill

python -m venv .venv
# Windows: .venv\Scripts\activate
# Unix:    source .venv/bin/activate

pip install -r requirements-dev.txt
```

## Running tests

```bash
python -m pytest memory/agent-memory/tests -q
```

The full suite should pass (currently 106 tests). When you touch retrieval,
also confirm the eval harness:

```bash
python -m pytest memory/agent-memory/tests/test_retrieval_eval.py -v
```

## Making changes

1. Start from a GitHub issue. Keep each issue and PR scoped to one behavior or
   design decision.
2. Add or update tests in `memory/agent-memory/tests/`. For time-sensitive
   logic (TTL, decay, rebound, retention) use the frozen-time fixture pattern.
3. For schema changes, keep migrations idempotent and document any impact on
   existing SQLite data.
4. For security-sensitive behavior, add a negative test where possible (for
   example, that `authorization` facts are never injected).
5. Run the full test suite before opening a PR.

## Commit and PR conventions

- Use Conventional Commit prefixes: `feat`, `fix`, `docs`, `refactor`, `perf`,
  `test`, `chore`, optionally scoped, e.g. `feat(core): ...`, `fix(plugin): ...`.
- Reference the issue your change closes with a `Closes #N` trailer.
- Keep PRs small and focused; update `CHANGELOG.md` for user-visible changes.

## Labels, tiers, and workflow

The label scheme (`tier:*`, `area:*`, `type:*`), priority tiers, and the issue
quality checklist live in
[docs/contributing-quickstart.md](docs/contributing-quickstart.md). Release
ordering is tracked in [ROADMAP.md](ROADMAP.md).

## Reporting security issues

Please do not open public issues for vulnerabilities. See
[SECURITY.md](SECURITY.md) for the private reporting process and the memory
threat model.
