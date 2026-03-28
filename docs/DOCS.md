# Agent Life Space — Dokumentácia

> **Hlavná dokumentácia je na [Wiki](https://github.com/B2JK-Industry/Agent_Life_Space/wiki).**

Tento súbor je len rozcestník. Detailná dokumentácia je na wiki stránkach:

| Stránka | Obsah |
|---------|-------|
| [Home](https://github.com/B2JK-Industry/Agent_Life_Space/wiki) | Prehľad, quick links, stats |
| [Architecture](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Architecture) | System design, module map, data flow |
| [Modules](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Modules) | Všetky moduly v detaile |
| [Security](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Security) | Vault, sandbox, auth, prompt injection |
| [API Reference](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/API-Reference) | Agent-to-Agent HTTP API + Telegram commands |
| [Deployment](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Deployment) | Setup, env vars, Cloudflare tunnel |
| [Testing](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Testing) | Test pyramid, coverage, how to run |
| [Skills & Knowledge](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Skills-and-Knowledge) | Čo John vie a pozná |
| [Cron & Maintenance](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Cron-and-Maintenance) | Background jobs, health checks |
| [Finance](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Finance) | Budget, proposals, approval flow |
| [Roadmap](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Roadmap) | Backlog, priorities, known issues |
| [Idea Review and Expansion](./IDEA_REVIEW_AND_EXPANSION_2026_03.md) | Koncept review, rozšírenia, aktuálne nálezy |
| [Security Model](./SECURITY_MODEL.md) | Bezpečnostný model, execution boundaries, tool policy |
| [Learning Model](./LEARNING_MODEL.md) | Definícia learning systému, 4 typy, safety rules |
| [Operator Handbook](./OPERATOR_HANDBOOK.md) | Praktický sprievodca pre vlastníka |
| [Product Identity](./PRODUCT_IDENTITY.md) | Rozhodnutie: personal sovereign operator |
| [Release Checklist](./RELEASE_CHECKLIST.md) | Checklist pre každý release |
| [Strategy Docs](./strategy/README.md) | Source of truth pre dlhodobú produktovú a architektonickú stratégiu |
| [Backlog Progress](./strategy/BACKLOG_PROGRESS.md) | Snapshot progresu proti stratégii a backlogu |
| [Backlog Review Against Masterplan](./strategy/BACKLOG_REVIEW_AGAINST_MASTERPLAN.md) | Gap analysis backlogu proti masterplanu |

## Rýchly štart

```bash
source .venv/bin/activate
python -m agent              # Spusti agenta
python -m agent --status     # Stav
python -m agent --health     # Zdravie
python -m agent --report     # Operator report / inbox
python -m agent --runtime-model   # Explicitný runtime model
python -m agent --gateway-catalog
python -m agent --gateway-catalog --gateway-provider obolos.tech --gateway-capability review_handoff_v1 --gateway-export-mode client_safe
python -m agent --review-quality-eval --review-quality-release-label v1.14.0
python -m agent --export-evidence-job <job_id>
python -m agent --export-evidence-job <job_id> --export-evidence-mode client_safe
python -m agent --list-artifacts  # Shared artifact query surface
python -m agent --list-persisted-jobs
python -m agent --list-retained-artifacts
python -m agent --prune-expired-retained-artifacts
python -m agent --list-cost-ledger
python -m agent --intake-git-url file:///path/to/repo --intake-work-type review --intake-description "Imported review"
python -m agent --build-repo . --build-description "Apply bounded builder plan" --build-plan-file plan.json --build-acceptance-file acceptance.json
python -m agent --intake-repo . --intake-work-type build --intake-description "Plan release slice" --intake-plan-file plan.json --intake-acceptance-file acceptance.json --intake-preview
python -m agent --list-plans
python -m agent --list-deliveries
python -m pytest tests/ -q   # Testy
```

## Verzia

Aktuálna: **v1.14.0** — phase 2 builder engine v2 and provider receipt release.

Nové v `v1.14.0`:
- builder vie bezpečne robiť aj `insert_before_text`, `insert_after_text`,
  `delete_text` a `delete_file`, nie len pôvodné základné mutácie
- builder capability guardrails teraz validujú operation count, typy operácií
  aj scope voči deklarovaným `target_files` ešte pred mutable execution
- gateway pre `obolos.tech` už vracia parsed provider receipt a fallbackuje aj
  pri nekompletnej downstream odpovedi, nie len pri 5xx/unavailable route
- delivery a cost/traces tým pádom nesú aj provider receipt metadata pre audit

Pozri [CHANGELOG.md](../CHANGELOG.md) pre kompletný zoznam zmien.
