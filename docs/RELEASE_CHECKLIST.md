# Release Checklist

Pre každý release:

## Pred release
- [ ] Všetky testy prechádzajú (`pytest tests/ -q`)
- [ ] Lint čistý (`ruff check agent/ tests/`)
- [ ] Žiadne TODO items v kóde pre tento release
- [ ] CHANGELOG.md aktualizovaný
- [ ] pyproject.toml version bump
- [ ] docs/DOCS.md verzia aktualizovaná
- [ ] README test counts aktualizované
- [ ] Security invariant testy prechádzajú
- [ ] Codex review na release PR

## Release
- [ ] PR do main
- [ ] CI passed
- [ ] Merge
- [ ] Git tag cez `gh release create`
- [ ] Release notes s detailným popisom

## Po release
- [ ] Vyčistiť staré branche
- [ ] Aktualizovať backlog
- [ ] Skontrolovať že latest release na GitHub je správny
