## What changed

<!-- One or two sentences. What is different for a user / caller / maintainer? -->

Closes #

## How it was verified

<!-- Commands run, plus anything a reviewer cannot reproduce from a clean checkout. -->

```bash
ruff check .
ruff format --check .
mypy
pytest -m "not slow"
```

## Checklist

- [ ] `ruff check .` passes
- [ ] `ruff format --check .` passes
- [ ] `mypy` passes
- [ ] `pytest -m "not slow"` passes
- [ ] Tests added (unit or integration + why that level)
- [ ] `CHANGELOG.md` entry added under `## [Unreleased]` for user-facing changes
- [ ] No unrelated refactors in this PR

## Notes for reviewers

<!-- Design decisions, tradeoffs, follow-ups, schema/behaviour changes, screenshots or reel output. -->
