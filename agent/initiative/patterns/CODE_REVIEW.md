# Pattern: CODE_REVIEW

**Kedy použiť:** Daniel (alebo iný agent) zadá "review tento PR / repo / commit". Iniciatíva ide do stavu DELIVER po tom, čo vyprodukuje review report.

## Polyglot

- Python AST + ruff + mypy pre Python repos
- `cargo check` + `clippy` pre Rust
- `tsc` + `eslint` pre TypeScript
- `golangci-lint` pre Go

## Fázy plánu

1. **ANALYZE** — naklonuj repo (alebo PR diff) do workspace, identifikuj jazyk, framework, veľkosť (LOC, súbory). Output: meta JSON.

2. **DESIGN** — vyber review checklist podľa jazyka + projektu (security, perf, ergonomics, tests, docs).

3. **CODE (analyse pass 1)** — statická analýza: ruff/clippy/eslint → JSON report.

4. **CODE (analyse pass 2)** — LLM-driven semantic review: vezmi najdôležitejšie súbory (po počte zmien) + výstup pass 1 → vyrob *human-readable* review s návrhmi.

5. **VERIFY** — sanity check: počet návrhov < 100 (inak agent halucinoval), každý návrh má file:line, žiaden návrh nie je len "consider X" bez akčnej časti.

6. **NOTIFY** — pošli sumár majiteľovi (Telegram), priložená cesta k full reportu (markdown v workspace).

## Acceptance criteria

- [ ] Report má sekcie: Critical / Major / Minor / Style
- [ ] Každý návrh má file:line + odôvodnenie + (ideálne) navrhované riešenie
- [ ] Žiadny návrh nie je generický ("could be improved" — bez konkrétneho ako)
- [ ] Pri PR review zachytené aspoň všetky failing CI checks
- [ ] Žiadne false positives prevyšujú 20 % (verifier sanity)

## Output formát

Markdown report v `agent/initiatives_data/<id>/review.md`:

```markdown
# Review: <repo/PR>

**Verdikt:** approve / request_changes / blocked
**LOC analyzované:** N
**Jazyk:** Python 3.11

## Critical (blocking)
- ...

## Major (recommended)
- ...

## Minor / Style
- ...

## Pozitíva
- ...
```
