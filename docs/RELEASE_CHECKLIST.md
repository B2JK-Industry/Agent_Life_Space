# Release Checklist

Pre každý release:

## Pred release
- [ ] Všetky testy prechádzajú (`pytest tests/ -q`)
- [ ] Lint čistý (`ruff check agent/ tests/`)
- [ ] Type-check čistý (`mypy agent --ignore-missing-imports`)
- [ ] Operator typecheck (`cd operator && npm run typecheck`)
- [ ] Žiadne TODO items v kóde pre tento release
- [ ] `CHANGELOG.md` má entry pre novú verziu (highlights, added, changed, fixed, security,
      deprecations, migration notes, tests, code quality)
- [ ] `pyproject.toml` version bump
- [ ] `agent/__init__.py::__version__` bump (musí byť zhodný s pyproject)
- [ ] `docs/DOCS.md` verzia + "Nové v" sekcia aktualizovaná
- [ ] `README.md` test counts aktualizované (`pytest --collect-only -q | tail -3`)
- [ ] `OPERATOR_HANDBOOK.md` má pre nové env vars / nové guards
- [ ] `SECURITY_MODEL.md` reflektuje nové bezpečnostné posuny
- [ ] Security audit testy prechádzajú (`pytest tests/test_security_audit.py -v`)
- [ ] Architecture invariants testy prechádzajú (`pytest tests/test_architecture_invariants.py -v`)
- [ ] Lokálny release-readiness gate prechádza:
      `AGENT_RELEASE_READINESS_SKIP_LLM_PROBE=1 python -m agent --release-readiness --release-readiness-release-label vX.Y.Z`
- [ ] Codex review na release PR
- [ ] Diff scan na accidental secret leakage
      (`git diff main..HEAD | grep -iE 'sk-ant-|sk-proj-|ghp_|xox[bp]-|password\s*='`)

## Release
- [ ] Feature branch → PR do main
- [ ] CI passed (test job + release readiness gate s `AGENT_RELEASE_READINESS_SKIP_LLM_PROBE=1`)
- [ ] PR merge (squash + delete branch)
- [ ] `git pull origin main` lokálne a `git tag vX.Y.Z` na merge commit
- [ ] `gh release create vX.Y.Z` s notes z CHANGELOG sekcie
- [ ] Verify že tag aj release sú na GitHub UI viditeľné

## Po release
- [ ] Vyčistiť merged branche (`git push origin --delete <branch>`, `git branch -D <branch>`)
- [ ] Aktualizovať `docs/strategy/BACKLOG_PROGRESS.md`
- [ ] Skontrolovať že "Latest" GitHub Release je správny
- [ ] Pull na produkčný server, reštart, smoke test cez Telegram
