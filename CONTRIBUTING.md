# CONTRIBUTING.md

Thanks for your interest in contributing to ByteBarn.

## Setup

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

## Running tests

All tests run without network or API keys. Offscreen Qt is required:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/pytest
```

Specific test example:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/engine/test_store.py -k cascade
```

pytest uses `asyncio_mode = "auto"` — write async test functions directly; no `@pytest.mark.asyncio` decorator needed.

## Submitting PRs

1. Fork the repo and create a feature branch from `main`.
2. Make focused changes; keep commits small and descriptive.
3. Ensure all tests pass (`pytest` as shown above).
4. Open a pull request against `main` with a clear description of the change and any linked issues.

Thank you!