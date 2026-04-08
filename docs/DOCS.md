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
| [Skills & Knowledge](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Skills-and-Knowledge) | Čo agent vie a pozná |
| [Cron & Maintenance](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Cron-and-Maintenance) | Background jobs, health checks |
| [Finance](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Finance) | Budget, proposals, approval flow |
| [Roadmap](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Roadmap) | Backlog, priorities, known issues |
| [Idea Review and Expansion](./IDEA_REVIEW_AND_EXPANSION_2026_03.md) | Koncept review, rozšírenia, aktuálne nálezy |
| [Security Model](./SECURITY_MODEL.md) | Bezpečnostný model, execution boundaries, tool policy |
| [Learning Model](./LEARNING_MODEL.md) | Definícia learning systému, 4 typy, safety rules |
| [Operator Handbook](./OPERATOR_HANDBOOK.md) | Praktický sprievodca pre vlastníka |
| [Product Identity](./PRODUCT_IDENTITY.md) | Rozhodnutie: personal sovereign operator |
| [Controlled Environments](./CONTROLLED_ENVIRONMENTS.md) | Runtime profily, gateway config, self-host posture po Phase 4 closure |
| [Release Checklist](./RELEASE_CHECKLIST.md) | Checklist pre každý release |
| [Strategy Docs](./strategy/README.md) | Source of truth pre dlhodobú produktovú a architektonickú stratégiu |
| [Backlog Progress](./strategy/BACKLOG_PROGRESS.md) | Snapshot progresu proti stratégii a backlogu |
| [Backlog Review Against Masterplan](./strategy/BACKLOG_REVIEW_AGAINST_MASTERPLAN.md) | Gap analysis backlogu proti masterplanu |

## Rýchly štart

```bash
source .venv/bin/activate
python -m agent --setup-doctor  # Self-host config audit
python -m agent              # Spusti agenta
python -m agent --status     # Stav
python -m agent --health     # Zdravie
python -m agent --report     # Operator report / inbox
python -m agent --runtime-model   # Explicitný runtime model
python -m agent --llm-runtime-status
python -m agent --llm-runtime-disable --llm-runtime-note "maintenance"
python -m agent --llm-runtime-enable --llm-runtime-backend cli
python -m agent --llm-runtime-enable --llm-runtime-backend api --llm-runtime-provider anthropic
python -m agent --llm-runtime-follow-env --llm-runtime-enable
python -m agent --gateway-catalog
python -m agent --gateway-catalog --gateway-provider obolos.tech --gateway-capability review_handoff_v1 --gateway-export-mode client_safe
python -m agent --call-provider-api --provider-api-provider obolos.tech --provider-api-capability marketplace_catalog_v1
python -m agent --call-provider-api --provider-api-provider obolos.tech --provider-api-capability wallet_balance_v1
python -m agent --call-provider-api --provider-api-provider obolos.tech --provider-api-capability marketplace_api_call_v1 --provider-api-resource ocr-text-extraction --provider-api-method POST --provider-api-json '{"mode":"fast"}'
python -m agent --review-quality-eval --review-quality-release-label v1.35.0
python -m agent --release-readiness --release-readiness-release-label v1.35.0
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
python -m pytest tests/ -q   # 1762+ testov, offline, $0.00
```

## Verzia

Aktuálna: **v1.35.0** — tiered logging, vault crash-safety, runtime LLM control, security hardening.

Nové v `v1.35.0`:
- Vault single-file v2 format (`ALSv2` magic + embedded random salt + Fernet token), atomic
  `os.replace` writes, automatická migrácia z v1
- Tiered structured logging — long tier (~30 dní) pre lifecycle/build/finance/audit, short tier
  (~6 hodín) pre verbose pipeline diagnostics, hourly cron prune sweep
- Runtime LLM operator control — flip `cli` ↔ `api` backend per-session bez restartu cez
  dashboard alebo `POST /api/operator/llm`
- Telegram + Claude CLI fail-closed guard pre programming tasky v sandbox-only mode
- Headless CLI auto-approve (`AGENT_CLI_AUTO_APPROVE` env var, default detect TTY)
- mypy 147 → 0 errors naprieč 112 source files

Predchádzajúce v `v1.34.0`:
- setup doctor a silnejší self-host runtime posture report
- bezpečnejší default runtime data-dir mimo source tree pre fresh checkout
- konzistentné `AGENT_DATA_DIR` správanie naprieč CLI a operator surfaces

Predchádzajúce v `v1.33.0`:
- Docker-isolated build execution pre generated projekty
- auto-fix retry loop po failing testoch
- bohatší build reporting cez Telegram/API

Pozri [CHANGELOG.md](../CHANGELOG.md) pre kompletný zoznam zmien.
