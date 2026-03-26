# Contributing

Thanks for your interest in Agent Life Space!

## How to Contribute

### Reporting Bugs

1. Check [existing issues](https://github.com/B2JK-Industry/Agent_Life_Space/issues) first
2. Open a new issue using the **Bug Report** template
3. Include: steps to reproduce, expected vs actual behavior, logs if available

### Suggesting Features

1. Open an issue using the **Feature Request** template
2. Describe the use case and expected behavior
3. Consider security implications (see [Security wiki](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Security))

### Pull Requests

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Write code following existing conventions
4. Add tests (see below)
5. Run all tests: `.venv/bin/python -m pytest tests/ -q`
6. Commit with clear message
7. Open a PR using the template

### Testing Requirements

All PRs must pass the full test suite (696+ tests):

```bash
.venv/bin/python -m pytest tests/ -q
```

Tests are organized in layers:
- **Unit tests** — for individual module changes
- **Integration tests** (`test_integration.py`) — for cross-module changes
- **E2E tests** (`test_e2e_effectiveness.py`) — for flow changes
- **Security audit** (`test_security_audit.py`) — automatically checked

If you add new functionality, add tests at the appropriate layer. See [Testing wiki](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Testing).

## Development Setup

```bash
git clone https://github.com/B2JK-Industry/Agent_Life_Space.git
cd Agent_Life_Space
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pip install sentence-transformers
```

## Code Style

- Python 3.11+
- Ruff for linting (`ruff check .`)
- Line length: 100
- Type hints on all public functions
- Docstrings on modules and classes
- No `eval/exec`, no `shell=True`, no hardcoded secrets

## Security

- All SQL must use parameterized queries (`?` placeholders)
- User input must be sanitized before use
- Secrets must go through the vault, never hardcoded
- New endpoints must have authentication
- See [SECURITY.md](SECURITY.md) for vulnerability reporting

## Architecture

Before making significant changes, read the [Architecture wiki](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Architecture) to understand the module structure and design principles.
